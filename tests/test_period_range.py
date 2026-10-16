"""Tests for fiscal-period range parsing (end-year convention).

A range such as "2025-26" or "FY 2025-26" normalises to the same Period as its
FY equivalent ("FY26"), per Indian convention where the fiscal year ends 31 March.
"""
from __future__ import annotations

from ingest import infer_period
from scd.periods import parse_period


def test_2025_26_equals_FY26():
    assert parse_period("2025-26").sort_key == parse_period("FY26").sort_key == parse_period("FY 2025-26").sort_key


def test_2024_25_equals_FY25():
    assert parse_period("2024-25").sort_key == parse_period("FY25").sort_key == parse_period("FY 2024-25").sort_key


def test_2025_10_15_does_not_give_fy2010():
    assert parse_period("2025-10-15").fy != 2010


def test_infer_period_uses_document_text_first():
    cover_text = (
        "Date: 16 July 2026\n\n"
        "Dear Sir/Madam,\n\n"
        "Sub:\nAnnual Report for the Financial Year 2025-26 and Notice of the "
        "107th Annual General Meeting\n\n"
        "The Annual Report for FY 2025-26 ..."
    )
    assert infer_period("annual_report_FY25.pdf", cover_text) == "FY26"


def test_infer_period_filename_fallback():
    assert infer_period("annual_report_FY25.pdf", "") == "FY25"
