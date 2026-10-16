"""Parse and order fiscal period labels.

Ledger periods arrive as free-text strings exactly as a company printed them
(e.g. ``"FY2024"``, ``"Q3 FY24"``, ``"FY25"``, ``"Q3FY24"``). Detectors need to
sort these chronologically and group quarters under their fiscal year, so we
normalise them here into a small structure.

Indian fiscal years end on 31 March, so ``FY24`` spans Apr-2023..Mar-2024 and
its quarters are Q1 (Apr-Jun) .. Q4 (Jan-Mar). ``FY24`` (annual) is treated as
coming *after* Q4 FY24 when sorting, because the annual figure is reported once
the full year is complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import arithmetic


@dataclass(frozen=True, order=True)
class Period:
    """A normalised, sortable fiscal period.

    ``fy`` is the 4-digit fiscal-year end (e.g. 2024 for FY24). ``quarter`` is
    1..4 for a quarter, or 5 for an annual/full-year period (so it sorts last
    within the year). ``sort_key`` gives a single monotonic integer.
    """

    fy: int
    quarter: int  # 1..4 for quarters, 5 for annual
    label: str

    @property
    def is_quarter(self) -> bool:
        return self.quarter <= 4

    @property
    def sort_key(self) -> int:
        return self.fy * 10 + self.quarter

    @property
    def fy_label(self) -> str:
        return f"FY{self.fy % 100:02d}"


def parse_period(raw: str | None) -> Period | None:
    """Parse a period label into a :class:`Period`, or ``None`` if unparseable."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("unknown", "n/a", "na", "-"):
        return None
    s = s.replace("_", " ").replace("-", " ")

    # Try to grab an explicit quarter.
    q = None
    m = re.search(r"\bq([1-4])\b", s, re.IGNORECASE)
    if m:
        q = int(m.group(1))

    # Fiscal year: "FY24", "fy 2024", "FY 2023-24", "Q3 FY24".
    fy = None
    m = re.search(r"\bfy\s*'?\s*(\d{2,4})(?:\s+(\d{2}))?\b", s, re.IGNORECASE)
    if m:
        fy = _normalise_year(int(m.group(1)))
        end2 = m.group(2)
        if end2 is not None:
            # "FY 2025-26": treat second number as end year only if it is
            # first_year + 1 (mod 100), per Indian convention (FY ends March).
            start2 = fy % 100
            if int(end2) == (start2 + 1) % 100:
                fy = fy - start2 + int(end2)
        if q is None:
            # Optional range "fy 2023-24" already handled by first token.
            q = 5  # treat bare FY as annual

    if fy is None:
        # A bare 4-digit calendar year, or a 2-digit like '24.
        # Range "2025-26" -> "2025 26": use end year only if second == first+1 mod 100.
        m = re.search(r"\b(20\d{2})\s+(\d{2})\b", s)
        if m:
            start = int(m.group(1))
            end2 = int(m.group(2))
            if end2 == (start % 100 + 1) % 100:
                fy = start - (start % 100) + end2
                q = 5
        if fy is None:
            m = re.search(r"\b(20\d{2})\b", s)
            if m:
                fy = _normalise_year(int(m.group(1)))
                q = q if q is not None else 5
            else:
                m = re.search(r"\b(\d{2})\b", s)
                if m:
                    two = int(m.group(1))
                    if 15 <= two <= 40:  # plausible FY short-code (FY15..FY40)
                        fy = 2000 + two
                        q = q if q is not None else 5

    if fy is None:
        return None
    if q is None:
        q = 5
    return Period(fy=fy, quarter=q, label=s.strip())


def _normalise_year(y: int) -> int:
    """Turn a 2- or 4-digit fiscal-year token into a 4-digit year."""
    if y >= 1000:
        return y
    return 2000 + y


def sort_periods(periods) -> list[str]:
    """Return the period labels sorted chronologically (unparseable last)."""
    def key(p: str):
        parsed = parse_period(p)
        if parsed is None:
            return (1, 0, p)
        return (0, parsed.sort_key, p)

    return sorted(set(periods), key=key)


def group_quarters_by_fy(periods) -> dict[str, list[str]]:
    """Group quarterly period labels under their fiscal-year label."""
    groups: dict[str, list[str]] = {}
    for p in periods:
        parsed = parse_period(p)
        if parsed is None or not parsed.is_quarter:
            continue
        groups.setdefault(parsed.fy_label, []).append(p)
    for fy in groups:
        groups[fy] = sort_periods(groups[fy])
    return groups
