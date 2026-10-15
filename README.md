# Silent Change Detector

An **autonomous financial-research agent** for the **ATMOS 2026** hackathon
(*Finding the Next Signal*, Track C — Consumer, FMCG & Retail, NIFTY 50).

Give it a NIFTY-50 FMCG company name and it investigates that company's public
disclosures (annual reports, quarterly results, investor presentations and
earnings-call transcripts) to produce **one non-obvious, evidence-backed
finding**.

> **Thesis.** The story is often in what a company *stopped saying*, *redefined*,
> or *emphasised differently* over time — compared against what the numbers
> actually show. This tool hunts exactly that kind of "silent change".

It contains **no company-specific code and no hard-coded results**. The same
pipeline runs unchanged for any company in the sector.

---

## What it guarantees

- **Public data only.** Every claim traces to a *document → page → date → quote*.
- **All arithmetic is done in Python/pandas**, never by the LLM (see
  `scd/arithmetic.py`, covered by `tests/test_arithmetic.py`).
- **LLM = Gemini** via the official `google-genai` package. Key from
  `GEMINI_API_KEY` in `.env`; model(s) from `GEMINI_MODELS` — a comma-separated
  fallback list (e.g. `gemini-3.8-flash,gemini-2.5-flash`). If a model returns
  `503/504/429` or a connection error after 3 attempts, the next model in the
  list is tried automatically. `GEMINI_MODEL` still works as a single-model
  fallback for backward compatibility. Note `gemini-2.5-flash` is retired for
  new accounts (it 404s) — prefer `gemini-3.8-flash`.
- **Every LLM call and every download is cached on disk** (`cache/`), so reruns
  are fast and free.
- **No investment recommendations.** This is a research finding, not advice.

---

## Pipeline (one module each)

| # | Module | Job |
|---|--------|-----|
| 1 | `ingest.py` | Find & download docs, parse PDFs with PyMuPDF, split into metadata-tagged chunks (company / doc type / period / page). Also reads a manual drop-in folder. |
| 2 | `ledger.py` | Use Gemini to extract every recurring KPI from each chunk as JSON, building a disclosure-ledger table across periods. |
| 3 | `detectors.py` | Three scored detectors: `vanished_or_redefined`, `narrative_vs_numbers`, `peer_divergence`. |
| 4 | `hypothesis.py` | Rank anomalies; turn the top one into a testable claim with a dated timeline. |
| 5 | `redteam.py` | Try to **disprove** the claim (sector-wide? accounting/definition change? one-off event? seasonality?) and rate Evidence Strength honestly. |
| 6 | `report.py` | Assemble the final JSON with the exact required schema. |
| 7 | `app.py` | Streamlit dashboard: discovery, key numbers, charts, timeline, clickable evidence trail, and **Export report as PDF**. |
| 8 | `run.py` | CLI that runs the whole pipeline with progress logs. |

Supporting library modules live in `scd/`: `config.py`, `cache.py`,
`arithmetic.py`, `llm.py` (robust Gemini client), `pdf.py`, `chunking.py`,
`periods.py`, `prompts.py`, and `demo.py` (offline fixture).

---

## Setup

Requires **Python 3.11**.

```powersShell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your key
copy .env.example .env
#    then open .env and paste your Gemini key into GEMINI_API_KEY

# 4. (Recommended) verify connectivity/credentials before a real run
python test_key.py
```

`.env` is **git-ignored**; commit only `.env.example`.

---

## Usage

### Quick offline demo (no API key, no internet)

Confirms the whole pipeline — including the three detectors, hypothesis, red
team and dashboard — works end to end on a synthetic company with a built-in
"silent change" story:

```PowerShell
$env:LLM_MODE = "offline"
python run.py "Bharath Foods Limited"
streamlit run app.py
```

### Real run

```PowerShell
python run.py "Hindustan Unilever Limited"
streamlit run app.py
```

`run.py` prints timestamped progress logs and writes
`data/out/<company>/report.json` at the end.

### Where do the documents come from?

Websites and exchange portals often block automated downloads, so the pipeline
is built around a **manual drop-in folder**:

```
data/manual/<Company Name>/
```

Drop the company's PDFs there (annual reports, quarterly results, investor
presentations, earnings-call transcripts). Document type and fiscal period are
inferred from each file's name and contents. There is also an **optional**
best-effort auto-downloader (`ingest.auto_download`) that fetches only from
explicit public URLs you register — it degrades quietly if a host blocks it.

### Dashboard

```PowerShell
streamlit run app.py
```

