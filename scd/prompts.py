"""Centralised LLM prompt templates.

Keeping prompts in one module makes them easy to review and ensures the cache
key (which includes the system instruction) stays stable between runs. Each
prompt asks the model ONLY to extract, label or phrase text -- never to do
arithmetic, which is handled by :mod:`scd.arithmetic`.
"""

from __future__ import annotations

# Bump this when a prompt's wording changes in a way that should invalidate
# previously cached LLM responses.
PROMPT_VERSION = "1"

# ---------------------------------------------------------------------------
# Ledger extraction
# ---------------------------------------------------------------------------
KPI_EXTRACT_SYSTEM = (
    "You are a meticulous financial-data extractor for equity research. "
    "You read excerpts from Indian company filings and extract KEY PERFORMANCE "
    "INDICATORS exactly as reported. You NEVER compute, estimate or round "
    "numbers yourself -- you only copy figures that are explicitly present in "
    "the text. You always return strictly valid JSON."
)

# The actual user prompt for KPI extraction. ``{context}`` is replaced with the
# chunk text plus its company/doc/period/page header.
KPI_EXTRACT_PROMPT = """Extract every recurring KEY PERFORMANCE INDICATOR (KPI) found in the filing excerpt below.

A "recurring KPI" is a metric a company reports period after period, such as:
revenue, net sales, volumes (in tonnes/lakh cases/MU), EBITDA, EBIT, gross
margin %, operating margin %, PAT/net profit, EPS, receivable/trade receivable
days, inventory days, domestic vs export revenue, rural vs urban mix, value
vs volume growth, marketing spend, distribution reach, market share, etc.

RULES (follow strictly):
- Copy values EXACTLY as printed. Do not do any arithmetic. Do not invent data.
- If a number is not explicitly in the text, do not include that KPI.
- Capture the reporting period label exactly (e.g. "FY2024", "Q3 FY2024", "FY25").
- "definition" = the company's own wording for the metric (verbatim, short).
- "quote" = the exact sentence or phrase from the excerpt containing the number.
- "unit" = the unit as printed (e.g. "INR crore", "%", "lakh MT", "million cases").
- "page" = the page number given in the header.
- Only extract from the excerpt; never use outside knowledge.

Return a JSON array. Each element MUST have exactly these keys:
{{
  "name": "<canonical KPI name, e.g. Revenue from operations>",
  "value": "<the number as a string, exactly as printed>",
  "unit": "<unit as printed or null>",
  "definition": "<verbatim wording used by the company, or null>",
  "period": "<period label as printed, e.g. FY2024>",
  "page": <integer page number>,
  "quote": "<exact sentence containing the number>"
}}

If no KPI is present, return an empty array: [].

----- FILING EXCERPT -----
{context}
"""

# Batched variant: several excerpts from the SAME document are sent in one call
# to reduce the number of round-trips. Each excerpt is wrapped in an <EXCERPT>
# tag carrying its own page number; the model attributes every KPI to the page
# of the excerpt it came from (the "page" key below), which preserves the
# evidence trail exactly as in the single-chunk version.
KPI_EXTRACT_BATCH_PROMPT = """You are given SEVERAL excerpts (each tagged <EXCERPT page="N">) from the SAME filing, in order.

For EACH excerpt, extract every recurring KEY PERFORMANCE INDICATOR (KPI) as
defined below, and tag every KPI with the page number of the excerpt it came
from.

A "recurring KPI" is a metric reported period after period: revenue, volumes,
EBITDA, margins, PAT, EPS, receivable days, rural/urban mix, value vs volume
growth, marketing spend, market share, etc.

RULES (strict):
- Copy values EXACTLY as printed; do no arithmetic; do not invent data.
- Only extract numbers explicitly present in the excerpt.
- "definition" = the company's own wording (verbatim, short).
- "quote" = the exact sentence containing the number.
- "page" = the page attribute of the <EXCERPT> the KPI came from.
- If an excerpt has no KPI, contribute nothing for it.

Return a single JSON array covering ALL excerpts. Each element MUST have:
{{"name": "...", "value": "...", "unit": "...", "definition": "...",
  "period": "...", "page": <int>, "quote": "..."}}

If nothing qualifies across all excerpts, return: [].

----- FILING EXCERPTS -----
{context}
"""


# ---------------------------------------------------------------------------
# Narrative topic tagging (for narrative_vs_numbers)
# ---------------------------------------------------------------------------
NARRATIVE_SYSTEM = (
    "You analyse earnings-call and investor-presentation language. You identify "
    "which strategic themes a company EMPHASISES and how strongly, and you "
    "quote the exact wording. You return strictly valid JSON."
)

