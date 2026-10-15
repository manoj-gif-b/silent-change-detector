"""Milestone 4 -- app.py, the Streamlit dashboard.

Run with::

    streamlit run app.py

Shows, for any company that has a finished pipeline run:

* the discovery (one-sentence finding) and why-it-matters bullets,
* key computed numbers and charts (from the verified timeline series),
* the dated timeline of the claim,
* the evidence trail -- click any entry to see the source document, page and
  the verbatim quote (with an on-disk link to the PDF),
* the red-team verdict: counterargument, falsification condition and evidence
  strength (with the reasoning shown),
* an "Export report as PDF" button that renders the whole report to PDF.

Data comes exclusively from ``data/out/<company>/report.json`` produced by
``run.py`` -- the dashboard itself never calls the LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from scd import config
from report import build_pdf_bytes, load_report

st.set_page_config(
    page_title="Silent Change Detector",
    page_icon="🔎",
    layout="wide",
)

_STRENGTH_COLORS = {"Strong": "green", "Moderate": "orange", "Weak": "red"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _companies_with_reports() -> list[str]:
    """Company display names that have a finished report on disk."""
    if not config.OUT_DIR.exists():
        return []
    out = []
    for sub in sorted(config.OUT_DIR.iterdir()):
        if sub.is_dir() and (sub / "report.json").exists():
            out.append(sub.name.replace("_", " ").title())
    return out


def _timeline_frame(report: dict) -> pd.DataFrame:
    """Turn the report timeline into a chartable DataFrame (period, value)."""
    rows = []
    for point in report.get("timeline") or []:
        if not isinstance(point, dict):
            continue
        try:
            value = float(point.get("value"))
        except (TypeError, ValueError):
            continue
        rows.append({"period": str(point.get("period", "?")), "value": value})
    return pd.DataFrame(rows)


def _numbers_table(numbers: dict) -> pd.DataFrame:
    """Flatten the verified 'numbers' dict into a 2-column table."""
    rows = []
    for key, value in (numbers or {}).items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        rows.append({"metric": key, "value": value})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# UI -- sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🔎 Silent Change Detector")
st.sidebar.caption("Autonomous financial research agent -- ATMOS 2026, Track C")
st.sidebar.markdown(
    "Produces **one non-obvious, evidence-backed finding** per company from "
    "public filings. No investment advice."
)

companies = _companies_with_reports()

if not companies:
    st.sidebar.warning(
        "No reports yet. Run the pipeline first:\n\n"
        "`python run.py \"Company Name\"`\n\n"
        "or try the offline demo:\n\n"
        "`python run.py --demo`"
    )
    st.info(
        "**No reports found.** Run the pipeline from the project root, then reload:\n\n"
        "```bash\n"
        'python run.py "Hindustan Unilever Limited"\n'
        "python run.py --demo        # offline demo, no API key needed\n"
        "streamlit run app.py\n"
        "```"
    )
    st.stop()

selected = st.sidebar.selectbox("Company", companies, index=0)
report = load_report(selected)

if report is None:
    st.error(f"No report found for {selected}.")
    st.stop()

# ---------------------------------------------------------------------------
# UI -- header with the discovery
# ---------------------------------------------------------------------------
st.title(f"🔎 {selected}")

meta = report.get("_meta", {})
strength = report.get("evidence_strength", {}) or {}
rating = strength.get("rating", "Weak")
color = _STRENGTH_COLORS.get(rating, "gray")

st.markdown(
    f'<div style="border-left:5px solid {color};padding:8px 16px;'
    f'border-radius:4px;background:rgba(255,165,0,0.08);margin-bottom:8px;">'
    f"<b>Discovery</b><br>{report.get('discovery', '')}</div>",
    unsafe_allow_html=True,
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Evidence strength", rating)
col2.metric("Checks survived", strength.get("checks_survived") or "-")
col3.metric("Detector", (meta.get("detector") or "-").replace("_", " "))
col4.metric("Anomaly score", meta.get("anomaly_score") if meta.get("anomaly_score") is not None else "-")

st.download_button(
    "⬇️ Export report as PDF",
    data=build_pdf_bytes(report, selected),
    file_name=f"silent_change_detector_{config.slugify(selected)}.pdf",
    mime="application/pdf",
)


def _render_evidence_card(i: int, ev: dict) -> None:
    """One clickable evidence entry: click reveals document, page and quote."""
    doc = ev.get("document") or ev.get("source_doc") or "?"
    page = ev.get("page", "?")
    with st.expander(f"📄 {doc}  ·  page {page}"):
        st.markdown(f"**Quote:** “{ev.get('quote') or '(no verbatim quote recorded)'}”")
        st.caption(f"Period: {ev.get('period') or '?'}")
        link = ev.get("link")
        if link and Path(str(link)).exists():
            st.markdown(f"**Source file:** `{link}`")
            try:
                with open(link, "rb") as fh:
                    st.download_button(
                        "⬇️ Download source PDF", data=fh.read(),
                        file_name=Path(str(link)).name, mime="application/pdf",
                        key=f"dl-{i}-{doc}",
                    )
            except OSError:
                pass
        else:
            st.caption("Source file not found on this machine.")


# ---------------------------------------------------------------------------
# UI -- tabs
# ---------------------------------------------------------------------------
tab_why, tab_numbers, tab_timeline, tab_evidence, tab_red, tab_other = st.tabs([
    "💡 Why it matters",
    "📊 Numbers & charts",
    "🗓 Timeline",
    "🧾 Evidence trail",
    "🛡 Red team",
    "🔎 Other candidates",
])

with tab_why:
    for bullet in report.get("why_it_matters") or []:
        st.markdown(f"- {bullet}")

with tab_numbers:
    numbers = report.get("numbers") or {}
    st.dataframe(_numbers_table(numbers), use_container_width=True, hide_index=True)

    frame = _timeline_frame(report)
    if not frame.empty:
        st.subheader("Metric across periods (from verified ledger values)")
        st.line_chart(frame.set_index("period")["value"])
        st.caption(
            "Charted from the pipeline's verified timeline values -- every point "
            "traces to a document, page and quote in the Evidence trail tab."
        )
    else:
        st.info("No chartable numeric series in this report.")

with tab_timeline:
    timeline = report.get("timeline") or []
    if timeline:
        for point in timeline:
            if not isinstance(point, dict):
                continue
            date = point.get("date") or "?"
            period = point.get("period") or "?"
            event = point.get("event") or ""
            value = point.get("value")
            value_str = f" (value: {value})" if value is not None else ""
            st.markdown(f"- **{date}** · `{period}` — {event}{value_str}")
    else:
        st.info("No timeline recorded.")

with tab_evidence:
    trail = report.get("evidence_trail") or []
    if trail:
        st.caption("Click any entry to see the source document, page and verbatim quote.")
        for i, ev in enumerate(trail):
            _render_evidence_card(i, ev)
    else:
        st.info("No evidence trail recorded.")

with tab_red:
    st.subheader("Strongest counterargument")
    st.warning(report.get("counterargument") or "(none recorded)")
    st.subheader("Falsification condition")
    st.info(report.get("falsification_condition") or "(none recorded)")
    st.subheader("Evidence strength")
    st.markdown(f"**Rating: {rating}**")
    st.write(strength.get("reasoning") or "")
    if strength.get("downgrade_note"):
        st.caption(f"Downgraded: {strength['downgrade_note']}")
    checks = meta.get("redteam_checks") or []
    if checks:
        st.subheader("The four checks")
        for chk in checks:
            icon = "❌" if chk.get("supported") else "✅"
            st.markdown(
                f"{icon} **{str(chk.get('name', '')).replace('_', ' ').title()}** "
                f"({'supported' if chk.get('supported') else 'not supported'})"
            )
            st.caption(chk.get("reasoning") or "")

with tab_other:
    others = meta.get("other_candidates") or []
    if others:
        st.caption("Other anomalies the pipeline found, ranked. The top one became the claim above.")
        st.dataframe(
            pd.DataFrame(others), use_container_width=True, hide_index=True
        )
    else:
        st.info("No other candidates recorded.")

st.divider()
st.caption(
    "Silent Change Detector -- ATMOS 2026 Track C. All numbers computed in "
    "Python/pandas from public documents; the LLM never does arithmetic. "
    "**This is research, not investment advice.**"
)



