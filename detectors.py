"""Milestone 2 -- detectors.

Three independent detectors scan the disclosure ledger and the ingested
documents and emit *scored anomaly records*. Each record is a plain dict so it
can be ranked by ``hypothesis.py`` and rendered by the dashboard.

All quantitative comparisons use :mod:`scd.arithmetic`; the LLM is used only to
(i) tag narrative emphasis in transcripts and (ii) identify peers from the
documents. Nothing here computes a number by asking the model to "do maths".

Anomaly record schema (common fields):
    {
      "detector": "vanished_or_redefined" | "narrative_vs_numbers" | "peer_divergence",
      "company": str,
      "score": float,              # 0..1, higher = more anomalous
      "title": str,                # one-line human summary
      "summary": str,              # 1-3 sentence explanation
      "numbers": {...},            # the computed figures that support the score
      "timeline": [ {period, ...} ],   # chronological evidence points
      "evidence": [ {source_doc, page, quote, period} ... ],
      "metrics": {...}             # optional chart-ready series for the dashboard
    }
"""

from __future__ import annotations

import json
import logging
import math
from typing import Iterable

import pandas as pd

from scd import arithmetic as ar
from scd import config, prompts
from scd.llm import get_llm
from scd.periods import parse_period, sort_periods
from ingest import load_chunks
from ledger import load_ledger

logger = logging.getLogger("scd.detectors")

# Emphasis ranking used when scoring narrative_vs_numbers.
_EMPHASIS = {"absent": 0.0, "low": 0.25, "medium": 0.6, "high": 1.0}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _num(value) -> float:
    """Coerce a ledger value cell to float via the shared arithmetic helper."""
    return ar._to_float(value)


def _series_for(ledger: pd.DataFrame, name_substr: str) -> pd.DataFrame:
    """Rows whose KPI name contains ``name_substr`` (case-insensitive)."""
    if ledger.empty:
        return ledger
    mask = ledger["name"].astype(str).str.lower().str.contains(name_substr.lower(), na=False)
    return ledger[mask].copy()


def _period_sorted_values(rows: pd.DataFrame) -> list[tuple[str, float]]:
    """Return ``[(period_label, value)]`` sorted chronologically, deduped.

    When a metric is reported more than once for the same period (e.g. in both
    an annual report and a quarterly release) the LAST occurrence in the sorted
    order wins, matching "most recently filed" intuition well enough here.
    """
    if rows.empty:
        return []
    parsed = []
    for _, r in rows.iterrows():
        p = parse_period(r.get("period"))
        if p is None:
            continue
        v = _num(r.get("value"))
        if math.isnan(v):
            continue
        parsed.append((p.sort_key, p.label, v))
    if not parsed:
        return []
    parsed.sort(key=lambda t: t[0])
    out: dict[str, float] = {}
    for _, label, v in parsed:
        out[label] = v
    return [(lbl, out[lbl]) for lbl in sort_periods(out.keys())]


def _evidence_from_rows(rows: pd.DataFrame, limit: int = 6) -> list[dict]:
    """Turn ledger rows into evidence dicts (doc/page/quote/period)."""
    ev = []
    for _, r in rows.head(limit).iterrows():
        ev.append(
            {
                "source_doc": r.get("source_doc"),
                "page": _safe_int(r.get("page")),
                "quote": r.get("quote"),
                "period": r.get("period"),
            }
        )
    return ev


def _safe_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _norm_text(v) -> str:
    """Normalise a ledger text field (definition/unit/wording) for comparison.

    Ledger cells are not guaranteed to be strings: ``pd.read_csv`` turns empty
    cells into float ``NaN`` (e.g. the Britannia Headcount row's ``unit``), and
    the LLM may emit plain numbers. ``float('nan')`` is truthy, so the old
    ``(v or '').strip()`` idiom crashed with ``'float' object has no attribute
    'strip'``. Here None/NaN become ``''`` (missing) and everything else is
    stringified, then stripped+lowered exactly as before -- so valid text
    compares identically to the original code.
    """
    if v is None or pd.isna(v):
        return ""
    return str(v).strip().lower()


