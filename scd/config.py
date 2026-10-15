"""Central configuration for the Silent Change Detector.

Everything path- and env-related lives here so the rest of the codebase can
stay free of magic strings. All settings are read from environment variables
(loaded from a local ``.env`` file if present) with sane defaults.

Directory layout (relative to the project root, which is the parent of the
``scd`` package)::

    cache/                     # cached LLM responses + downloaded files
    data/manual/<company>/     # user-dropped PDFs (fallback source)
    data/raw/<company>/        # auto-downloaded PDFs
    data/chunks/<company>/     # parsed + chunked text (JSON per document)
    data/ledger/<company>/     # extracted disclosure ledger (CSV/JSON)
    data/out/<company>/        # final report JSON
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# PROJECT_ROOT is the directory that contains the `scd` package.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

CACHE_DIR: Path = PROJECT_ROOT / "cache"
DATA_DIR: Path = PROJECT_ROOT / "data"
MANUAL_DIR: Path = DATA_DIR / "manual"
RAW_DIR: Path = DATA_DIR / "raw"
CHUNKS_DIR: Path = DATA_DIR / "chunks"
LEDGER_DIR: Path = DATA_DIR / "ledger"
OUT_DIR: Path = DATA_DIR / "out"


def _ensure_dirs() -> None:
    for d in (CACHE_DIR, DATA_DIR, MANUAL_DIR, RAW_DIR, CHUNKS_DIR, LEDGER_DIR, OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# Load a local .env file if present. Existing environment variables take
# precedence (override=False) so an exported GEMINI_API_KEY still wins.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_gemini_api_key() -> str | None:
    """Return the Gemini API key from the environment, or ``None`` if unset."""
    key = os.getenv("GEMINI_API_KEY")
    if key and key.strip() and key != "your_gemini_api_key_here":
        return key.strip()
    return None


def get_model_name() -> str:
    """Return the LLM model name (default ``gemini-2.5-flash``)."""
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()


def get_llm_provider() -> str:
    """Return the LLM provider name: ``gemini`` (default) or ``mock``."""
    return os.getenv("SCD_LLM_PROVIDER", "gemini").strip().lower()


def get_int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back to ``default`` on any error."""
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, TypeError):
        return default


def get_float_env(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on any error."""
    try:
        return float(os.getenv(name, "").strip())
    except (ValueError, TypeError):
        return default


def get_bool_env(name: str, default: bool) -> bool:
    """Read a boolean env var (1/true/yes/on -> True; 0/false/no/off -> False)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Tuning knobs (documented in .env.example)
CHUNK_BATCH_SIZE: int = get_int_env("SCD_CHUNK_BATCH_SIZE", 12)
CHUNK_CHARS: int = get_int_env("SCD_CHUNK_CHARS", 3000)

# -- Speed / throughput tuning for the ledger (KPI extraction) -------------
# Number of worker threads used to call the LLM concurrently. Keep modest on
# the free tier so bursts don't trip rate limits (the limiter below also helps).
LLM_CONCURRENCY: int = max(1, get_int_env("SCD_LLM_CONCURRENCY", 4))
# Soft requests-per-minute cap across all workers (0 disables the limiter).
LLM_MAX_RPM: int = max(0, get_int_env("SCD_LLM_MAX_RPM", 15))
# Per-request network timeout in seconds. Spec default is 120s so a slow
# 503/504 + retry/backoff cycle has room to complete on flaky networks. Floor of
# 30s prevents a premature abort of a slow-but-successful first token. Override
# with SCD_LLM_TIMEOUT_SECONDS.
LLM_TIMEOUT_SECONDS: float = max(30.0, get_float_env("SCD_LLM_TIMEOUT_SECONDS", 120.0))
# Target characters of chunk text packed into a single batched LLM call. Larger
# = fewer calls. Only applied for the real (Gemini) provider; the offline demo
# keeps one chunk per call so its page numbers stay exact.
LEDGER_BATCH_CHARS: int = max(2000, get_int_env("SCD_LEDGER_BATCH_CHARS", 24000))
# Skip chunks that almost certainly contain no KPIs (TOC, legal boilerplate).
LEDGER_RELEVANCE_FILTER: bool = get_bool_env("SCD_LEDGER_RELEVANCE_FILTER", True)

# Cheap keyword vocabulary for the relevance filter. A chunk is considered
# KPI-relevant if it contains a number-ish token OR any of these financial
# signal words. Deliberately broad so we never drop a chunk that might hold a
# KPI; it only removes obvious boilerplate (disclaimers, TOC, signature pages).
KPI_SIGNAL_WORDS: tuple[str, ...] = (
    "revenue", "sales", "turnover", "income", "ebitda", "ebit", "pat", "profit",
    "margin", "volume", "growth", "crop", "crore", "lakh", "million", "billion",
    "tonne", "mt", "receivable", "inventory", "eps", "dividend", "capex",
    "market share", "gross", "operating", "net profit", "expense", "cost",
    "realisation", "realization", "asp", "yoy", "y-o-y", "qoq", "q-o-q",
    "rural", "urban", "quick commerce", "e-commerce", "distribution",
)

# Number of years of periods the disclosure ledger should span.
LEDGER_YEARS: int = 5

# Topic vocabulary for the narrative_vs_numbers detector. These are generic
# consumer/FMCG narrative themes; the detector matches them against call
# transcripts without any company-specific hard-coding.
NARRATIVE_TOPICS: list[str] = [
    "volume",
    "value",
    "premium",
    "rural",
    "urban",
    "quick commerce",
    "e-commerce",
    "modern trade",
    "general trade",
    "receivables",
    "inventory",
    "gross margin",
    "pricing",
    "commodity",
    "input cost",
    "distribution",
    "new products",
    "market share",
    "promotion",
]


# ---------------------------------------------------------------------------
# Per-company directory helpers
# ---------------------------------------------------------------------------
def slugify(name: str) -> str:
    """Turn a company name into a filesystem-safe slug.

    >>> slugify("Hindustan Unilever Limited")
    'hindustan_unilever_limited'
    """
    cleaned = []
    for ch in name.strip().lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in " -_.":
            cleaned.append("_")
    slug = "".join(cleaned)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def company_manual_dir(company: str) -> Path:
    return MANUAL_DIR / slugify(company)


def company_raw_dir(company: str) -> Path:
    return RAW_DIR / slugify(company)


def company_chunks_dir(company: str) -> Path:
    return CHUNKS_DIR / slugify(company)


def company_ledger_dir(company: str) -> Path:
    return LEDGER_DIR / slugify(company)


def company_out_dir(company: str) -> Path:
    return OUT_DIR / slugify(company)


def ensure_company_dirs(company: str) -> None:
    """Create every per-company working directory for ``company``."""
    _ensure_dirs()
    for maker in (
        company_manual_dir,
        company_raw_dir,
        company_chunks_dir,
        company_ledger_dir,
        company_out_dir,
    ):
        maker(company).mkdir(parents=True, exist_ok=True)


_ensure_dirs()
