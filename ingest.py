"""Milestone 1a -- ingest.

Responsibility
--------------
Given a company name, gather its public documents (annual reports, quarterly
results, investor presentations, earnings-call transcripts), parse them with
PyMuPDF and split them into metadata-tagged chunks saved under
``data/chunks/<company>/``.

Sources, in priority order
--------------------------
1. **Manual drop-in folder** ``data/manual/<company>/`` -- the reliable path.
   Drop any PDFs here; their filename is used to infer document type and
   period. This exists precisely because IR websites and exchange portals often
   block automated scripts.
2. **Automatic download** (optional, ``enable_auto=True``) -- a best-effort
   fetcher that downloads from explicit public URLs you register, with all
   responses cached. It never relies on fragile crawling and degrades quietly
   if a host blocks us.

Nothing here calls the LLM. Period/type inference is deterministic heuristics.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from scd import cache
from scd.config import (
    company_chunks_dir,
    company_manual_dir,
    company_raw_dir,
    slugify as _slug,
    ensure_company_dirs,
)
from scd.chunking import Chunk, build_chunks
from scd.pdf import extract_pages

logger = logging.getLogger("scd.ingest")

# Document-type vocabulary used across the pipeline.
DOC_TYPES = ("annual_report", "quarterly", "presentation", "transcript", "other")

# Known investor-relations / announcement sources for NIFTY-50 FMCG names.
# These are only *hints* for the optional auto-downloader; the manual folder is
# always preferred and the auto path is fully optional. No results are hard-coded.
IR_SOURCES: dict[str, list[str]] = {
    # slug -> list of public URL templates. Kept intentionally small and safe.
    # Add entries here if you want the auto-downloader to try them.
}


# ---------------------------------------------------------------------------
# Type / period inference (deterministic, filename + content based)
# ---------------------------------------------------------------------------
def infer_doc_type(filename: str, text_sample: str = "") -> str:
    """Guess the document type from its filename (then a text sample)."""
    name = filename.lower()
    sample = text_sample.lower()

    def has(*needles: str, hay: str) -> bool:
        return any(n in hay for n in needles)

    # Transcript signals.
    if has("transcript", "earnings call", "concall", "con-call", "call transcript", hay=name):
        return "transcript"
    if has("earnings call", "conference call", "management discussion", hay=sample):
        return "transcript"
    # Investor presentation signals.
    if has("investor", "presentation", "ppt", hay=name):
        return "presentation"
    # Quarterly results signals.
    if has("quarterly", "q1", "q2", "q3", "q4", "results", "quarter", hay=name):
        return "quarterly"
    if has("quarter ended", "three months", "nine months", "six months", hay=sample):
        return "quarterly"
    # Annual report signals.
    if has("annual report", "annualreport", "integrated report", hay=name):
        return "annual_report"
    if has("annual report", "financial year", "board's report", "boards report", hay=sample):
        return "annual_report"
    return "other"


_MONTHS = {
    "jan": "Q4", "feb": "Q4", "mar": "Q4",
    "apr": "Q1", "may": "Q1", "jun": "Q1",
    "jul": "Q2", "aug": "Q2", "sep": "Q2",
    "oct": "Q3", "nov": "Q3", "dec": "Q3",
}


def _fy_year_from_label(match_year: int, quarter: str | None) -> str:
    """Build a canonical period label from a year and optional quarter."""
    if quarter:
        return f"{quarter} FY{match_year % 100:02d}"
    return f"FY{match_year % 100:02d}"


def infer_period(filename: str, text_sample: str = "") -> str:
    """Best-effort canonical period label (e.g. ``FY24`` or ``Q3 FY24``).

    Indian fiscal years end March 31, so a year labelled FY24 spans Apr-2023 to
    Mar-2024. We try the document text first (the report's own words are
    authoritative), then the filename, and finally fall back to "unknown".
    """
    for hay in (text_sample.lower(), filename.lower()):
        if not hay:
            continue
        # Normalise common separators so "Q3_FY2024", "q3-fy24" and "Q3 FY24" match.
        h = hay.replace("_", " ").replace("-", " ")

        # Pattern 1: explicit "Q3 FY24" / "q3 fy2024".
        m = re.search(r"\bq([1-4])\s*(?:fy)?\s*'?(\d{2,4})\b", h)
        if m:
            q = f"Q{m.group(1)}"
            yr = int(m.group(2))
            return _fy_year_from_label(yr, q)

        # Pattern 2: "FY24" / "fy 2023-24" / "fy2024".
        m = re.search(r"\bfy\s*'?(\d{2,4})(?:\s*[/ ]\s*(\d{2}))?\b", h)
        if m:
            yr = int(m.group(1))
            # For a range like 2023-24 the fiscal year label uses the ending year.
            if m.group(2):
                end2 = int(m.group(2))
                start2 = yr % 100
                yr = yr - start2 + end2
            return _fy_year_from_label(yr, None)

        # Pattern 3: a bare 4-digit year (e.g. "annual_report_2024.pdf").
        m = re.search(r"\b(20\d{2})\b", h)
        if m:
            return _fy_year_from_label(int(m.group(1)), None)

        # Pattern 4: month name near a year -> quarter (fiscal).
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(20\d{2})\b", h)
        if m:
            q = _MONTHS[m.group(1)]
            return _fy_year_from_label(int(m.group(2)), q)

    return "unknown"


# ---------------------------------------------------------------------------
# Document gathering
# ---------------------------------------------------------------------------
def gather_manual_pdfs(company: str) -> list[Path]:
    """Return all PDFs dropped into ``data/manual/<company>/`` (recursively).

    The folder name is matched by slug, so ``data/manual/Hindustan Unilever
    Limited`` and ``data/manual/hindustan_unilever_limited`` both resolve to the
    same company. All subfolders whose slug matches are scanned, and PDFs are
    collected from every one that contains them (an auto-created empty slug
    folder never shadows a real, differently-cased folder).
    """
    slug = _slug(company)
    manual_root = company_manual_dir(company).parent
    if not manual_root.exists():
        return []
    folders = [sub for sub in manual_root.iterdir() if sub.is_dir() and _slug(sub.name) == slug]
    pdfs: list[Path] = []
    seen: set[Path] = set()
    for folder in folders:
        for p in sorted(folder.rglob("*.pdf")):
            if p.is_file() and p.resolve() not in seen:
                seen.add(p.resolve())
                pdfs.append(p)
    return pdfs


def auto_download(company: str, urls: Iterable[str] | None = None) -> list[Path]:
    """Best-effort download of PDFs from explicit public URLs.

    Only URLs explicitly supplied (or registered in ``IR_SOURCES``) are fetched.
    Every download is cached under ``cache/files`` and copied into
    ``data/raw/<company>/``. Failures (network blocks, 403, timeouts) are logged
    and skipped so the pipeline still runs on whatever is available.
    """
    import requests  # local import; optional dependency path

    slug = _slug(company)
    candidates = list(urls or IR_SOURCES.get(slug, []))
    if not candidates:
        logger.info("No auto-download URLs registered for %s; using manual folder only.", company)
        return []

    raw_dir = company_raw_dir(company)
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    headers = {"User-Agent": "Mozilla/5.0 (research; Silent Change Detector)"}
    for url in candidates:
        try:
            cached = cache.get_file(url, ext="pdf")
            data = cached if cached is not None else _fetch(url, headers, requests)
            if cached is None and data is not None:
                cache.set_file(data, url, ext="pdf")
            if not data:
                continue
            fname = _filename_from_url(url)
            dest = raw_dir / fname
            # Avoid clobbering an existing distinct file with the same name.
            if dest.exists() and dest.read_bytes() == data:
                saved.append(dest)
                continue
            n = 1
            base = dest.stem
            while dest.exists():
                dest = raw_dir / f"{base}_{n}.pdf"
                n += 1
            dest.write_bytes(data)
            saved.append(dest)
            logger.info("Downloaded %s -> %s", url, dest.name)
        except Exception as exc:  # never let one bad URL abort the run
            logger.warning("Auto-download failed for %s: %s", url, exc)
    return saved


def _fetch(url: str, headers: dict, requests_mod) -> bytes | None:
    resp = requests_mod.get(url, headers=headers, timeout=30)
    if resp.status_code != 200 or not resp.content:
        logger.warning("Skipping %s (HTTP %s)", url, resp.status_code)
        return None
    return resp.content


def _filename_from_url(url: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    # Sanitise to a safe filename.
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "document.pdf"


# ---------------------------------------------------------------------------
# Parse + chunk
# ---------------------------------------------------------------------------
def _text_sample(text: str, limit: int = 4000) -> str:
    """First ``limit`` chars of concatenated page text (for inference)."""
    return text[:limit]


def process_pdf(pdf_path: Path, company: str, *, chunk_chars: int | None = None) -> list[Chunk]:
    """Parse one PDF into chunks, inferring its type and period."""
    pages = extract_pages(pdf_path)
    joined = "\n".join(p.text for p in pages)
    doc_type = infer_doc_type(pdf_path.name, _text_sample(joined))
    period = infer_period(pdf_path.name, _text_sample(joined))
    logger.info(
        "Parsed %-40s type=%-13s period=%-10s pages=%d",
        pdf_path.name, doc_type, period, len(pages),
    )
    kwargs = {} if chunk_chars is None else {"chunk_chars": chunk_chars}
    return build_chunks(
        pages,
        company=company,
        doc_type=doc_type,
        period=period,
        source_doc=pdf_path.name,
        **kwargs,
    )


def ingest_company(
    company: str,
    *,
    enable_auto: bool = False,
    auto_urls: Iterable[str] | None = None,
    chunk_chars: int | None = None,
) -> dict:
    """Full ingest for ``company``; returns a summary and writes chunks to disk.

    Steps: ensure dirs -> gather manual (+ optional auto) PDFs -> parse & chunk
    each -> write ``chunks.json`` and ``manifest.json`` under
    ``data/chunks/<company>/``.
    """
    ensure_company_dirs(company)

    pdfs = gather_manual_pdfs(company)
    logger.info("Found %d manual PDF(s) for %s", len(pdfs), company)

    if enable_auto:
        downloaded = auto_download(company, auto_urls)
        if downloaded:
            # Merge, avoiding duplicates already present in the manual folder.
            manual_names = {p.name for p in pdfs}
            pdfs += [p for p in downloaded if p.name not in manual_names]

    if not pdfs:
        logger.warning(
            "No PDFs found for '%s'. Drop files into %s and rerun, "
            "or enable auto-download with explicit URLs.",
            company, company_manual_dir(company),
        )

    all_chunks: list[Chunk] = []
    docs_meta: list[dict] = []
    for pdf in pdfs:
        try:
            chunks = process_pdf(pdf, company, chunk_chars=chunk_chars)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", pdf.name, exc)
            continue
        all_chunks.extend(chunks)
        docs_meta.append(
            {
                "source_doc": pdf.name,
                "doc_type": chunks[0].doc_type if chunks else "other",
                "period": chunks[0].period if chunks else "unknown",
                "n_chunks": len(chunks),
                "path": str(pdf),
            }
        )

    out_dir = company_chunks_dir(company)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / "chunks.json"
    manifest_path = out_dir / "manifest.json"

    chunks_path.write_text(
        json.dumps([c.to_dict() for c in all_chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest = {
        "company": company,
        "n_documents": len(docs_meta),
        "n_chunks": len(all_chunks),
        "documents": docs_meta,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Wrote %d chunk(s) across %d document(s) -> %s", len(all_chunks), len(docs_meta), chunks_path)
    return manifest


def load_chunks(company: str) -> list[dict]:
    """Load previously-ingested chunks for ``company`` from disk."""
    chunks_path = company_chunks_dir(company) / "chunks.json"
    if not chunks_path.exists():
        return []
    return json.loads(chunks_path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # tiny manual entrypoint for debugging
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    name = sys.argv[1] if len(sys.argv) > 1 else "Hindustan Unilever Limited"
    result = ingest_company(name, enable_auto=False)
    print(json.dumps(result, indent=2))