# ---------------------------------------------------------------------------
# Detector 1: vanished_or_redefined
# ---------------------------------------------------------------------------
def detect_vanished_or_redefined(ledger: pd.DataFrame, company: str) -> list[dict]:
    """Flag KPIs that disappear from disclosure or change definition/wording.

    Two sub-signals per KPI name:
    * **vanished** -- reported in some periods, then absent in the most recent
      periods even though the company filed in those periods (i.e. other KPIs
      exist for those periods). Stronger the more consecutive trailing periods
      it is missing and the longer it had been reported.
    * **redefined** -- the ``definition``/``unit`` wording for the same KPI
      changes between consecutive periods.
    """
    if ledger.empty:
        return []

    anomalies: list[dict] = []
    # Build sort-key index of all parseable periods; skip unparseable with a warning.
    # Two labels with the same sort_key (e.g. "2025-26" and "FY26") count as the
    # same period. Periods that cannot be parsed are skipped entirely.
    all_periods: list[str] = []
    label_to_key: dict[str, int] = {}
    for p in sort_periods(ledger["period"].dropna().unique().tolist()):
        pp = parse_period(p)
        if pp is None:
            logger.warning("Skipping unparseable period %r", p)
            continue
        all_periods.append(p)
        label_to_key[p] = pp.sort_key
    latest_period = all_periods[-1] if all_periods else None

    for name, grp in ledger.groupby("name"):
        grp = grp.copy()
        reported_periods: list[str] = []
        reported_keys: set[int] = set()
        for p in sort_periods(grp["period"].dropna().unique().tolist()):
            pp = parse_period(p)
            if pp is None:
                logger.warning("Skipping unparseable period %r", p)
                continue
            reported_periods.append(p)
            reported_keys.add(pp.sort_key)
        if not reported_periods:
            continue

        # --- vanished signal ---
        missing_after = [p for p in all_periods if label_to_key[p] not in reported_keys]
        # Only count trailing periods where the company WAS active.
        trailing_missing = []
        for p in reversed(all_periods):
            if label_to_key[p] in reported_keys:
                break
            trailing_missing.append(p)
        trailing_missing.reverse()

        vanish_strength = 0.0
        if trailing_missing and len(reported_periods) >= 2:
            # Scales with how many trailing periods it vanished for.
            vanish_strength = ar.clamp(len(trailing_missing) / 4.0, 0.0, 1.0)

        # --- redefined signal ---
        redef = _detect_redefinition(grp)

        score = ar.weighted_score(
            {"vanished": vanish_strength, "redefined": redef["strength"]},
            {"vanished": 0.6, "redefined": 0.4},
        )
        if math.isnan(score) or score <= 0:
            continue

        last_reported = reported_periods[-1]
        first_missing = trailing_missing[0] if trailing_missing else None

        numbers = {
            "kpi": name,
            "periods_reported": len(reported_periods),
            "last_reported_period": last_reported,
            "first_missing_period": first_missing,
            "trailing_missing_periods": len(trailing_missing),
            "redefined": redef["changed"],
        }

        title_bits = [f"'{name}'"]
        if trailing_missing:
            title_bits.append(f"last reported {last_reported}, absent since {first_missing}")
        if redef["changed"]:
            title_bits.append(f"definition/wording changed ({redef['detail']})")
        title = "Disclosure change: " + "; ".join(title_bits)

        # Evidence: the last reported occurrence + (if any) a redefinition pair.
        last_rows = grp[grp["period"] == last_reported]
        evidence = _evidence_from_rows(last_rows, limit=2)
        if redef["changed"] and redef["rows"]:
            evidence += _evidence_from_rows(redef["rows"], limit=4)

        anomalies.append(
            {
                "detector": "vanished_or_redefined",
                "company": company,
                "score": round(float(score), 4),
                "title": title,
                "summary": (
                    f"The KPI '{name}' was reported for {len(reported_periods)} periods up to "
                    f"{last_reported}"
                    + (f", then disappeared for {len(trailing_missing)} period(s)." if trailing_missing else ".")
                    + (" Its wording/unit also changed over time." if redef["changed"] else "")
                ),
                "numbers": numbers,
                "timeline": _timeline_from_rows(grp, name),
                "evidence": evidence,
                "metrics": {
                    "kpi": name,
                    "reported_periods": reported_periods,
                    "missing_after": missing_after,
                },
            }
        )

    anomalies.sort(key=lambda a: a["score"], reverse=True)
    return anomalies


