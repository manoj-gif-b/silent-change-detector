"""Milestone 3a -- hypothesis.

Responsibility
--------------
From the pool of scored anomalies produced by ``detectors.py``:

1. Rank them (detector score first, with a small recency/consistency bonus
   computed in pure Python -- never by the LLM).
2. Turn the single strongest anomaly into ONE testable claim, phrased by the
   LLM from *already computed* numbers and quotes (``scd.prompts``).
3. Build a dated timeline for the claim: each period in the anomaly mapped to
   its real-world calendar date using Indian fiscal-year conventions
   (FY ends 31 March; Q1 ends 30 June, Q2 30 Sep, Q3 31 Dec, Q4 31 Mar).

Output: a hypothesis dict consumed by ``redteam.py`` and the report builder.
"""

from __future__ import annotations

import logging
from datetime import date

from scd import prompts
from scd.llm import get_llm
from scd.periods import parse_period

logger = logging.getLogger("scd.hypothesis")

# Bonus added to a detector score when the anomaly involves the most recent
# periods available (a change happening *now* matters more than an old one).
_RECENCY_BONUS = 0.05
# Bonus when the same detector produced multiple consistent anomalies.
_CONSISTENCY_BONUS = 0.03


# ---------------------------------------------------------------------------
# Ranking (pure arithmetic, no LLM)
# ---------------------------------------------------------------------------
def rank_anomalies(anomalies: list[dict]) -> list[dict]:
    """Return anomalies sorted by adjusted score (desc), each annotated with
    ``adjusted_score`` and ``rank``.

    Adjusted score = detector score
                   + recency bonus  (anomaly touches the newest period seen)
                   + consistency bonus (same detector fires more than once)
    All bonuses are capped so the adjusted score stays within [0, 1].
    """
    if not anomalies:
        return []

    # Newest period across all anomalies (by fiscal sort key).
    newest_key = -1
    for a in anomalies:
        for point in a.get("timeline", []):
            p = parse_period(point.get("period"))
            if p and p.sort_key > newest_key:
                newest_key = p.sort_key

    detector_counts: dict[str, int] = {}
    for a in anomalies:
        detector_counts[a["detector"]] = detector_counts.get(a["detector"], 0) + 1

    ranked: list[dict] = []
    for a in anomalies:
        score = float(a.get("score", 0.0))
        touches_newest = False
        for point in a.get("timeline", []):
            p = parse_period(point.get("period"))
            if p and p.sort_key == newest_key:
                touches_newest = True
                break
        adjusted = score
        if touches_newest:
            adjusted += _RECENCY_BONUS
        if detector_counts.get(a["detector"], 0) > 1:
            adjusted += _CONSISTENCY_BONUS
        a = dict(a)
        a["adjusted_score"] = round(min(adjusted, 1.0), 4)
        ranked.append(a)

    ranked.sort(key=lambda x: x["adjusted_score"], reverse=True)
    for i, a in enumerate(ranked, start=1):
        a["rank"] = i
    return ranked


# ---------------------------------------------------------------------------
# Dated timeline (pure date arithmetic, no LLM)
# ---------------------------------------------------------------------------
def _period_end_date(period_label: str) -> str | None:
    """Return the calendar date a period *ends* on (ISO ``YYYY-MM-DD``).

    Indian fiscal year ends 31 March. Quarter end dates:
    Q1 -> 30 Jun of (fy-1), Q2 -> 30 Sep (fy-1), Q3 -> 31 Dec (fy-1),
    Q4 -> 31 Mar of fy. Annual (quarter 5) -> 31 Mar of fy.
    """
    p = parse_period(period_label)
    if p is None:
        return None
    if p.quarter == 5 or not p.is_quarter:
        return date(p.fy, 3, 31).isoformat()
    end_month_day = {1: (p.fy - 1, 6, 30), 2: (p.fy - 1, 9, 30),
                     3: (p.fy - 1, 12, 31), 4: (p.fy, 3, 31)}
    y, m, d = end_month_day[p.quarter]
    return date(y, m, d).isoformat()


