"""PDF text extraction using PyMuPDF (``pymupdf``).

We only ever *read* text here; no model involvement. Each page becomes a
record so downstream chunking can carry an accurate page number for the
evidence trail (finding -> doc -> page -> quote).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class PageText:
    """Text extracted from a single PDF page."""

    page: int          # 1-based page number
    text: str          # extracted text for the page


def _open_document(pdf_path: Path):
    """Open a PDF with pymupdf, tolerating both the new and legacy names."""
    try:
        import pymupdf  # modern name
    except ImportError:  # pragma: no cover - fallback for older installs
        import fitz as pymupdf  # type: ignore
    return pymupdf.open(str(pdf_path))


def extract_pages(pdf_path: str | Path) -> list[PageText]:
    """Return the text of every page in ``pdf_path`` as :class:`PageText`.

    Pages with no extractable text (e.g. pure scans without an OCR layer) are
    returned with empty ``text`` rather than being dropped, so page numbers
    stay aligned with the original document.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    doc = _open_document(path)
    pages: list[PageText] = []
    try:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            # Normalise whitespace but keep newlines for readability.
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            pages.append(PageText(page=index + 1, text=text))
    finally:
        doc.close()
    return pages


def extract_text(pdf_path: str | Path) -> str:
    """Convenience helper: all pages joined with page-break markers."""
    pages = extract_pages(pdf_path)
    return "\n\n".join(f"[PAGE {p.page}]\n{p.text}" for p in pages)


def pages_to_dicts(pdf_path: str | Path) -> list[dict]:
    """Return pages as plain dicts (JSON-serialisable)."""
    return [asdict(p) for p in extract_pages(pdf_path)]
