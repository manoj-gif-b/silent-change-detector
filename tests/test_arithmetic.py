"""Unit tests for the pure arithmetic helpers in ``scd.arithmetic``.

These guard the "all arithmetic is done in Python, never by the LLM" rule:
every number the agent reports flows through these functions, so they must be
correct and robust to messy, missing and string-typed inputs.
"""

from __future__ import annotations

import math

import pytest

from scd import arithmetic as a


# ---------------------------------------------------------------------------
# pct_change
# ---------------------------------------------------------------------------
class TestPctChange:
    def test_positive_growth(self):
        assert a.pct_change(150, 100) == pytest.approx(50.0)

    def test_decline_is_negative(self):
        assert a.pct_change(80, 100) == pytest.approx(-20.0)

    def test_zero_base_is_nan(self):
        assert math.isnan(a.pct_change(10, 0))

    def test_none_inputs_are_nan(self):
        assert math.isnan(a.pct_change(None, 100))
        assert math.isnan(a.pct_change(10, None))

    def test_string_inputs_are_parsed(self):
        assert a.pct_change("150", "100") == pytest.approx(50.0)

    def test_negative_base_uses_abs(self):
        # Moving from -100 to -50 is a 50% improvement in magnitude.
        assert a.pct_change(-50, -100) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# yoy / qoq
# ---------------------------------------------------------------------------
class TestYoYQoQ:
    def test_yoy_four_quarters_back(self):
        series = [100, 110, 120, 130, 200]
        out = a.yoy(series, periods=4)
        assert all(math.isnan(x) for x in out[:4])
        # 200 vs 100 -> +100%
        assert out[4] == pytest.approx(100.0)

    def test_qoq_period_over_period(self):
        series = [100, 110, 121]
        out = a.qoq(series)
        assert math.isnan(out[0])
        assert out[1] == pytest.approx(10.0)
        assert out[2] == pytest.approx(10.0)

    def test_yoy_half_yearly(self):
        series = [100, 120, 200]
        out = a.yoy(series, periods=2)
        assert out[2] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# cagr
# ---------------------------------------------------------------------------
class TestCagr:
    def test_known_cagr(self):
        # 100 -> 200 over 3 years ~= 25.99%
        assert a.cagr(100, 200, 3) == pytest.approx(25.992, abs=1e-3)

    def test_flat_series_is_zero(self):
        assert a.cagr(100, 100, 5) == pytest.approx(0.0)

    def test_non_positive_inputs_are_nan(self):
        assert math.isnan(a.cagr(0, 100, 3))
        assert math.isnan(a.cagr(100, -5, 3))
        assert math.isnan(a.cagr(100, 200, 0))


# ---------------------------------------------------------------------------
# linear_slope
# ---------------------------------------------------------------------------
class TestLinearSlope:
    def test_rising_series_positive_slope(self):
        assert a.linear_slope([1, 2, 3, 4]) == pytest.approx(1.0)

    def test_falling_series_negative_slope(self):
        assert a.linear_slope([4, 3, 2, 1]) == pytest.approx(-1.0)

    def test_flat_series_zero_slope(self):
        assert a.linear_slope([5, 5, 5, 5]) == 0.0

    def test_too_few_points_is_nan(self):
        assert math.isnan(a.linear_slope([1]))

    def test_ignores_nan_in_middle(self):
        slope = a.linear_slope([1, 2, None, 4])
        assert slope == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# margins and ratios
# ---------------------------------------------------------------------------
class TestMargins:
    def test_margin_pct(self):
        assert a.margin_pct(30, 100) == pytest.approx(30.0)

    def test_margin_zero_base_is_nan(self):
        assert math.isnan(a.margin_pct(30, 0))

    def test_receivable_days_annual(self):
        # Receivables 100, revenue 1000 -> 100/1000*365 = 36.5 days
        assert a.receivable_days(100, 1000, 365) == pytest.approx(36.5)

    def test_receivable_days_quarterly_scaling(self):
        # Same ratio but scaled for a quarter -> ~9.1 days
        assert a.receivable_days(100, 1000, 90) == pytest.approx(9.0)

    def test_price_volume_split(self):
        # Revenue +10%, volume +5% -> realisation ~ +4.76%
        assert a.price_volume_split(10, 5) == pytest.approx(4.7619, abs=1e-3)


# ---------------------------------------------------------------------------
# dispersion / scoring
# ---------------------------------------------------------------------------
class TestDispersion:
    def test_mean_ignores_nan(self):
        assert a.mean([1, 2, None, 4]) == pytest.approx(7 / 3)

    def test_stdev_needs_two_points(self):
        assert math.isnan(a.stdev([5]))
        assert a.stdev([1, 2, 3]) > 0

    def test_zscore_basic(self):
        z = a.zscore(5, [1, 2, 3, 4])
        assert z > 1.9  # value above a tight cluster

    def test_zscore_no_spread_is_nan(self):
        assert math.isnan(a.zscore(5, [5, 5, 5]))

    def test_divergence_gap(self):
        assert a.divergence_gap(10, 7) == pytest.approx(3.0)

    def test_clamp_bounds(self):
        assert a.clamp(150, 0, 100) == 100
        assert a.clamp(-5, 0, 100) == 0
        assert math.isnan(a.clamp(float("nan"), 0, 100))

    def test_weighted_score_drops_missing_components(self):
        comps = {"a": 10, "b": math.nan, "c": 4}
        weights = {"a": 1, "b": 1, "c": 1}
        # b is NaN -> ignored; mean of a(10) and c(4) = 7
        assert a.weighted_score(comps, weights) == pytest.approx(7.0)

    def test_weighted_score_all_missing_is_nan(self):
        assert math.isnan(a.weighted_score({"a": math.nan}, {"a": 1}))