def _detect_redefinition(grp: pd.DataFrame) -> dict:
    """Detect a change in definition/unit wording across consecutive periods."""
    if grp.empty:
        return {"changed": False, "strength": 0.0, "detail": "", "rows": grp}

    grp_sorted = grp.copy()
    grp_sorted["_key"] = grp_sorted["period"].map(
        lambda p: parse_period(p).sort_key if parse_period(p) else 10**9
    )
    grp_sorted = grp_sorted.sort_values("_key")

    defs = []
    for _, r in grp_sorted.iterrows():
        # definition/unit may be NaN (empty CSV cell) or numeric, so they go
        # through _norm_text instead of a bare (v or '').strip().
        d = _norm_text(r.get("definition"))
        u = _norm_text(r.get("unit"))
        defs.append((d, u))

    changed = False
    detail = ""
    change_rows = []
    for i in range(1, len(defs)):
        prev_d, prev_u = defs[i - 1]
        cur_d, cur_u = defs[i]
        d_changed = prev_d and cur_d and prev_d != cur_d
        u_changed = prev_u and cur_u and prev_u != cur_u
        if d_changed or u_changed:
            changed = True
            detail = "unit" if u_changed and not d_changed else "definition"
            change_rows = pd.concat(
                [grp_sorted.iloc[[i - 1]], grp_sorted.iloc[[i]]]
            )
            break

    return {"changed": changed, "strength": 1.0 if changed else 0.0, "detail": detail, "rows": change_rows}


def _timeline_from_rows(grp: pd.DataFrame, name: str) -> list[dict]:
    """Build a chronological timeline of (period -> value) for one KPI."""
    pts = _period_sorted_values(grp)
    return [{"period": p, "value": v, "metric": name} for p, v in pts]


# ---------------------------------------------------------------------------
# Detector 2: narrative_vs_numbers
# ---------------------------------------------------------------------------
def _transcript_chunks_by_period(company: str) -> dict[str, list[dict]]:
    """Group transcript chunks by their period label."""
    chunks = load_chunks(company)
    out: dict[str, list[dict]] = {}
    for c in chunks:
        if c.get("doc_type") == "transcript" and c.get("period"):
            out.setdefault(c["period"], []).append(c)
    return out


def _tag_transcript_emphasis(company: str, period: str, chunks: list[dict], llm) -> dict[str, dict]:
    """Ask the LLM to tag topic emphasis for one transcript period (cached)."""
    context = "\n\n".join(
        f"[page {c.get('page')} | source: {c.get('source_doc')}]\n{c.get('text','')}"
        for c in chunks
    )[:8000]
    prompt = prompts.NARRATIVE_PROMPT.format(themes=", ".join(config.NARRATIVE_TOPICS), period=period, context=context)
    try:
        records = llm.complete_json(prompt, system=prompts.NARRATIVE_SYSTEM, expected=list)
    except Exception as exc:
        logger.warning("Narrative tagging failed for %s %s: %s", company, period, exc)
        return {}
    result: dict[str, dict] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        topic = (rec.get("topic") or "").strip().lower()
        if topic:
            result[topic] = {
                "emphasis": (rec.get("emphasis") or "absent").lower(),
                "quote": rec.get("quote"),
            }
    return result


def _metric_trend(values: list[tuple[str, float]]) -> dict:
    """Summarise a metric's recent direction: slope, last value, recent avg."""
    if len(values) < 2:
        return {"slope": math.nan, "last": values[-1][1] if values else math.nan, "declining": False}
    ys = [v for _, v in values]
    slope = ar.linear_slope(ys)
    recent = ys[-3:] if len(ys) >= 3 else ys
    recent_avg = ar.mean(recent)
    return {
        "slope": slope,
        "last": ys[-1],
        "recent_avg": recent_avg,
        "declining": (not math.isnan(slope) and slope < 0),
        "recent_decline": (not math.isnan(recent_avg) and recent_avg < 0),
    }


