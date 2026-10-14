"""Milestone 5 -- run.py, the CLI entry point.

Runs the full pipeline for any NIFTY-50 consumer/FMCG company with clear
progress logs::

    python run.py "Hindustan Unilever Limited"
    python run.py --demo                      # offline demo (no API key needed)
    python run.py "Company Name" --auto       # also try URL downloads
    python run.py "Company Name" --max-chunks 20   # quick partial run

Stages (one log block each, timings included):
    1. ingest     find/parse PDFs -> metadata-tagged chunks
    2. ledger     LLM extracts KPIs -> disclosure ledger
    3. detectors  3 detectors -> scored anomalies
    4. hypothesis rank anomalies -> ONE testable claim + dated timeline
    5. redteam    try to DISPROVE the claim -> strength rating
    6. report     assemble final JSON -> data/out/<company>/report.json

The report is written to ``data/out/<company>/report.json``. No investment
advice is ever produced -- the output is a research finding with evidence.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from scd import config

logger = logging.getLogger("scd.run")

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def _stage_banner(n: int, total: int, name: str) -> None:
    logger.info("=" * 64)
    logger.info("STAGE %d/%d  %s", n, total, name)
    logger.info("=" * 64)


def run_pipeline(
    company: str,
    *,
    enable_auto: bool = False,
    auto_urls: list[str] | None = None,
    max_chunks: int | None = None,
    use_demo: bool = False,
) -> dict:
    """Run every pipeline stage; return the final report dict.

    ``use_demo=True`` generates the synthetic demo PDFs first and forces the
    offline scripted LLM (no API key or internet needed).
    """
    import ingest, ledger, detectors, hypothesis, redteam, report as report_mod

    total_stages = 6
    t_start = time.time()

    llm = None
    if use_demo:
        from scd import demo

        demo.generate_demo_pdfs(company)
        llm = demo.scripted_llm()
        logger.info("Offline demo mode: synthetic PDFs generated + scripted LLM wired.")

    # -- Stage 1: ingest ----------------------------------------------------
    _stage_banner(1, total_stages, "INGEST")
    t0 = time.time()
    manifest = ingest.ingest_company(
        company, enable_auto=enable_auto, auto_urls=auto_urls, chunk_chars=None
    )
    logger.info(
        "Ingest complete in %.1fs: %d document(s), %d chunk(s).",
        time.time() - t0, manifest["n_documents"], manifest["n_chunks"],
    )
    if manifest["n_chunks"] == 0:
        logger.error(
            "No chunks produced. Drop PDFs into %s and rerun "
            "(or use --demo to try the offline demo).",
            config.company_manual_dir(company),
        )
        raise SystemExit(2)

    # -- Stage 2: ledger ----------------------------------------------------
    _stage_banner(2, total_stages, "LEDGER (KPI extraction)")
    t0 = time.time()
    ledger_df = ledger.run(company, llm=llm, max_chunks=max_chunks)
    logger.info(
        "Ledger complete in %.1fs: %d KPI row(s), %d distinct metric(s).",
        time.time() - t0, len(ledger_df),
        ledger_df["name"].nunique() if not ledger_df.empty else 0,
    )
    if ledger_df.empty:
        logger.error(
            "The ledger is empty -- no KPIs were extracted. Check that the PDFs "
            "contain selectable text (not pure scans) or adjust --max-chunks."
        )
        raise SystemExit(3)

    # -- Stage 3: detectors -------------------------------------------------
    _stage_banner(3, total_stages, "DETECTORS")
    t0 = time.time()
    anomalies = detectors.run_all(ledger_df, company, llm=llm)
    logger.info("Detectors complete in %.1fs: %d anomal(y/ies).", time.time() - t0, len(anomalies))
    for a in anomalies[:5]:
        logger.info("  [%s | score %.2f] %s", a["detector"], a["score"], a["title"])

    # -- Stage 4: hypothesis ------------------------------------------------
    _stage_banner(4, total_stages, "HYPOTHESIS")
    t0 = time.time()
    hypothesis_dict = hypothesis.build_hypothesis(company, anomalies, llm=llm)
    logger.info("Hypothesis built in %.1fs.", time.time() - t0)
    if not hypothesis_dict.get("claim"):
        logger.error(
            "No testable claim could be produced (no anomalies or LLM failure). "
            "The pipeline stops here."
        )
        raise SystemExit(4)
    logger.info("Claim: %s", hypothesis_dict["claim"])

    # -- Stage 5: red team --------------------------------------------------
    _stage_banner(5, total_stages, "RED TEAM (disproof attempt)")
    t0 = time.time()
    verdict = redteam.run_redteam(company, hypothesis_dict, llm=llm)
    logger.info(
        "Red team complete in %.1fs: strength=%s (survived %d checks).",
        time.time() - t0, verdict.get("evidence_strength"), verdict.get("survived_checks", 0),
    )
    if verdict.get("downgrade_note"):
        logger.warning("Downgraded: %s", verdict["downgrade_note"])

    # -- Stage 6: report ----------------------------------------------------
    _stage_banner(6, total_stages, "REPORT")
    t0 = time.time()
    final_report = report_mod.build_report(company, hypothesis_dict, verdict)
    out_path = report_mod.save_report(final_report, company)
    logger.info("Report assembled and saved in %.1fs -> %s", time.time() - t0, out_path)

    total = time.time() - t_start
    logger.info("=" * 64)
    logger.info("PIPELINE FINISHED for '%s' in %.1fs.", company, total)
    logger.info("Report: %s", out_path)
    logger.info("View it in the dashboard:  streamlit run app.py")
    logger.info("(This is a research finding only -- not investment advice.)")
    logger.info("=" * 64)
    final_report["_meta"]["report_path"] = str(out_path)
    return final_report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Silent Change Detector -- autonomous financial research agent "
                    "(ATMOS 2026, Track C). Produces ONE evidence-backed, "
                    "non-obvious finding for a NIFTY-50 consumer/FMCG company.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  python run.py "Hindustan Unilever Limited"\n'
            "  python run.py --demo\n"
            '  python run.py "ITC Limited" --auto\n'
            '  python run.py "Nestle India" --max-chunks 15\n'
        ),
    )
    p.add_argument(
        "company",
        nargs="?",
        default=None,
        help="Full company name as it appears on the NSE (e.g. 'Hindustan Unilever Limited').",
    )
    p.add_argument(
        "--demo", action="store_true",
        help="Offline demo: generate synthetic PDFs for 'Bharath Foods Limited' and "
             "use the scripted LLM (no API key / internet needed).",
    )
    p.add_argument(
        "--auto", action="store_true",
        help="Also attempt best-effort URL downloads (unreliable; manual folder is preferred).",
    )
    p.add_argument(
        "--url", action="append", default=None, metavar="URL",
        help="Explicit public PDF URL to download (repeatable). Implies --auto.",
    )
    p.add_argument(
        "--max-chunks", type=int, default=None, metavar="N",
        help="Only process the first N chunks (quicker, useful for testing).",
    )
    p.add_argument(
        "--llm-model", default=None, metavar="MODEL",
        help="Override the Gemini model (default: GEMINI_MODEL env or gemini-2.5-flash).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Debug-level logging.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    company = args.company
    if args.demo and not company:
        from scd.demo import DEMO_COMPANY
        company = DEMO_COMPANY
    if not company:
        logger.error('No company given. Usage: python run.py "Company Name"  (or --demo).')
        return 1

    if args.llm_model:
        import os
        os.environ["GEMINI_MODEL"] = args.llm_model

    enable_auto = args.auto or bool(args.url)

    try:
        run_pipeline(
            company,
            enable_auto=enable_auto,
            auto_urls=args.url,
            max_chunks=args.max_chunks,
            use_demo=args.demo,
        )
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except KeyboardInterrupt:
        logger.error("Interrupted by user.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

