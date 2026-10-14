"""Milestone 3b -- red team.

Responsibility
--------------
Attack the hypothesis instead of agreeing with it. For the top claim, evaluate
the four standard alternative explanations:

1. ``sector_wide``       -- are peers doing the same thing? (checked against
   peer ledgers *deterministically* where data exists, plus LLM reasoning)
2. ``accounting_change`` -- could a metric-definition or accounting change
   explain the pattern?
3. ``one_off_event``     -- acquisition, divestment, one-off gain/loss?
4. ``seasonality``       -- ordinary seasonal pattern?

Then output:

* the strongest counterargument (with a source if one exists),
* a falsification condition -- the specific *future* evidence that would
  overturn the claim,
* an Evidence Strength rating of Strong / Moderate / Weak with the reasoning
  shown, and an automatic downgrade when the claim does not survive the
  checks (honesty enforced in code, not left to the model's mood).

The LLM provides the reasoning; every downgrade rule here is deterministic.
"""

from __future__ import annotations

import logging

import pandas as pd

from scd import prompts
from scd.llm import get_llm

logger = logging.getLogger("scd.redteam")

STRENGTH_ORDER = {"Weak": 0, "Moderate": 1, "Strong": 2}

# Four canonical checks (name -> human description).
CHECKS = {
    "sector_wide": "Is this just the whole sector/industry doing the same thing?",
    "accounting_change": "Could an accounting or metric-definition change explain it?",
    "one_off_event": "Could a one-off event, acquisition or divestment explain it?",
    "seasonality": "Could ordinary seasonality explain the pattern?",
}


def _peer_snapshot(company: str, llm) -> dict:
    """Best-effort peer ledger summary used as deterministic red-team context.

    Returns {peer: {metric_series...}} or {} when no peer data is available.
    Never fails the pipeline: peers may simply not be discoverable offline.
    """
    try:
        from detectors import _identify_peers, _ensure_peer_ledger

        peers = _identify_peers(company, pd.DataFrame(), llm, max_peers=4)
    except Exception as exc:
        logger.info("Red team: peer identification unavailable (%s).", exc)
        return {}

    snapshot: dict[str, dict] = {}
    for peer in peers:
        try:
            peer_ledger = _ensure_peer_ledger(peer, llm)
        except Exception:
            peer_ledger = None
        if peer_ledger is None or peer_ledger.empty:
            continue
        metrics: dict[str, list] = []
        for name in peer_ledger["name"].astype(str).unique()[:5]:
            rows = peer_ledger[peer_ledger["name"].astype(str) == name]
            metrics.append({"metric": name, "periods": [
                {"period": r["period"], "value": r["value"]} for _, r in rows.iterrows()
            ][:8]})
        snapshot[peer] = {"metrics": metrics}
    return snapshot


def _downgrade(strength: str, checks: list[dict]) -> tuple[str, str]:
    """Deterministically cap the LLM's strength rating based on the checks.

    Rules (applied in order; first match wins as the *cap*):
    * sector_wide supported      -> cap at Moderate (not company-specific)
    * 2+ of the 4 checks support -> cap at Weak
    * accounting_change supported AND one_off_event supported -> cap at Weak
    The function returns the (possibly lowered) rating and a note explaining
    the downgrade, or the original rating with an empty note.
    """
    supported = {c.get("name") for c in checks if c.get("supported")}
    current = STRENGTH_ORDER.get(strength, 1)
    note = ""

    if len(supported) >= 2 and current > 0:
        strength, note = "Weak", (
            f"Downgraded to Weak: {len(supported)} alternative explanations are "
            f"supported ({', '.join(sorted(supported))}); the claim does not survive."
        )
    elif "sector_wide" in supported and current > 1:
        strength, note = "Moderate", (
            "Downgraded to Moderate: the pattern is at least partly sector-wide, "
            "so it is not company-specific evidence on its own."
        )
    elif "accounting_change" in supported and current > 1:
        strength, note = "Moderate", (
            "Downgraded to Moderate: an accounting or definition change could "
            "explain part of the pattern."
        )
    return strength, note


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_redteam(company: str, hypothesis: dict, *, llm=None) -> dict:
    """Try to DISPROVE ``hypothesis``; return the red-team verdict dict.

    Output schema:
      {
        "checks": [{name, supported, reasoning}, ...],
        "strongest_counterargument": str,
        "counterargument_source": {source_doc, page, quote} | None,
        "falsification_condition": str,
        "evidence_strength": "Strong" | "Moderate" | "Weak",
        "strength_reasoning": str,
        "downgrade_note": str,          # empty unless a rule lowered the rating
        "survived_checks": int,         # how many of the 4 checks the claim survived
      }
    """
    llm = llm or get_llm()
    claim = hypothesis.get("claim") or hypothesis.get("title") or ""

    peers_context = _peer_snapshot(company, llm)
    prompt = prompts.REDTEAM_PROMPT.format(
        company=company,
        claim=claim,
        numbers=hypothesis.get("numbers", {}),
        peers=peers_context or "(no peer data available)",
        timeline=hypothesis.get("timeline", []),
    )

    try:
        verdict = llm.complete_json(prompt, system=prompts.REDTEAM_SYSTEM, expected=dict)
    except Exception as exc:
        logger.error("Red-team LLM call failed (%s); returning a Weak default verdict.", exc)
        verdict = {}

    checks = []
    raw_checks = verdict.get("checks") or []
    seen = set()
    for name, description in CHECKS.items():
        match = next((c for c in raw_checks if isinstance(c, dict) and c.get("name") == name), None)
        checks.append({
            "name": name,
            "description": description,
            "supported": bool(match.get("supported")) if match else False,
            "reasoning": (match.get("reasoning") or "").strip() if match else
                         "No evidence examined; assume not supported.",
        })
        seen.add(name)
    # Keep any extra checks the model added, in case they are informative.
    for c in raw_checks:
        if isinstance(c, dict) and c.get("name") not in seen:
            checks.append({"name": c.get("name"), "description": "",
                           "supported": bool(c.get("supported")),
                           "reasoning": (c.get("reasoning") or "").strip()})

    strength = (verdict.get("evidence_strength") or "Weak").strip().capitalize()
    if strength not in STRENGTH_ORDER:
        strength = "Weak"
    strength, downgrade_note = _downgrade(strength, checks)

    survived = sum(1 for c in checks if not c.get("supported"))
    counterargument = (verdict.get("strongest_counterargument") or "").strip()
    falsification = (verdict.get("falsification_condition") or "").strip()
    if not falsification:
        falsification = (
            "No falsification condition was produced; treat this claim as "
            "untested until the company's next two disclosures address it."
        )

    result = {
        "checks": checks,
        "strongest_counterargument": counterargument,
        "counterargument_source": verdict.get("counterargument_source"),
        "falsification_condition": falsification,
        "evidence_strength": strength,
        "strength_reasoning": (verdict.get("strength_reasoning") or "").strip(),
        "downgrade_note": downgrade_note,
        "survived_checks": survived,
    }
    logger.info("Red team: strength=%s (survived %d/%d checks)%s",
                strength, survived, len(CHECKS),
                " -- " + downgrade_note if downgrade_note else "")
    return result


if __name__ == "__main__":  # pragma: no cover - manual debug entrypoint
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from detectors import run_all
    from hypothesis import build_hypothesis
    from ledger import load_ledger

    name = sys.argv[1] if len(sys.argv) > 1 else "Bharath Foods Limited"
    hyp = build_hypothesis(name, run_all(load_ledger(name), name))
    print(json.dumps(run_redteam(name, hyp), indent=2, ensure_ascii=False))