# Map narrative topics to the KPI-name substrings they should be checked against.
_TOPIC_TO_METRIC = {
    "volume": ["volume"],
    "pricing": ["price", "realis", "realiz", "asp"],
    "premium": ["premium", "realis", "realiz"],
    "rural": ["rural"],
    "urban": ["urban"],
    "receivables": ["receivable"],
    "inventory": ["inventory"],
    "gross margin": ["gross margin", "gross profit"],
    "market share": ["market share"],
}


def _match_metric_rows(ledger: pd.DataFrame, keys: Iterable[str]) -> pd.DataFrame:
    """Rows whose KPI name contains ANY of the given substrings."""
    if ledger.empty:
        return ledger
    mask = ledger["name"].astype(str).str.lower().str.contains("|".join(keys), na=False)
    return ledger[mask].copy()


def detect_narrative_vs_numbers(ledger: pd.DataFrame, company: str, *, llm=None) -> list[dict]:
    """Flag periods where narrative emphasis contradicts the metric trend.

    Classic case: management emphasises "volume" strongly on the call while the
    volume metric is flat/falling (or has stopped being reported). We score the
    emphasis of a topic against the direction of its matching metric.
    """
    llm = llm or get_llm()
    if ledger.empty:
        return []

    transcripts = _transcript_chunks_by_period(company)
    if not transcripts:
        logger.info("No transcripts found for %s; narrative detector has nothing to score.", company)
        return []

    anomalies: list[dict] = []
    for period, chunks in transcripts.items():
        emphasis = _tag_transcript_emphasis(company, period, chunks, llm)
        if not emphasis:
            continue

        for topic, tag in emphasis.items():
            emph = tag.get("emphasis", "absent")
            emph_score = _EMPHASIS.get(emph, 0.0)
            if emph_score < 0.6:  # only care about topics the company LEANS on
                continue

            metric_keys = _TOPIC_TO_METRIC.get(topic)
            if not metric_keys:
                continue

            # Gather the matching metric's full history.
            matched_rows = _match_metric_rows(ledger, metric_keys)
            if matched_rows.empty:
                continue
            series = _period_sorted_values(matched_rows)
            if len(series) < 2:
                continue
            trend = _metric_trend(series)

            # Contradiction: strong emphasis on a topic whose metric is weak.
            contradiction = 0.0
            if trend["declining"]:
                contradiction = ar.clamp(abs(trend["slope"]) / 5.0, 0.0, 1.0)
            elif trend.get("recent_decline"):
                contradiction = ar.clamp(abs(trend["recent_avg"]) / 5.0, 0.0, 1.0)

            if contradiction <= 0:
                continue

            score = ar.weighted_score(
                {"emphasis": emph_score, "contradiction": contradiction},
                {"emphasis": 0.4, "contradiction": 0.6},
            )
            if math.isnan(score) or score <= 0:
                continue

            metric_name = matched_rows["name"].iloc[0]
            anomalies.append(
                {
                    "detector": "narrative_vs_numbers",
                    "company": company,
                    "score": round(float(score), 4),
                    "title": f"Narrative says '{topic}' ({emph}) but {metric_name} is declining in {period}",
                    "summary": (
                        f"On the {period} call the company emphasised '{topic}' ({emph} emphasis), "
                        f"yet the reported '{metric_name}' trend is negative "
                        f"(slope {trend['slope']:.2f}/period, latest {trend['last']:.2f})."
                    ),
                    "numbers": {
                        "topic": topic,
                        "emphasis": emph,
                        "metric": metric_name,
                        "metric_slope": round(trend["slope"], 4),
                        "metric_last": round(trend["last"], 4),
                        "metric_recent_avg": round(trend.get("recent_avg", math.nan), 4),
                        "period": period,
                    },
                    "timeline": [{"period": p, "value": v, "metric": metric_name} for p, v in series],
                    "evidence": [
                        {
                            "source_doc": chunks[0].get("source_doc"),
                            "page": chunks[0].get("page"),
                            "quote": tag.get("quote"),
                            "period": period,
                        }
                    ] + _evidence_from_rows(matched_rows.tail(2)),
                    "metrics": {
                        "topic": topic,
                        "metric": metric_name,
                        "series": [{"period": p, "value": v} for p, v in series],
                    },
                }
            )

    anomalies.sort(key=lambda a: a["score"], reverse=True)
    return anomalies