Shows the discovery, key numbers and charts, the timeline, and a clickable
**evidence trail** — click any claim to see its source document, page and quote.
An **"Export report as PDF"** button writes the full finding to a PDF.

---

## Output schema

`report.json` contains exactly these fields (plus a `_meta` block):

`discovery` (one sentence) · `why_it_matters` (3–5 bullets) · `evidence`
(independent sources with links) · `timeline` · `numbers` (only the calculations
needed) · `counterargument` · `falsification_condition` ·
`evidence_strength` (Strong / Moderate / Weak, with reasoning) ·
`evidence_trail` (finding → doc → page → quote).

---

## Troubleshooting LLM errors (`ECONNRESET`, rejected credentials)

If Gemini calls fail, **run this first**:

```PowerShell
python test_key.py
```

It reports, in order:

1. **Is the network blocked?** — a plain HTTPS `GET` to
   `https://generativelanguage.googleapis.com` (no key involved).
   *`NETWORK BLOCKED`* means the `ECONNRESET` is a firewall/proxy issue. If your
   network needs a proxy, set `HTTPS_PROXY`/`HTTP_PROXY` in `.env` — `scd/llm.py`
   honours them and logs whether a proxy is in use.
2. **Is the key configured?** — never printed. `scd/llm.py` strips surrounding
   spaces and quotes from the key before use.
3. **Does a tiny real call succeed?** — routed through `scd/llm.py` so it gets
   the same retry/classification. It labels failures as *bad key*,
   *model not found*, *rate limit*, *network* or **server**.
   - **`[server]`** = Google's own capacity problem (`503 UNAVAILABLE` "high
     demand" / `504 DEADLINE_EXCEEDED`). The request reached Google fine — this
     is transient and **not** your key or network. Just rerun; the retry/backoff
     already tries 5 times and cached calls are never repeated.
   - **`[model not found]`** = the model isn't offered to your account.
     `gemini-2.5-flash` is retired for new users → add a live model to
      `GEMINI_MODELS` (e.g. `gemini-3.8-flash,gemini-2.5-flash`). With a list,
      a retired/overloaded model is skipped automatically.

`scd/llm.py` is built to ride out overload and flaky networks:

- **Multi-model fallback.** It reads `GEMINI_MODELS` (a comma-separated list)
  and tries each model in order. A model that fails with `503/504/429` or an
  SSL/connection error after 3 attempts is abandoned and the **next** model is
  used. `GEMINI_MODEL` still works on its own for backward compatibility.
- **Jittered exponential backoff**, 3s → 60s (random ±50%), so retries don't
  stampede an already-overloaded API.
- **120s per-request timeout.**
- **Prints which model actually answered** each call (`[llm] answered by model:
  …`), so you can watch the fallback happen live.
- **Caches every successful response on disk by `(prompt, task)` — not by
  model** — so a result from *any* model is reused and a run resumes after a
  crash without repeating a call. The API key is never logged or printed.

To keep working with no internet at all, set `LLM_MODE=offline` in `.env`: the
pipeline then uses the built-in scripted LLM so it stays fully testable.

---

## Project structure

```
.
├── run.py                 # CLI: full pipeline with progress logs
├── app.py                 # Streamlit dashboard + PDF export
├── ingest.py              # download / manual-drop / parse / chunk
├── ledger.py              # KPI extraction -> disclosure ledger
├── detectors.py           # 3 scored detectors
├── hypothesis.py          # rank + turn top anomaly into a claim
├── redteam.py             # adversarial disproof + strength rating
├── report.py              # final JSON assembly
├── test_key.py            # connectivity + credentials diagnostic
├── requirements.txt
├── .env.example
├── .gitignore             # ignores .env, cache/, data/
├── README.md
├── scd/                   # library: config, cache, arithmetic, llm, pdf,
│                          #          chunking, periods, prompts, demo
├── tests/                 # pytest: arithmetic helpers + PDF fixtures
└── scripts/smoke_demo.py  # offline end-to-end smoke run
```

`cache/` and `data/` are git-ignored (generated outputs and downloads).

---

## Tests

```PowerShell
python -m pytest tests/ -q
```

Covers the pure arithmetic helpers that guarantee "all math is done in Python,
never by the LLM" (percent change, YoY/QoQ, CAGR, slope, margins, receivable
days, price/volume split, z-score, weighted scoring) plus graceful handling of
missing/`None`/string inputs.

---

## Rules honoured

- Public data only; every claim traces to a document, page, date and quote.
- All arithmetic in Python/pandas, never the LLM.
- Gemini via `google-genai`; key and model from `.env`.
- Every LLM call and download cached on disk.
- **No investment recommendations.**

