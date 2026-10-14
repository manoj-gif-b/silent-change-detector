"""Split extracted PDF pages into retrieval-ready chunks.

Each chunk carries enough metadata to preserve the evidence trail:

``company``, ``doc_type`` (annual_report / quarterly / presentation /
transcript), ``period`` (e.g. ``FY2024`` / ``Q3 FY2024``), ``source_doc``
(the original filename) and ``page`` (1-based PDF page number).

Chunks are produced per page with a soft overlap between consecutive chunks of
the same page so that a KPI described across a line/pages boundary is unlikely
to be lost. A tiny chunk is not split further.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Iterable

from .config import CHUNK_CHARS


@dataclass
class Chunk:
    """A single chunk of text plus its provenance."""

    company: str
    doc_type: str
    period: str
    source_doc: str
    page: int
    text: str
    chunk_id: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    """Collapse runs of blank lines/space but keep paragraph breaks."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip() == "":
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def split_page_text(
    page_text: str,
    *,
    chunk_chars: int = CHUNK_CHARS,
    overlap: int = 250,
) -> list[str]:
    """Split one page's text into overlapping chunks.

    The split prefers paragraph boundaries; if a paragraph is itself longer
    than ``chunk_chars`` it is split on sentence boundaries. ``overlap`` chars
    from the tail of the previous chunk are prepended to the next.
    """
    text = _clean(page_text)
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    # First pass: paragraph segments.
    segments = [s.strip() for s in text.split("\n\n") if s.strip()]

    chunks: list[str] = []
    current = ""
    for seg in segments:
        # If a single segment is huge, break it into sentence-sized pieces.
        if len(seg) > chunk_chars:
            piece = _split_long_segment(seg, chunk_chars)
        else:
            piece = [seg]
        for part in piece:
            if not current:
                current = part
            elif len(current) + 2 + len(part) <= chunk_chars:
                current = f"{current}\n\n{part}"
            else:
                chunks.append(current)
                # Start the next chunk with a tail overlap for continuity.
                tail = current[-overlap:] if overlap and len(current) > overlap else ""
                current = f"{tail}\n\n{part}" if tail else part
    if current.strip():
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]


def _split_long_segment(segment: str, chunk_chars: int) -> list[str]:
    """Break an over-long segment on sentence boundaries, then hard-split."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", segment)
    pieces: list[str] = []
    buf = ""
    for sent in sentences:
        if len(sent) > chunk_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            # Hard split the oversized sentence.
            for i in range(0, len(sent), chunk_chars):
                pieces.append(sent[i : i + chunk_chars])
            continue
        if not buf:
            buf = sent
        elif len(buf) + 1 + len(sent) <= chunk_chars:
            buf = f"{buf} {sent}"
        else:
            pieces.append(buf)
            buf = sent
    if buf.strip():
        pieces.append(buf)
    return pieces


def build_chunks(
    pages: Iterable,
    *,
    company: str,
    doc_type: str,
    period: str,
    source_doc: str,
    chunk_chars: int = CHUNK_CHARS,
) -> list[Chunk]:
    """Turn :class:`~scd.pdf.PageText` items into a flat list of chunks."""
    chunks: list[Chunk] = []
    counter = 0
    for page in pages:
        page_no = getattr(page, "page", None)
        page_text = getattr(page, "text", "")
        if page_no is None and isinstance(page, dict):
            page_no = page.get("page")
            page_text = page.get("text", "")
        for text in split_page_text(page_text, chunk_chars=chunk_chars):
            counter += 1
            chunks.append(
                Chunk(
                    company=company,
                    doc_type=doc_type,
                    period=period,
                    source_doc=source_doc,
                    page=int(page_no),
                    text=text,
                    chunk_id=f"{source_doc}::p{page_no}::c{counter}",
                )
            )
    return chunks
