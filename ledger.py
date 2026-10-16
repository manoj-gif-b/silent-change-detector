"""Milestone 1b -- ledger.

Responsibility
--------------
Turn ingested chunks into a structured **disclosure ledger**: one row per
(company, period, KPI) with the reported value, unit, the company's own
definition/wording, and a full provenance record (source document, page,
verbatim quote).

The LLM is used ONLY to read text and copy out figures verbatim (see
``scd.prompts.KPI_EXTRACT_PROMPT``). No arithmetic happens here -- numbers are
stored as-reported. Downstream detectors compute trends, margins etc. from this
table using :mod:`scd.arithmetic`.

Output (per company) under ``data/ledger/<company>/``:
* ``ledger.csv``  -- flat table, one row per extracted KPI occurrence
* ``ledger.json`` -- same data as a list of dicts (richer, keeps all fields)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

from scd import config, prompts, selection
from scd.llm import get_llm
from ingest import load_chunks

logger = logging.getLogger("scd.ledger")

# The canonical columns of a ledger row. ``period`` is what the company printed;
# the detectors normalise it later when computing trends.
LEDGER_COLUMNS = [
    "company",
    "period",
    "name",
    "value",
    "unit",
    "definition",
    "doc_type",
    "source_doc",
    "page",
    "quote",
]


def _chunk_header(chunk: dict) -> str:
    """Build the small provenance header prepended to each chunk for the LLM."""
    return (
        f"[company: {chunk.get('company')}] "
        f"[doc_type: {chunk.get('doc_type')}] "
        f"[period: {chunk.get('period')}] "
        f"[source_doc: {chunk.get('source_doc')}] "
        f"[page: {chunk.get('page')}]"
    )


def _clean_kpi_record(raw: dict, chunk: dict, company: str) -> dict | None:
    """Validate/normalise one LLM-extracted KPI record against its chunk."""
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    value = raw.get("value")
    if not name or value in (None, ""):
        return None
    try:
        page = int(raw.get("page"))
    except (TypeError, ValueError):
        page = chunk.get("page")
    return {
        "company": company,
        "period": (raw.get("period") or chunk.get("period") or "unknown"),
        "name": name,
        "value": str(value).strip(),
        "unit": raw.get("unit"),
        "definition": raw.get("definition"),
        "doc_type": chunk.get("doc_type"),
        "source_doc": chunk.get("source_doc"),
        "page": page,
        "quote": (raw.get("quote") or "").strip(),
    }


def _kpi_prompt(chunk: dict) -> str:
    """The exact extraction prompt for one chunk.

    Shared by the cache pre-check and the actual call so the cache key always
    matches (an interrupted run resumes without re-issuing finished calls).
    """
    context = f"{_chunk_header(chunk)}\n\n{chunk.get('text', '')}"
    return prompts.KPI_EXTRACT_PROMPT.format(context=context)


def extract_kpis_from_chunk(chunk: dict, llm) -> list[dict]:
    """Extract every recurring KPI from one chunk via the LLM (cached)."""
    company = chunk.get("company", "")
    prompt = _kpi_prompt(chunk)
    try:
        records = llm.complete_json(prompt, system=prompts.KPI_EXTRACT_SYSTEM, expected=list)
    except Exception as exc:
        logger.warning("KPI extraction failed for %s p%s: %s",
                       chunk.get("source_doc"), chunk.get("page"), exc)
        return []
    cleaned = []
    for raw in records:
        rec = _clean_kpi_record(raw, chunk, company)
        if rec is not None:
            cleaned.append(rec)
    return cleaned


def build_ledger(
    company: str,
    *,
    llm=None,
    max_chunks: int | None = None,
    relevance_filter: bool | None = None,
) -> pd.DataFrame:
    """Extract KPIs from ``company``'s chunks into a ledger DataFrame.

    The full page index (every page of every PDF) is preserved on disk by
    ingest; here we pre-select the chunks most likely to contain KPIs (MD&A,
    segment information, financial highlights, shareholder letter, trade
    receivables and revenue notes) and send only those to the LLM.
    Boilerplate (cover, contents, directors' report formalities, long legal
    sections) is skipped. Caching happens per-chunk inside the LLM client
    (keyed by the exact prompt), so an interrupted run resumes without
    repeating any call.
    """
    llm = llm or get_llm()
    chunks = load_chunks(company)
    if not chunks:
        logger.warning("No chunks for %s -- run ingest first.", company)
        return pd.DataFrame(columns=LEDGER_COLUMNS)

    # -- Pre-select KPI-likely chunks (deterministic; page index untouched) --
    if relevance_filter is None:
        relevance_filter = config.LEDGER_RELEVANCE_FILTER
    chunks, sel_stats = selection.select_kpi_chunks(chunks, enabled=relevance_filter)
    logger.info("Chunk selection: %s", selection.format_stats(sel_stats))

    if max_chunks is not None and len(chunks) > max_chunks:
        logger.info(
            "--max-chunks %d: processing the first %d selected chunk(s).",
            max_chunks, max_chunks,
        )
        chunks = chunks[:max_chunks]

    # -- Resume report: how many calls are already cached on disk ------------
    cached = 0
    for chunk in chunks:
        try:
            if llm.is_cached(_kpi_prompt(chunk), system=prompts.KPI_EXTRACT_SYSTEM, kind="json"):
                cached += 1
        except Exception:
            # is_cached is an optimisation; never fail the run over it.
            break
    if cached:
        logger.info(
            "Resume: %d of %d chunk extraction(s) already cached; %d call(s) to make.",
            cached, len(chunks), len(chunks) - cached,
        )

    # -- Extract, with per-chunk progress (doc X of N, chunk Y of M, elapsed)
    doc_order: list[str] = []
    for chunk in chunks:
        doc = chunk.get("source_doc") or "unknown"
        if doc not in doc_order:
            doc_order.append(doc)
    doc_index = {d: i + 1 for i, d in enumerate(doc_order)}

    rows: list[dict] = []
    total = len(chunks)
    t0 = time.time()
    for i, chunk in enumerate(chunks, start=1):
        rows.extend(extract_kpis_from_chunk(chunk, llm))
        logger.info(
            "document %d of %d (%s), chunk %d of %d, %d KPI row(s), elapsed %.1fs",
            doc_index.get(chunk.get("source_doc") or "unknown", 0), len(doc_order),
            chunk.get("source_doc"), i, total, len(rows), time.time() - t0,
        )

    df = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    return df


def save_ledger(df: pd.DataFrame, company: str) -> dict:
    """Persist the ledger for ``company``; returns output paths."""
    out_dir = config.company_ledger_dir(company)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ledger.csv"
    json_path = out_dir / "ledger.json"
    df.to_csv(csv_path, index=False)
    json_path.write_text(
        json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved ledger (%d rows) -> %s", len(df), csv_path)
    return {"csv": str(csv_path), "json": str(json_path), "rows": len(df)}


def load_ledger(company: str) -> pd.DataFrame:
    """Load a previously-saved ledger CSV for ``company`` (empty if none)."""
    csv_path = config.company_ledger_dir(company) / "ledger.csv"
    if not csv_path.exists():
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    return pd.read_csv(csv_path, dtype={"value": "object", "page": "Int64"})


def run(
    company: str,
    *,
    llm=None,
    max_chunks: int | None = None,
    save: bool = True,
    relevance_filter: bool | None = None,
) -> pd.DataFrame:
    """Convenience end-to-end: build (and optionally save) the ledger."""
    df = build_ledger(
        company, llm=llm, max_chunks=max_chunks, relevance_filter=relevance_filter,
    )
    if save:
        save_ledger(df, company)
    return df


if __name__ == "__main__":  # pragma: no cover - manual debug entrypoint
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    name = sys.argv[1] if len(sys.argv) > 1 else "TestCo Ltd"
    frame = run(name)
    print(frame.head(20).to_string())