# ---------------------------------------------------------------------------
# Detector 3: peer_divergence
# ---------------------------------------------------------------------------
# The peer metrics we compare. Each maps to KPI-name substrings in the ledger.
_PEER_METRICS = {
    "volume_growth": ["volume growth", "volume"],
    "revenue_growth": ["revenue growth", "revenue from operations", "net sales"],
    "receivable_days": ["receivable"],
    "gross_margin": ["gross margin", "gross profit"],
}


def _identify_peers(company: str, ledger: pd.DataFrame, llm, max_peers: int = 4) -> list[str]:
    """Ask the LLM to name comparable listed peers, grounded in the documents."""
    chunks = load_chunks(company)
    sample = "\n\n".join(
        f"[{c.get('source_doc')} p{c.get('page')}] {c.get('text','')[:600]}" for c in chunks[:15]
    )[:8000]
    if not sample:
        # Fall back to ledger KPI names if no chunks are available.
        sample = "KPIs reported: " + ", ".join(sorted(set(ledger["name"].astype(str))))[:2000]
    prompt = prompts.PEER_PROMPT.format(company=company, context=sample)
    try:
        records = llm.complete_json(prompt, system=prompts.PEER_SYSTEM, expected=list)
    except Exception as exc:
        logger.warning("Peer identification failed for %s: %s", company, exc)
        return []
    peers: list[str] = []
    for rec in records[:max_peers]:
        name = (rec.get("peer") or "").strip() if isinstance(rec, dict) else ""
        if name and name.lower() != company.lower():
            peers.append(name)
    return peers


def _peer_metric_series(peer_ledger: pd.DataFrame, metric_keys: list[str]) -> list[tuple[str, float]]:
    rows = _match_metric_rows(peer_ledger, metric_keys)
    return _period_sorted_values(rows)


def _ensure_peer_ledger(peer: str, llm) -> pd.DataFrame | None:
    """Return a peer's ledger, building it from PDFs only if not already cached."""
    existing = load_ledger(peer)
    if not existing.empty:
        return existing
    # Only attempt to build if the peer has manual PDFs (no network crawling).
    from ingest import gather_manual_pdfs, ingest_company
    import ledger as ledger_mod

    if not gather_manual_pdfs(peer):
        return None
    logger.info("Building ledger for peer %s from manual PDFs...", peer)
    manifest = ingest_company(peer, enable_auto=False)
    if manifest.get("n_chunks", 0) == 0:
        return None
    return ledger_mod.run(peer, llm=llm, save=True)