def build_timeline(anomaly: dict) -> list[dict]:
    """Convert an anomaly's timeline points into dated entries.

    Each entry: {period, date, metric, value[, company, peer_mean]}.
    Unparseable periods get ``date: null`` but are kept (order preserved).
    """
    from scd.periods import sort_periods

    points = anomaly.get("timeline", [])
    labels = [pt.get("period") for pt in points if pt.get("period")]
    ordered = sort_periods(labels)
    by_label = {pt.get("period"): pt for pt in points}

    dated: list[dict] = []
    for label in ordered:
        pt = by_label.get(label, {})
        entry = {"period": label, "date": _period_end_date(label)}
        for key, val in pt.items():
            if key != "period":
                entry[key] = val
        dated.append(entry)
    return dated


# ---------------------------------------------------------------------------
# Claim phrasing (LLM phrases; numbers are never computed here)
# ---------------------------------------------------------------------------
def _format_numbers(anomaly: dict) -> str:
    """Compact, verbatim rendering of the anomaly's computed numbers."""
    lines = []
    for key, val in (anomaly.get("numbers") or {}).items():
        lines.append(f"- {key}: {val}")
    return "\n".join(lines) or "- (none)"


def _format_quotes(anomaly: dict, limit: int = 6) -> str:
    """Render supporting quotes with their sources for the prompt."""
    lines = []
    for ev in (anomaly.get("evidence") or [])[:limit]:
        src = ev.get("source_doc", "?")
        page = ev.get("page", "?")
        quote = (ev.get("quote") or "").strip()
        lines.append(f"- [{src}, p.{page}] {quote}")
    return "\n".join(lines) or "- (none)"


def build_hypothesis(company: str, anomalies: list[dict], *, llm=None) -> dict:
    """Rank anomalies, pick the top one, and phrase it as a testable claim.

    Returns a dict:
      {
        "company": ...,
        "detector": ..., "title": ..., "score": ..., "rank": 1,
        "claim": "...",                       # one sentence, LLM-phrased
        "why_it_matters": ["...", ...],       # 3-5 bullets
        "testable_prediction": "...",
        "numbers": {...},                     # the verified computed figures
        "timeline": [{period, date, ...}],    # dated timeline
        "evidence": [...],                    # doc/page/quote records
        "source_anomaly": {...},              # full detector record
      }
    """
    llm = llm or get_llm()
    ranked = rank_anomalies(anomalies)
    if not ranked:
        logger.warning("No anomalies for %s; no hypothesis can be built.", company)
        return {"company": company, "claim": None,
                "why_it_matters": [], "testable_prediction": None,
                "numbers": {}, "timeline": [], "evidence": []}

    top = ranked[0]
    logger.info("Top anomaly: rank=1 detector=%s adjusted_score=%.3f -- %s",
                top["detector"], top["adjusted_score"], top["title"])

    prompt = prompts.HYPOTHESIS_PROMPT.format(
        company=company,
        detector=top["detector"],
        numbers=_format_numbers(top),
        quotes=_format_quotes(top),
        timeline=", ".join(pt.get("period", "?") for pt in top.get("timeline", [])),
    )
    try:
        phrasing = llm.complete_json(prompt, system=prompts.HYPOTHESIS_SYSTEM, expected=dict)
    except Exception as exc:
        logger.error("Hypothesis phrasing failed (%s); falling back to detector title.", exc)
        phrasing = {}

    claim = (phrasing.get("claim") or "").strip() or top.get("title", "")
    why = [str(b).strip() for b in (phrasing.get("why_it_matters") or []) if str(b).strip()]
    prediction = (phrasing.get("testable_prediction") or "").strip()

    return {
        "company": company,
        "detector": top["detector"],
        "title": top["title"],
        "score": top.get("score"),
        "adjusted_score": top.get("adjusted_score"),
        "rank": 1,
        "claim": claim,
        "why_it_matters": why[:5],
        "testable_prediction": prediction,
        "numbers": top.get("numbers", {}),
        "timeline": build_timeline(top),
        "evidence": top.get("evidence", []),
        "metrics": top.get("metrics", {}),
        "all_ranked": [
            {"rank": a["rank"], "detector": a["detector"], "title": a["title"],
             "adjusted_score": a["adjusted_score"]}
            for a in ranked[:10]
        ],
        "source_anomaly": top,
    }


if __name__ == "__main__":  # pragma: no cover - manual debug entrypoint
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from detectors import run_all
    from ledger import load_ledger

    name = sys.argv[1] if len(sys.argv) > 1 else "Bharath Foods Limited"
    anoms = run_all(load_ledger(name), name)
    result = build_hypothesis(name, anoms)
    print(json.dumps({k: v for k, v in result.items() if k != "source_anomaly"},
                     indent=2, ensure_ascii=False))

