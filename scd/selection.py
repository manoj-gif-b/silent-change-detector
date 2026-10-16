"""Chunk pre-selection for the ledger (KPI extraction) stage.

Motivation: a NIFTY-50 annual report runs ~150-250 pages, but only a handful
of sections repeat the KPIs the pipeline needs (MD&A, segment information,
financial highlights, the shareholder letter, and the notes on trade
receivables and revenue). Sending every page to the LLM makes a real run slow
and expensive and floods the ledger with boilerplate quotes.

Contract
--------
* **Nothing is dropped from the page index.** Ingest still writes one chunk
  record for every page of every PDF (``data/chunks/<company>/chunks.json``),
  so every later citation keeps its true 1-based PDF page number. This module
  only chooses which of those existing chunks are *sent to the LLM*.
* **Deterministic and explainable**: plain regex/keyword scoring, no LLM.
* Every skipped chunk carries a machine-readable reason so the ledger stage can
  log why selection removed it.
* Deliberately biased towards KEEPING: a false keep costs one extra API call, a
  false drop could lose evidence. Only unambiguous boilerplate (contents page,
  cover, disclaimer, registered-office page) is dropped on a single signal.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

# ---------------------------------------------------------------------------
# Strong positive signals: named sections that reliably repeat KPIs
# ---------------------------------------------------------------------------
# Each entry is (reason_label, compiled-pattern). Matched case-insensitively
# against the chunk text.
STRONG_SECTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("management_discussion", re.compile(
        r"management discussion|md\s*&\s*a|management'?s discussion and analysis",
        re.I)),
    ("segment_information", re.compile(
        r"segment (?:information|revenue|results|analysis)|reportable segment|"
        r"business segment|segment-wise|segment wise", re.I)),
    ("financial_highlights", re.compile(
        r"financial highlights|performance highlights|key financial (?:indicators|metrics|figures)",
        re.I)),
    ("shareholder_letter", re.compile(
        r"letter to (?:the )?shareholders|dear shareholder|chairman'?s statement|"
        r"dear stakeholder|to our stakeholders", re.I)),
    ("trade_receivables", re.compile(r"trade receivable", re.I)),
    ("revenue_note", re.compile(
        r"revenue from operations|disaggregation of revenue|revenue recognition",
        re.I)),
    ("financial_statements", re.compile(
        r"statement of profit and loss|balance sheet|cash flow statement|"
        r"notes to (?:the )?(?:consolidated )?financial statements", re.I)),
    ("financial_results", re.compile(
        r"financial results|quarter ended|half year ended|year ended", re.I)),
)

# ---------------------------------------------------------------------------
# Hard boilerplate: unambiguously KPI-free pages. Checked BEFORE the strong
# sections so a contents page that merely *lists* "Financial Results" is still
# skipped. One match is enough to skip.
# ---------------------------------------------------------------------------
HARD_BOILERPLATE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("table_of_contents", re.compile(r"table of contents|^\s*contents\s*$", re.I | re.M)),
    ("disclaimer", re.compile(
        r"forward[- ]looking statements?|disclaimer|without any warranties|"
        r"does not constitute (?:an )?(?:offer|solicitation|recommendation)", re.I)),
    ("registered_office", re.compile(
        r"corporate identity number|registered office|corporate information|"
        r"corporate office", re.I)),
    ("legal_clause", re.compile(
        r"indemnif(?:y|ication|ity)|governing law|arbitration clause|exclusive jurisdiction",
        re.I)),
    ("copyright_terms", re.compile(
        r"all rights reserved|terms of (?:use|service)|privacy policy", re.I)),
)

# A page whose entire text is basically the title (cover / divider page).
COVER_PATTERN = re.compile(r"annual report|integrated report|financial statements", re.I)
_MIN_COVER_CHARS = 220

# ---------------------------------------------------------------------------
# Soft negatives: formalities / long legal sections. A single occurrence is
# too weak (MD&A pages mention "the board" too), so we skip only when at least
# TWO distinct markers are present and no strong section matched.
# ---------------------------------------------------------------------------
FORMALITY_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bappointment\b|\bappoint(?:ed|ing)\b", re.I),
    re.compile(r"\bratif(?:y|ication|ied)\b", re.I),
    re.compile(r"\bremuneration\b|\bsitting fee", re.I),
    re.compile(r"vacation of office|terms of (?:office|reference)", re.I),
    re.compile(r"secretarial (?:standard|audit)|compliance with (?:the )?provisions", re.I),
    re.compile(r"declaration|hereby certify|true and correct", re.I),
    re.compile(r"register of members|maintained under the provisions", re.I),
)

LEGAL_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"save as (?:provided|stated)|without prejudice", re.I),
    re.compile(r"nothing contained herein|to the maximum extent permitted", re.I),
    re.compile(r"binding (?:upon|on)|shall be (?:governed|construed)", re.I),
    re.compile(r"rules and regulations|frame(?:work)? notified", re.I),
    re.compile(r"pursuant to (?:section|sub[- ]section|rule)", re.I),
)

# ---------------------------------------------------------------------------
# KPI vocabulary (reuses scd.config.KPI_SIGNAL_WORDS) + number detection
# ---------------------------------------------------------------------------
def _compile_signal(word: str) -> re.Pattern[str]:
    # Short tokens need word boundaries so "mt" doesn't match inside "amount"
    # or "pat" inside "compatible". Longer words are matched as substrings so
    # "crores"/"margins" still count.
    if len(word) <= 4 and " " not in word:
        return re.compile(rf"\b{re.escape(word)}\b", re.I)
    return re.compile(re.escape(word), re.I)


def _load_kpi_patterns() -> tuple[re.Pattern[str], ...]:
    from .config import KPI_SIGNAL_WORDS
    return tuple(_compile_signal(w) for w in KPI_SIGNAL_WORDS)


_KPI_PATTERNS: tuple[re.Pattern[str], ...] | None = None

_NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%?")


def _kpi_patterns() -> tuple[re.Pattern[str], ...]:
    global _KPI_PATTERNS
    if _KPI_PATTERNS is None:
        _KPI_PATTERNS = _load_kpi_patterns()
    return _KPI_PATTERNS


def has_number(text: str) -> bool:
    """True if the text contains at least one digit-bearing token."""
    return bool(_NUMBER_PATTERN.search(text))


def has_kpi_word(text: str) -> bool:
    """True if the text contains any configured financial signal word."""
    return any(p.search(text) for p in _kpi_patterns())


def _marker_count(text: str, markers: Iterable[re.Pattern[str]]) -> int:
    """Count DISTINCT marker patterns that match (one vote per marker)."""
    return sum(1 for m in markers if m.search(text))


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def evaluate_chunk(chunk: dict) -> tuple[bool, str]:
    """Decide whether ``chunk`` should be sent to the LLM.

    Returns ``(keep, reason)`` where reason is machine-readable, e.g.
    ``section:segment_information``, ``boilerplate:table_of_contents``,
    ``kpi_signal``, ``boilerplate:directors_report_formalities`` or
    ``no_kpi_signal``.
    """
    text = chunk.get("text") or ""
    if not text.strip():
        return False, "empty"

    # 1. Unambiguous boilerplate first (contents pages *list* section names).
    for label, pattern in HARD_BOILERPLATE:
        if pattern.search(text):
            return False, f"boilerplate:{label}"
    # Cover / divider page: short text that is basically just the title.
    if len(text) < _MIN_COVER_CHARS and COVER_PATTERN.search(text):
        return False, "boilerplate:cover"

    # 2. Named KPI-bearing sections are always kept.
    for label, pattern in STRONG_SECTIONS:
        if pattern.search(text):
            return True, f"section:{label}"

    # 3. Directors'-report formalities / long legal prose: skip only when at
    #    least two distinct markers agree (a single hit is too weak).
    if _marker_count(text, FORMALITY_MARKERS) >= 2:
        return False, "boilerplate:directors_report_formalities"
    if _marker_count(text, LEGAL_MARKERS) >= 2:
        return False, "boilerplate:legal_section"

    # 4. Transcript / investor-presentation prose is kept on vocabulary alone
    #    (margin commentary often carries no digit on the quoted page).
    if chunk.get("doc_type") in ("transcript", "presentation") and has_kpi_word(text):
        return True, "doc_type:qualitative_commentary"

    # 5. Generic rule: financial vocabulary + an actual number.
    if has_number(text) and has_kpi_word(text):
        return True, "kpi_signal"

    return False, "no_kpi_signal"


def select_kpi_chunks(
    chunks: list[dict],
    *,
    enabled: bool = True,
) -> tuple[list[dict], dict]:
    """Split ``chunks`` into (selected, stats). Input order is preserved.

    ``stats`` summarises what happened so the caller can log it::

        {"total": N, "kept": K, "skipped": S,
         "skip_reasons": {reason: count, ...}, "kept_reasons": {...},
         "kept_by_doc": {source_doc: count, ...}}
    """
    if not enabled:
        return list(chunks), {
            "total": len(chunks), "kept": len(chunks), "skipped": 0,
            "skip_reasons": {}, "kept_reasons": {"unfiltered": len(chunks)},
            "kept_by_doc": {},
        }

    selected: list[dict] = []
    skip_reasons: Counter[str] = Counter()
    kept_reasons: Counter[str] = Counter()
    kept_by_doc: Counter[str] = Counter()

    for chunk in chunks:
        keep, reason = evaluate_chunk(chunk)
        if keep:
            selected.append(chunk)
            kept_reasons[reason] += 1
            kept_by_doc[chunk.get("source_doc") or "unknown"] += 1
        else:
            skip_reasons[reason] += 1

    stats = {
        "total": len(chunks),
        "kept": len(selected),
        "skipped": len(chunks) - len(selected),
        "skip_reasons": dict(skip_reasons),
        "kept_reasons": dict(kept_reasons),
        "kept_by_doc": dict(kept_by_doc),
    }
    return selected, stats


def format_stats(stats: dict) -> str:
    """One-line human summary of a selection run, for logging."""
    parts = [f"kept {stats['kept']}/{stats['total']} chunks"]
    if stats.get("skipped"):
        top = sorted(stats["skip_reasons"].items(), key=lambda kv: -kv[1])[:3]
        detail = ", ".join(f"{k} x{v}" for k, v in top)
        parts.append(f"skipped {stats['skipped']} ({detail})")
    return "; ".join(parts)