def detect_peer_divergence(ledger: pd.DataFrame, company: str, *, llm=None, max_peers: int = 4) -> list[dict]:
    """Compare the company's key metrics against peers over the same quarters.

    For each comparable metric we compute, per period, the company value minus
    the mean of the peers that report it, then score how far and how
    persistently the company diverges. Only peers with their own ledger data are
    used (built on demand from any PDFs dropped in their manual folder).
    """
    llm = llm or get_llm()
    if ledger.empty:
        return []

    peers = _identify_peers(company, ledger, llm, max_peers=max_peers)
    if not peers:
        logger.info("No peers identified for %s; peer detector skipped.", company)
        return []

    # Build/load each peer's ledger (only those with ingested data count).
    peer_data: dict[str, pd.DataFrame] = {}
    for peer in peers:
        try:
            pled = _ensure_peer_ledger(peer, llm)
        except Exception as exc:
            logger.warning("Could not build ledger for peer %s: %s", peer, exc)
            continue
        if pled is not None and not pled.empty:
            peer_data[peer] = pled

    if not peer_data:
        logger.info("No peer ledger data available for %s (peers: %s).", company, peers)
        return []

    anomalies: list[dict] = []
    for metric_name, keys in _PEER_METRICS.items():
        company_series = dict(_period_sorted_values(_match_metric_rows(ledger, keys)))
        if len(company_series) < 2:
            continue

        # Peer mean per period (only periods where >=1 peer reports it).
        peer_means: dict[str, float] = {}
        for period in company_series:
            vals = []
            for peer, pled in peer_data.items():
                pseries = dict(_peer_metric_series(pled, keys))
                if period in pseries:
                    vals.append(pseries[period])
            if vals:
                m = ar.mean(vals)
                if not math.isnan(m):
                    peer_means[period] = m

        common = [p for p in company_series if p in peer_means]
        if len(common) < 2:
            continue

        gaps = [company_series[p] - peer_means[p] for p in common]
        mean_gap = ar.mean(gaps)
        persistence = sum(1 for g in gaps if (g >= 0) == (mean_gap >= 0)) / len(gaps)
        magnitude = ar.clamp(abs(mean_gap) / 5.0, 0.0, 1.0)  # 5pt gap ~= strong
        score = ar.weighted_score(
            {"magnitude": magnitude, "persistence": persistence},
            {"magnitude": 0.6, "persistence": 0.4},
        )
        if math.isnan(score) or score <= 0.3:
            continue

        direction = "above" if mean_gap >= 0 else "below"
        anomalies.append(
            {
                "detector": "peer_divergence",
                "company": company,
                "score": round(float(score), 4),
                "title": f"{metric_name.replace('_',' ')} is persistently {direction} peers "
                         f"(avg gap {mean_gap:+.1f}pt over {len(common)} periods)",
                "summary": (
                    f"Across {len(common)} comparable quarters, the company's '{metric_name.replace('_',' ')}' "
                    f"runs {mean_gap:+.2f} points {'above' if mean_gap>=0 else 'below'} the peer mean "
                    f"({persistence*100:.0f}% of periods). Peers: {', '.join(peer_data)}."
                ),
                "numbers": {
                    "metric": metric_name,
                    "mean_gap_vs_peers": round(mean_gap, 4),
                    "persistence": round(persistence, 3),
                    "n_periods": len(common),
                    "peers": list(peer_data),
                },
                "timeline": [
                    {"period": p, "company": company_series[p], "peer_mean": peer_means[p]}
                    for p in common
                ],
                "evidence": _evidence_from_rows(_match_metric_rows(ledger, keys).tail(2)),
                "metrics": {
                    "metric": metric_name,
                    "company_series": [{"period": p, "value": company_series[p]} for p in common],
                    "peer_series": [{"period": p, "value": peer_means[p]} for p in common],
                },
            }
        )

    anomalies.sort(key=lambda a: a["score"], reverse=True)
    return anomalies


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_all(ledger: pd.DataFrame, company: str, *, llm=None) -> list[dict]:
    """Run every detector and return the combined, score-sorted anomalies."""
    llm = llm or get_llm()
    results: list[dict] = []
    for detect in (detect_vanished_or_redefined, detect_narrative_vs_numbers, detect_peer_divergence):
        try:
            # detect_vanished_or_redefined is pure-pandas and takes no llm.
            if detect is detect_vanished_or_redefined:
                results.extend(detect(ledger, company))
            else:
                results.extend(detect(ledger, company, llm=llm))
        except Exception as exc:
            logger.error("Detector %s failed: %s", getattr(detect, "__name__", detect), exc)
    results.sort(key=lambda a: a.get("score", 0.0), reverse=True)
    logger.info("Detectors produced %d anomaly record(s) for %s.", len(results), company)
    return results


if __name__ == "__main__":  # pragma: no cover - manual debug entrypoint
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from ledger import load_ledger

    name = sys.argv[1] if len(sys.argv) > 1 else "Bharath Foods Limited"
    frame = load_ledger(name)
    anoms = run_all(frame, name)
    for a in anoms[:5]:
        print(f"[{a['score']:.3f}] {a['detector']}: {a['title']}")

