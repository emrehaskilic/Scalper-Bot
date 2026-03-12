"""Technical indicators ported from Pine Script strategy.

Implements only the actively-used indicators:
ALMA, TEMA, HullMA, RSI, Keltner Channel, ATR, EMA, SMA, Swing High/Low.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# =====================================================================
# Moving Averages
# =====================================================================

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def tema(series: pd.Series, period: int) -> pd.Series:
    """Triple Exponential Moving Average."""
    e1 = ema(series, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return 3 * (e1 - e2) + e3


def hull_ma(series: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average."""
    half_len = max(1, period // 2)
    sqrt_len = max(1, round(math.sqrt(period)))
    wma_half = series.rolling(window=half_len, min_periods=half_len).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )
    wma_full = series.rolling(window=period, min_periods=period).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )
    diff = 2 * wma_half - wma_full
    return diff.rolling(window=sqrt_len, min_periods=sqrt_len).apply(
        lambda x: np.average(x, weights=np.arange(1, len(x) + 1)), raw=True
    )


def alma(series: pd.Series, period: int, offset: float = 0.85, sigma: int = 5) -> pd.Series:
    """Arnaud Legoux Moving Average."""
    m = offset * (period - 1)
    s = period / sigma
    weights = np.array([math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)])
    weights /= weights.sum()

    def _alma_calc(window: np.ndarray) -> float:
        return np.dot(window, weights)

    return series.rolling(window=period, min_periods=period).apply(_alma_calc, raw=True)


def variant(
    ma_type: str,
    series: pd.Series,
    period: int,
    sigma: int = 5,
    offset_alma: float = 0.85,
) -> pd.Series:
    """Select MA type — mirrors Pine Script variant() function."""
    ma_type = ma_type.upper()
    if ma_type == "ALMA":
        return alma(series, period, offset_alma, sigma)
    elif ma_type == "TEMA":
        return tema(series, period)
    elif ma_type in ("HULLMA", "HULL"):
        return hull_ma(series, period)
    elif ma_type == "EMA":
        return ema(series, period)
    else:
        return sma(series, period)


# =====================================================================
# Oscillators & Bands
# =====================================================================

def rsi(series: pd.Series, period: int = 28) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 50) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def keltner_channel(
    close: pd.Series, period: int = 80, multiplier: float = 10.5
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel: returns (basis, upper, lower)."""
    basis = sma(close, period)
    span = atr(close, close, close, period)  # simplified — use actual H/L if available
    return basis, basis + span * multiplier, basis - span * multiplier


# =====================================================================
# Swing Detection
# =====================================================================

def pivot_high(high: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    """Detect swing highs (pivot highs). Returns NaN where no pivot."""
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, len(high) - right):
        pivot_val = high.iloc[i]
        is_pivot = True
        for j in range(1, left + 1):
            if high.iloc[i - j] >= pivot_val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if high.iloc[i + j] >= pivot_val:
                    is_pivot = False
                    break
        if is_pivot:
            result.iloc[i] = pivot_val
    return result


def pivot_low(low: pd.Series, left: int = 10, right: int = 10) -> pd.Series:
    """Detect swing lows (pivot lows). Returns NaN where no pivot."""
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, len(low) - right):
        pivot_val = low.iloc[i]
        is_pivot = True
        for j in range(1, left + 1):
            if low.iloc[i - j] <= pivot_val:
                is_pivot = False
                break
        if is_pivot:
            for j in range(1, right + 1):
                if low.iloc[i + j] <= pivot_val:
                    is_pivot = False
                    break
        if is_pivot:
            result.iloc[i] = pivot_val
    return result
