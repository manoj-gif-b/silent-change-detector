"""Report builder -- assembles the final JSON with EXACTLY these fields:

    discovery          one sentence
    why_it_matters     3-5 bullets
    evidence           multiple independent sources with links
    timeline           dated events supporting the discovery
    numbers            only the needed calculations (already verified)
    counterargument    strongest reason the claim might be wrong
    falsification_condition  future evidence that would overturn it
    evidence_strength  Strong / Moderate / Weak + reasoning
    evidence_trail     finding -> doc -> page -> quote

Every value comes from the pipeline's own verified artefacts: the hypothesis
supplies discovery/why_it_matters/timeline/numbers, the red team supplies
counterargument/falsification/strength, and evidence is collected from the
detector records (each entry already carries source document, page and quote).
No new numbers are ever introduced here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scd import config

logger = logging.getLogger("scd.report")

# The public report schema -- order matters for human readability.
REPORT_FIELDS = [
    "discovery",
    "why_it_matters",
    "evidence",
    "timeline",
    "numbers",
    "counterargument",
    "falsification_condition",
    "evidence_strength",
    "evidence_trail",
]


def _source_link(evidence_record: dict) -> str | None:
    """Best-effort 'link' for an evidence record.

    Uses the document's on-disk path (the canonical locator for a locally held
    PDF); ``None`` when the file cannot be located.
    """
    doc = evidence_record.get("source_doc")
    if not doc:
        return None
    company = evidence_record.get("company")
    candidates: list[Path] = []
    if company:
        for base in (config.company_manual_dir(company), config.company_raw_dir(company)):
            candidates.extend(base.rglob(doc))
    if not candidates:
        candidates = list(config.DATA_DIR.rglob(doc))
    return str(candidates[0]) if candidates else None


def _collect_evidence(hypothesis: dict, redteam: dict, limit: int = 12) -> list[dict]:
    """Independent evidence sources: every distinct (doc, page, quote) record."""
    seen: set[tuple] = set()
    records: list[dict] = []

    raw = list(hypothesis.get("evidence") or [])
    counter_src = redteam.get("counterargument_source")
    if isinstance(counter_src, dict) and counter_src.get("source_doc"):
        raw = [counter_src] + raw  # include the counterargument's source first

    for ev in raw:
        if not isinstance(ev, dict):
            continue
        doc = ev.get("source_doc")
        if not doc:
            continue
        key = (doc, ev.get("page"), (ev.get("quote") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "source_doc": doc,
            "page": ev.get("page"),
            "period": ev.get("period"),
            "quote": (ev.get("quote") or "").strip() or None,
            "link": _source_link({**ev, "company": hypothesis.get("company")}),
        })
        if len(records) >= limit:
            break
    return records


def _build_evidence_trail(hypothesis: dict, evidence: list[dict]) -> list[dict]:
    """finding -> doc -> page -> quote chain for every evidence source."""
    finding = hypothesis.get("claim") or hypothesis.get("title") or "(no finding)"
    trail = []
    for ev in evidence:
        trail.append({
            "finding": finding,
            "document": ev.get("source_doc"),
            "page": ev.get("page"),
            "quote": ev.get("quote"),
            "period": ev.get("period"),
            "link": ev.get("link"),
        })
    return trail


def build_report(company: str, hypothesis: dict, redteam: dict) -> dict:
    """Assemble the final report dict (exact schema, exact field order)."""
    claim = hypothesis.get("claim") or hypothesis.get("title") or "(no discovery found)"
    evidence = _collect_evidence(hypothesis, redteam)

    strength = redteam.get("evidence_strength", "Weak")
    reasoning = redteam.get("strength_reasoning") or ""
    downgrade = redteam.get("downgrade_note") or ""
    survived = redteam.get("survived_checks")
    checks_total = len(redteam.get("checks") or [])
    strength_block = {
        "rating": strength,
        "reasoning": reasoning,
        "downgrade_note": downgrade or None,
        "checks_survived": f"{survived}/{checks_total}" if checks_total else None,
    }

    report = {
        "discovery": claim,
        "why_it_matters": hypothesis.get("why_it_matters") or [],
        "evidence": evidence,
        "timeline": hypothesis.get("timeline") or [],
        "numbers": hypothesis.get("numbers") or {},
        "counterargument": redteam.get("strongest_counterargument") or
                           "(no counterargument recorded)",
        "falsification_condition": redteam.get("falsification_condition") or
                                   "(none recorded)",
        "evidence_strength": strength_block,
        "evidence_trail": _build_evidence_trail(hypothesis, evidence),
    }
    # Guarantee exact field set/order.
    ordered = {field: report[field] for field in REPORT_FIELDS}
    ordered["_meta"] = {
        "company": company,
        "detector": hypothesis.get("detector"),
        "anomaly_score": hypothesis.get("adjusted_score"),
        "redteam_checks": redteam.get("checks") or [],
        "other_candidates": hypothesis.get("all_ranked") or [],
        "no_investment_advice": True,
    }
    return ordered


def save_report(report: dict, company: str) -> Path:
    """Write the report JSON to ``data/out/<company>/report.json``."""
    out_dir = config.company_out_dir(company)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Report saved -> %s", path)
    return path


def load_report(company: str) -> dict | None:
    """Load a previously-saved report, or ``None`` if absent."""
    path = config.company_out_dir(company) / "report.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - manual debug entrypoint
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    name = sys.argv[1] if len(sys.argv) > 1 else "Bharath Foods Limited"
    rep = load_report(name)
    if rep is None:
        print("No report found -- run: python run.py", name)
    else:
        print(json.dumps({k: rep[k] for k in REPORT_FIELDS}, ensure_ascii=False, indent=2))