NARRATIVE_PROMPT = """For each of the themes below, decide how strongly the company EMPHASISES it in the excerpt.

Themes: {themes}

For each theme return an emphasis level:
- "high"  = the company leads with it or repeats it as a core message
- "medium" = mentioned meaningfully but not central
- "low"   = only a passing mention
- "absent" = not mentioned

Also give a short verbatim quote (max ~200 chars) that best shows the emphasis,
or null if absent. Do not infer financial values; you are only tagging language.

Return a JSON array; each element:
{{
  "topic": "<one of the themes>",
  "emphasis": "high|medium|low|absent",
  "quote": "<verbatim snippet or null>"
}}

----- EXCERPT (period: {period}) -----
{context}
"""


# ---------------------------------------------------------------------------
# Peer identification
# ---------------------------------------------------------------------------
PEER_SYSTEM = (
    "You are an equity-research analyst. You identify comparable listed "
    "companies (peers) that a company itself names or that operate in the same "
    "consumer/FMCG segment. You return strictly valid JSON."
)

PEER_PROMPT = """From the excerpts below (filed by or about "{company}"), identify 3-4 comparable listed peer companies.

Prefer companies the document EXPLICITLY names as peers/competitors. If none are
named, fall back to well-known listed companies in the same consumer/FMCG/retail
sub-segment within India. Give the full company name as it appears on the NSE.

Return a JSON array of objects:
[
  {{"peer": "<company name>", "rationale": "<why it is comparable, <=120 chars>"}}
]

Return at most 4 peers.

----- EXCERPTS -----
{context}
"""


# ---------------------------------------------------------------------------
# Hypothesis phrasing
# ---------------------------------------------------------------------------
HYPOTHESIS_SYSTEM = (
    "You are a financial-research writer. You turn a numeric anomaly that has "
    "already been computed into ONE crisp, testable, non-obvious finding. You "
    "do not add new numbers and you never give investment advice. You return "
    "strictly valid JSON."
)

HYPOTHESIS_PROMPT = """Turn the following detected anomaly into ONE testable finding.

Company: {company}
Detector: {detector}
Computed numbers (already verified -- do NOT recompute or alter): {numbers}
Supporting quotes with sources: {quotes}
Timeline periods involved: {timeline}

Produce:
- "claim": ONE sentence stating the non-obvious, evidence-backed finding. It must
  reference the specific change over time (e.g. what was dropped, redefined or
  emphasised differently) and the numbers that support it.
- "why_it_matters": 3-5 short bullets on why this matters for understanding the
  company (NOT investment advice; no buy/sell language).
- "testable_prediction": one sentence describing what future disclosure would
  confirm or refute the claim.

Return JSON:
{{
  "claim": "...",
  "why_it_matters": ["...", "...", "..."],
  "testable_prediction": "..."
}}
"""


# ---------------------------------------------------------------------------
# Red team
# ---------------------------------------------------------------------------
REDTEAM_SYSTEM = (
    "You are a skeptical red-team analyst. Your job is to DISPROVE a research "
    "claim, not to agree with it. You check four alternative explanations, pick "
    "the strongest counterargument, state a clear falsification condition, and "
    "rate how well the claim survives. You are honest and willing to downgrade "
    "weak claims. You return strictly valid JSON and give NO investment advice."
)

REDTEAM_PROMPT = """Try to DISPROVE this finding. Be adversarial and specific.

Company: {company}
Finding (claim): {claim}
Numbers already computed: {numbers}
Peer context (if any): {peers}
Timeline: {timeline}

Evaluate the four standard alternative explanations and, for each, say whether
it is supported by the available evidence and why:
1. sector_wide      - is this just the whole sector/industry doing the same thing?
2. accounting_change - could an accounting or metric-definition change explain it?
3. one_off_event    - could a one-off event, acquisition or divestment explain it?
4. seasonality      - could ordinary seasonality explain the pattern?

Then:
- "strongest_counterargument": the single best reason the claim might be WRONG.
- "counterargument_source": a doc/page/quote if one supports the counterargument, else null.
- "falsification_condition": what specific FUTURE evidence would overturn the claim.
- "evidence_strength": one of "Strong", "Moderate", "Weak".
- "strength_reasoning": 2-3 sentences justifying the rating. Downgrade if the
  claim does not survive the four checks.

Return JSON:
{{
  "checks": [
    {{"name": "sector_wide", "supported": true|false, "reasoning": "..."}},
    {{"name": "accounting_change", "supported": true|false, "reasoning": "..."}},
    {{"name": "one_off_event", "supported": true|false, "reasoning": "..."}},
    {{"name": "seasonality", "supported": true|false, "reasoning": "..."}}
  ],
  "strongest_counterargument": "...",
  "counterargument_source": null,
  "falsification_condition": "...",
  "evidence_strength": "Strong|Moderate|Weak",
  "strength_reasoning": "..."
}}
"""
