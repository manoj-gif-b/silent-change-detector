"""Offline demo: synthetic data + a scripted LLM.

Purpose
-------
Lets the WHOLE pipeline run end-to-end with no Gemini API key and no real
filings, so the detectors, hypothesis, red-team and dashboard can be exercised
and demonstrated deterministically. It is intentionally fictional -- "Bharath
Foods Limited" is not a real company and the numbers are illustrative.

Two pieces:
* :func:`generate_demo_pdfs` -- writes synthetic quarterly/annual PDFs into the
  company's manual folder, embedding a deliberate *silent change* story.
* :func:`scripted_llm` -- a :class:`~scd.llm.MockLLM` whose responses are keyed
  off the prompt so the pipeline behaves as if a model were reading the docs.

The embedded story (a classic "silent change"):
    A packaged-foods company used to report *volume growth* and *rural revenue
    mix* every quarter. As volumes slowed, management re-labelled the story as
    "value-led growth" and "premiumisation", then quietly stopped disclosing
    volume growth and the rural split altogether -- while transcripts kept
    implying volume-led momentum. The numbers tell a different story.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import company_manual_dir

DEMO_COMPANY = "Bharath Foods Limited"

# ---------------------------------------------------------------------------
# The "ground truth" the synthetic documents encode.
# ---------------------------------------------------------------------------
# Quarterly rows: (label, revenue_growth_%, volume_growth_% or None if dropped,
#                  rural_mix_% or None if dropped, narrative_theme)
_QUARTERS = [
    ("Q1 FY21", 14.0, 9.0, 33.0, "volume"),
    ("Q2 FY21", 13.0, 8.0, 33.0, "volume"),
    ("Q3 FY21", 12.0, 7.0, 32.0, "volume"),
    ("Q4 FY21", 13.5, 8.0, 32.0, "volume"),
    ("Q1 FY22", 12.0, 7.0, 32.0, "volume"),
    ("Q2 FY22", 11.5, 6.0, 31.0, "volume"),
    ("Q3 FY22", 11.0, 6.0, 31.0, "volume"),
    ("Q4 FY22", 10.5, 5.0, 31.0, "volume"),
    ("Q1 FY23", 10.0, 4.0, None, "value"),
    ("Q2 FY23", 10.5, 3.0, None, "value"),
    ("Q3 FY23", 11.0, 3.0, None, "value"),
    ("Q4 FY23", 11.5, 2.0, None, "value"),
    ("Q1 FY24", 11.0, 1.0, None, "value"),
    ("Q2 FY24", 11.5, 0.0, None, "value"),
    ("Q3 FY24", 12.0, None, None, "value"),   # volume growth disclosure stops
    ("Q4 FY24", 12.0, None, None, "value"),
    ("Q1 FY25", 12.5, None, None, "value"),
    ("Q2 FY25", 12.0, None, None, "value"),
]

_PEERS = ["Tata Consumer Products", "Nestle India", "Britannia Industries"]


# ---------------------------------------------------------------------------
# Synthetic PDF generation
# ---------------------------------------------------------------------------
def _results_page_text(label: str, rev: float, vol: float | None, rural: float | None, theme: str) -> str:
    lines = [f"{label} -- Unaudited financial results for Bharath Foods Limited.",
             f"Revenue from operations grew {rev:.1f}% year-on-year to Rs 1,000 crore."]
    if theme == "volume" and vol is not None:
        lines.append(f"Our growth was volume-led: volumes increased {vol:.1f}% year-on-year.")
    else:
        lines.append("Growth was value-led, driven by premiumisation and pricing.")
    if vol is not None:
        lines.append(f"Reported volume growth: {vol:.1f}%.")
    else:
        lines.append("Volume growth is not separately disclosed this quarter.")
    if rural is not None:
        lines.append(f"Rural markets contributed {rural:.0f}% of revenue.")
    else:
        lines.append("Rural and urban split is not disclosed this quarter.")
    lines.append("Gross margin expanded on account of input-cost deflation and premium mix.")
    return "\n".join(lines)


def _transcript_text(label: str, theme: str) -> str:
    # Management keeps claiming volume-led momentum on calls in EVERY era, even
    # after the filed results stopped disclosing volume growth. This is the
    # archetypal narrative-vs-numbers contradiction the pipeline should catch.
    return (
        f"Management commentary -- {label} earnings call.\n"
        "CEO: Our growth continues to be volume-led, with broad-based volume momentum "
        "across our core categories. We remain confident in our volume-led strategy and "
        "rural demand is strong.\n"
        "CFO: Volume growth remained the backbone of the quarter; rural contribution "
        "continued to support us."
    )


def generate_demo_pdfs(company: str = DEMO_COMPANY) -> Path:
    """Write synthetic quarterly-result and quarterly transcript PDFs.

    One results PDF and one earnings-call transcript per quarter, so the
    narrative_vs_numbers detector can align per-quarter narrative emphasis with
    the per-quarter metric trend.
    """
    from tests._fixtures import make_pdf

    folder = company_manual_dir(company)
    folder.mkdir(parents=True, exist_ok=True)

    for label, rev, vol, rural, theme in _QUARTERS:
        fname = f"{label.replace(' ', '_')}_results.pdf"
        make_pdf(folder / fname, {1: _results_page_text(label, rev, vol, rural, theme)})
        tname = f"{label.replace(' ', '_')}_earnings_call.pdf"
        make_pdf(folder / tname, {1: _transcript_text(label, theme)})
    return folder


# ---------------------------------------------------------------------------
# Scripted LLM (keyed off the prompt text)
# ---------------------------------------------------------------------------
def _lookup_quarter(context: str) -> tuple | None:
    """Find the synthetic quarter whose label appears in the chunk context."""
    for row in _QUARTERS:
        if row[0] in context:
            return row
    return None


def _kpi_response(context: str) -> str:
    row = _lookup_quarter(context)
    if row is None:
        # A transcript or annual chunk -> no numeric KPIs.
        return json.dumps([])
    label, rev, vol, rural, _theme = row
    records = [
        {"name": "Revenue growth", "value": f"{rev:.1f}", "unit": "%", "definition": "year-on-year revenue growth",
         "period": label, "page": 1, "quote": f"Revenue from operations grew {rev:.1f}% year-on-year."},
    ]
    if vol is not None:
        records.append({"name": "Volume growth", "value": f"{vol:.1f}", "unit": "%", "definition": "year-on-year volume growth",
                        "period": label, "page": 1, "quote": f"Reported volume growth: {vol:.1f}%."})
    if rural is not None:
        records.append({"name": "Rural revenue mix", "value": f"{rural:.0f}", "unit": "%", "definition": "rural share of revenue",
                        "period": label, "page": 1, "quote": f"Rural markets contributed {rural:.0f}% of revenue."})
    return json.dumps(records)


def _narrative_response(context: str) -> str:
    # All demo transcripts claim volume-led/rural-led momentum in every period,
    # so the narrative emphasis for those topics is consistently "high" while
    # the underlying metrics (and their disclosure) deteriorate.
    themes_emph = {"volume": "high", "rural": "high", "value": "low", "premium": "low"}
    quote = "Our growth continues to be volume-led, with broad-based volume momentum."
    out = []
    for topic, emph in themes_emph.items():
        out.append({"topic": topic, "emphasis": emph, "quote": quote if emph == "high" else None})
    return json.dumps(out)


def _peer_response(prompt: str) -> str:
    return json.dumps([{"peer": p, "rationale": "Listed Indian packaged-foods peer."} for p in _PEERS])


def _hypothesis_response(prompt: str) -> str:
    return json.dumps({
        "claim": "Bharath Foods quietly stopped disclosing quarterly volume growth from Q3 FY24 and "
                 "re-framed its story as value-led/premium, while transcripts still implied volume "
                 "momentum; reported volume growth had already decayed from ~9% (FY21) to ~0% (Q2 FY24).",
        "why_it_matters": [
            "The headline revenue kept compounding, masking a stall in underlying volumes.",
            "Disclosure of volume growth and rural mix was dropped exactly as the numbers weakened.",
            "Growth quality shifted from volume-led to price/mix-led without an explicit acknowledgement.",
        ],
        "testable_prediction": "If volumes are genuinely flat, the next quarterly filing will continue to omit "
                               "volume growth and lean further on value/premium language.",
    })


def _redteam_response(prompt: str) -> str:
    return json.dumps({
        "checks": [
            {"name": "sector_wide", "supported": False, "reasoning": "Peers in the demo set continued to report volume growth, so this is not sector-wide."},
            {"name": "accounting_change", "supported": False, "reasoning": "No accounting-standard or metric redefinition is documented; the metric was simply dropped."},
            {"name": "one_off_event", "supported": False, "reasoning": "No acquisition or divestment coincides with the disclosure change."},
            {"name": "seasonality", "supported": True, "reasoning": "Quarterly seasonality exists but cannot explain a multi-year monotonic decline in volume growth."},
        ],
        "strongest_counterargument": "The company may have re-classified volumes into a broader 'value' metric rather than losing volumes.",
        "counterargument_source": None,
        "falsification_condition": "A future filing that reinstates volume growth showing recovery above ~3% YoY would overturn the claim.",
        "evidence_strength": "Moderate",
        "strength_reasoning": "The disclosure change is well-evidenced and the volume decay is clear across many quarters, "
                              "but on synthetic demo data corroboration is limited; a real run on filings would be stronger.",
    })


def scripted_llm():
    """Return a MockLLM wired to the demo responses above."""
    from .llm import MockLLM

    def responder(prompt: str, system: str | None) -> str:
        p = prompt
        if "Extract every recurring KEY PERFORMANCE INDICATOR" in p:
            return _kpi_response(p)
        if "how strongly the company EMPHASISES" in p:
            return _narrative_response(p)
        if "comparable listed peer companies" in p:
            return _peer_response(p)
        if "Turn the following detected anomaly" in p:
            return _hypothesis_response(p)
        if "Try to DISPROVE this finding" in p:
            return _redteam_response(p)
        return json.dumps({"items": []})

    llm = MockLLM()
    llm.set_script(responder)
    return llm

