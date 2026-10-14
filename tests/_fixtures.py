"""Test helpers for the Silent Change Detector test-suite.

Provides a tiny synthetic-PDF builder (via PyMuPDF) so ingest/ledger can be
exercised without shipping binary fixtures. Not collected by pytest (leading
underscore in the filename).
"""

from __future__ import annotations

from pathlib import Path


def make_pdf(path: str | Path, pages: dict[int, str]) -> Path:
    """Create a real PDF at ``path`` with the given ``{page_number: text}``.

    Uses PyMuPDF to insert plain text so extraction round-trips cleanly.
    """
    import pymupdf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    max_page = max(pages) if pages else 0
    for i in range(1, max_page + 1):
        page = doc.new_page()
        text = pages.get(i, "")
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path
