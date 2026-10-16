"""Unit tests for the detectors' ledger-text normalisation.

Guards the fix for ``'float' object has no attribute 'strip'``: ledger text
fields (definition/unit) can be float NaN (empty CSV cell), a plain number,
an empty string, or real wording -- none of them may crash
``detect_vanished_or_redefined``, and valid text must compare exactly as the
old ``(v or '').strip().lower()`` idiom did.
"""

from __future__ import annotations

import pandas as pd

from detectors import _norm_text, detect_vanished_or_redefined


def _ledger_row(period: str, definition, name="Test KPI", unit="%") -> dict:
    return {
        "company": "Britannia Industries Limited",
        "period": period,
        "name": name,
        "value": "10",
        "unit": unit,
        "definition": definition,
        "doc_type": "annual_report",
        "source_doc": "annual_report_FY25.pdf",
        "page": 9,
        "quote": "Test KPI was 10.",
    }


class TestNormText:
    def test_float_nan_is_missing(self):
        assert _norm_text(float("nan")) == ""

    def test_none_is_missing(self):
        assert _norm_text(None) == ""

    def test_float_number_is_stringified(self):
        assert _norm_text(42.0) == "42.0"

    def test_empty_string_stays_empty(self):
        assert _norm_text("") == ""

    def test_whitespace_only_is_empty(self):
        assert _norm_text("   ") == ""

    def test_valid_text_stripped_and_lowered(self):
        assert _norm_text("  People  ") == "people"

    def test_valid_text_unchanged_for_comparison(self):
        # Same result as the original (v or '').strip().lower() for real text.
        v = "Building E-commerce Salience"
        assert _norm_text(v) == (v or "").strip().lower()


class TestDetectVanishedOrRedefinedRobustness:
    def test_mixed_definition_types_do_not_raise(self):
        # float NaN, float number, empty string and normal text in one group
        # -- this mirrors what the real Britannia ledger produced.
        ledger = pd.DataFrame([
            _ledger_row("FY21", float("nan")),
            _ledger_row("FY22", 42.0),
            _ledger_row("FY23", ""),
            _ledger_row("FY24", "Normal wording for the KPI"),
        ])
        result = detect_vanished_or_redefined(ledger, "Britannia Industries Limited")
        assert isinstance(result, list)

    def test_nan_unit_row_does_not_raise(self):
        # The exact crash shape from the Britannia run: Headcount row with an
        # empty unit cell -> float NaN after pd.read_csv.
        ledger = pd.DataFrame([
            _ledger_row("FY23", "People", unit=float("nan")),
            _ledger_row("FY24", "People", unit="%"),
        ])
        result = detect_vanished_or_redefined(ledger, "Britannia Industries Limited")
        assert isinstance(result, list)

    def test_empty_ledger_is_safe(self):
        assert detect_vanished_or_redefined(pd.DataFrame(), "X") == []


class TestDetectVanishedSortKeyIdentity:
    """Same sort_key (e.g. "2025-26" and "FY26") must count as the same period."""

    def test_no_anomaly_when_continuously_reported(self):
        # Metric A reported for 2021-22 .. 2025-26. Metric B reported only for FY26
        # (same sort_key as 2025-26). Metric A covers every period in the ledger,
        # so there is NO vanished anomaly for Metric A.
        ledger = pd.DataFrame([
            _ledger_row("2021-22", "Metric A def", name="Metric A"),
            _ledger_row("2022-23", "Metric A def", name="Metric A"),
            _ledger_row("2023-24", "Metric A def", name="Metric A"),
            _ledger_row("2024-25", "Metric A def", name="Metric A"),
            _ledger_row("2025-26", "Metric A def", name="Metric A"),
            _ledger_row("FY26", "Metric B def", name="Metric B"),
        ])
        result = detect_vanished_or_redefined(ledger, "Test Company")
        metric_a = [a for a in result if a["numbers"]["kpi"] == "Metric A"]
        assert metric_a == [], f"Expected no anomaly for Metric A, got: {metric_a}"

    def test_anomaly_when_metric_disappears(self):
        # Metric A reported for 2021-22 .. 2023-24 only. Metric B reported for
        # 2025-26 (the latest period). Metric A is absent in the latest period,
        # so it MUST be flagged with "absent since".
        ledger = pd.DataFrame([
            _ledger_row("2021-22", "Metric A def", name="Metric A"),
            _ledger_row("2022-23", "Metric A def", name="Metric A"),
            _ledger_row("2023-24", "Metric A def", name="Metric A"),
            _ledger_row("2025-26", "Metric B def", name="Metric B"),
        ])
        result = detect_vanished_or_redefined(ledger, "Test Company")
        metric_a = [a for a in result if a["numbers"]["kpi"] == "Metric A"]
        assert len(metric_a) == 1, f"Expected 1 anomaly for Metric A, got: {metric_a}"
        assert "absent since" in metric_a[0]["title"], metric_a[0]["title"]
