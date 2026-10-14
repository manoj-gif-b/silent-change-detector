"""Pure arithmetic / statistics helpers.

RULE: every number that appears in the final report is computed here using
pandas/numpy. The LLM never does arithmetic -- it only extracts text, labels
periods, and phrases prose. Keeping the math here makes it deterministic,
testable and auditable.

All functions accept plain Python scalars, lists or pandas Series and are
written to be robust to missing values (NaN/None) so they can be dropped
straight onto scraped data.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Basic guards
# ---------------------------------------------------------------------------
def _to_float(x) -> float:
    """Best-effort conversion to float; returns NaN when not possible."""
    if x is None:
        return math.nan
    if isinstance(x, (int, float)):
        return float(x)
    try:
        # Strip common thousands separators / currency symbols / percent signs.
        s = str(x).strip().replace(",", "").replace("\u20b9", "").replace("₹", "")
        s = s.replace("%", "").strip()
        if s in ("", "-", "--", "NA", "N/A", "na", "nan", "None"):
            return math.nan
        return float(s)
    except (ValueError, TypeError):
        return math.nan


# ---------------------------------------------------------------------------
# Growth and change
# ---------------------------------------------------------------------------
def pct_change(new: float, old: float) -> float:
    """Percentage change from ``old`` to ``new``.

    Returns NaN when ``old`` is 0/NaN or the result is not finite. This is the
    safest primitive for period-over-period moves.
    """
    new = _to_float(new)
    old = _to_float(old)
    if math.isnan(new) or math.isnan(old) or old == 0:
        return math.nan
    result = (new - old) / abs(old) * 100.0
    return result if math.isfinite(result) else math.nan


def yoy(series: Sequence[float], periods: int = 4) -> list[float]:
    """Year-over-year percent change for a series (default: 4 quarters back).

    Works for both quarterly (periods=4) and half-yearly (periods=2) series.
    Leading positions that have no year-ago comparison become NaN.
    """
    s = pd.Series([_to_float(v) for v in series], dtype="float64")
    out: list[float] = [math.nan] * len(s)
    for i in range(periods, len(s)):
        out[i] = pct_change(s.iloc[i], s.iloc[i - periods])
    return out


def qoq(series: Sequence[float]) -> list[float]:
    """Quarter-over-quarter percent change (period-over-period)."""
    s = pd.Series([_to_float(v) for v in series], dtype="float64")
    out: list[float] = [math.nan] * len(s)
    for i in range(1, len(s)):
        out[i] = pct_change(s.iloc[i], s.iloc[i - 1])
    return out


def cagr(begin: float, end: float, years: float) -> float:
    """Compound annual growth rate in percent over ``years``.

    Returns NaN unless both endpoints are positive and years > 0.
    """
    begin = _to_float(begin)
    end = _to_float(end)
    years = _to_float(years)
    if math.isnan(begin) or math.isnan(end) or math.isnan(years):
        return math.nan
    if begin <= 0 or end <= 0 or years <= 0:
        return math.nan
    result = ((end / begin) ** (1.0 / years) - 1.0) * 100.0
    return result if math.isfinite(result) else math.nan


def linear_slope(series: Sequence[float]) -> float:
    """Least-squares slope of a series vs. its index.

    Positive = rising trend, negative = falling trend. NaN when fewer than two
    finite points exist. This is used to quantify a multi-quarter trend without
    relying on any single endpoint.
    """
    s = pd.Series([_to_float(v) for v in series], dtype="float64")
    s = s.dropna()
    if len(s) < 2:
        return math.nan
    x = np.arange(len(s), dtype="float64")
    y = s.to_numpy(dtype="float64")
    # Guard against a degenerate (all-equal) series.
    if np.allclose(y, y[0]):
        return 0.0
    slope = np.polyfit(x, y, 1)[0]
    return float(slope) if math.isfinite(slope) else math.nan


# ---------------------------------------------------------------------------
# Financial ratios
# ---------------------------------------------------------------------------
def margin_pct(value: float, base: float) -> float:
    """``value`` as a percentage of ``base`` (e.g. gross margin %)."""
    value = _to_float(value)
    base = _to_float(base)
    if math.isnan(value) or math.isnan(base) or base == 0:
        return math.nan
    result = value / abs(base) * 100.0
    return result if math.isfinite(result) else math.nan


def receivable_days(receivables: float, revenue: float, days: float = 365.0) -> float:
    """Days sales outstanding: receivables / revenue * days.

    Either ``receivables`` (a balance-sheet stock) or ``revenue`` may be for a
    quarter or a year -- the caller must be consistent about the ``days``
    scaling (365 for annual revenue, ~90 for quarterly).
    """
    receivables = _to_float(receivables)
    revenue = _to_float(revenue)
    if math.isnan(receivables) or math.isnan(revenue) or revenue == 0:
        return math.nan
    result = receivables / abs(revenue) * days
    return result if math.isfinite(result) else math.nan


def price_volume_split(revenue: float, volume: float) -> float:
    """Implied realisation (price) change when revenue and volume are known.

    Given revenue growth (%) and volume growth (%), the residual
    ``(1+rev)/(1+vol)-1`` is the value/price realisation change (%). Used for
    the peer_divergence detector's price-vs-volume split.
    """
    rev = _to_float(revenue) / 100.0
    vol = _to_float(volume) / 100.0
    if math.isnan(rev) or math.isnan(vol) or (1.0 + vol) == 0:
        return math.nan
    result = ((1.0 + rev) / (1.0 + vol) - 1.0) * 100.0
    return result if math.isfinite(result) else math.nan


# ---------------------------------------------------------------------------
# Dispersion / divergence (for peer comparison and anomaly scoring)
# ---------------------------------------------------------------------------
def mean(values: Iterable[float]) -> float:
    """Mean of the finite values in ``values`` (NaN if none)."""
    arr = np.array([_to_float(v) for v in values], dtype="float64")
    arr = arr[~np.isnan(arr)]
    return float(arr.mean()) if arr.size else math.nan


def stdev(values: Iterable[float], sample: bool = True) -> float:
    """Standard deviation of the finite values (NaN if < 2 points)."""
    arr = np.array([_to_float(v) for v in values], dtype="float64")
    arr = arr[~np.isnan(arr)]
    if arr.size < 2:
        return math.nan
    ddof = 1 if sample else 0
    result = float(arr.std(ddof=ddof))
    return result if math.isfinite(result) else math.nan


def zscore(value: float, values: Iterable[float]) -> float:
    """Z-score of ``value`` against the distribution ``values``.

    Returns NaN when the sample has no spread. Used to flag a company metric
    that is unusual relative to its peer group.
    """
    v = _to_float(value)
    mu = mean(values)
    sd = stdev(values)
    if math.isnan(v) or math.isnan(mu) or math.isnan(sd) or sd == 0:
        return math.nan
    result = (v - mu) / sd
    return result if math.isfinite(result) else math.nan


def divergence_gap(company: float, peer_mean: float) -> float:
    """Gap between a company metric and the peer mean (same units)."""
    c = _to_float(company)
    p = _to_float(peer_mean)
    if math.isnan(c) or math.isnan(p):
        return math.nan
    return c - p


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` into ``[lo, hi]``; NaN passes through unchanged."""
    v = _to_float(value)
    if math.isnan(v):
        return math.nan
    return max(lo, min(hi, v))


def weighted_score(components: dict, weights: dict) -> float:
    """Weighted average of ``components`` using ``weights``.

    Missing components are ignored (their weight is dropped from the
    denominator) so a detector can score on whatever signals are available.
    Returns NaN if the total available weight is zero.
    """
    num = 0.0
    den = 0.0
    for key, val in components.items():
        w = _to_float(weights.get(key, 0.0))
        val = _to_float(val)
        if math.isnan(w) or math.isnan(val) or w == 0:
            continue
        num += w * val
        den += w
    if den == 0:
        return math.nan
    return num / den
