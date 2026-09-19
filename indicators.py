"""
Indicator math module — matches Pine Script exactly.

Functions:
  - ema(series, length)            → standard EMA
  - rma(series, length)            → Wilder's RMA (ta.rma in Pine)
  - true_range(high, low, close)   → True Range
  - supertrend(high, low, close, period=10, multiplier=3.0)
      → (supertrend_line, direction, upper_band, lower_band)
        direction: 1 = uptrend (bullish), -1 = downtrend (bearish)
  - atr_sl_finder(high, low, close, length=14, factor=1.5)
      → ATR-based stop-loss distance per bar
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """Standard EMA matching Pine Script ta.ema()."""
    return series.ewm(alpha=2 / (length + 1), adjust=False).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's RMA matching Pine Script ta.rma().

    Pine initialises with SMA, then:
        rma[i] = (rma[i-1] * (length-1) + source[i]) / length
    Equivalent to ewm(alpha=1/length, adjust=False) after seeding
    the first valid value with SMA.
    """
    result = series.copy().astype(float)
    vals = result.values
    n = len(vals)
    sma = np.nan
    for i in range(n):
        if not np.isnan(vals[i]):
            sma = vals[i]
            break
    if np.isnan(sma):
        return result
    out = np.full(n, np.nan)
    out[i] = sma
    alpha = 1.0 / length
    for j in range(i + 1, n):
        if np.isnan(vals[j]):
            out[j] = out[j - 1]
        else:
            out[j] = out[j - 1] * (1 - alpha) + vals[j] * alpha
    return pd.Series(out, index=series.index)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range matching Pine Script ta.tr(true)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Supertrend indicator matching Pine Script.

    Returns:
        st_line:  supertrend value (the line itself)
        direction: 1 = uptrend (bullish), -1 = downtrend (bearish)
        upper:    final upper band
        lower:    final lower band
    """
    atr = rma(true_range(high, low, close), period)
    hl2 = (high + low) / 2.0

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(high)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    st_line = np.full(n, np.nan)

    prev_dir = np.nan

    for i in range(n):
        bu = basic_upper.iloc[i] if not np.isnan(basic_upper.iloc[i]) else np.nan
        bl = basic_lower.iloc[i] if not np.isnan(basic_lower.iloc[i]) else np.nan
        prev_close = close.iloc[i - 1] if i > 0 else np.nan

        # --- direction first (uses previous final bands) ---
        prev_fu = final_upper[i - 1] if i > 0 else np.nan
        prev_fl = final_lower[i - 1] if i > 0 else np.nan

        if i == 0 or np.isnan(prev_dir):
            new_dir = 1 if not np.isnan(bl) else np.nan
        elif prev_dir == 1:
            new_dir = -1 if (not np.isnan(prev_fl) and close.iloc[i] < prev_fl) else 1
        else:
            new_dir = 1 if (not np.isnan(prev_fu) and close.iloc[i] > prev_fu) else -1

        direction[i] = new_dir

        # --- final upper band ---
        if not np.isnan(bu):
            if np.isnan(prev_fu):
                final_upper[i] = bu
            elif new_dir == -1:
                # Downtrend: ratchet down = min(basic, prev)
                final_upper[i] = min(bu, prev_fu)
            elif not np.isnan(prev_close) and prev_close > prev_fu:
                # Bullish but close was above prev upper → reset
                final_upper[i] = bu
            else:
                final_upper[i] = prev_fu

        # --- final lower band ---
        if not np.isnan(bl):
            if np.isnan(prev_fl):
                final_lower[i] = bl
            elif new_dir == 1:
                # Uptrend: ratchet up = max(basic, prev)
                final_lower[i] = max(bl, prev_fl)
            elif not np.isnan(prev_close) and prev_close < prev_fl:
                # Bearish but close was below prev lower → reset
                final_lower[i] = bl
            else:
                final_lower[i] = prev_fl

        prev_dir = new_dir

        # --- supertrend line ---
        if not np.isnan(new_dir):
            st_line[i] = final_lower[i] if new_dir == 1 else final_upper[i]

    return (
        pd.Series(st_line, index=high.index),
        pd.Series(direction, index=high.index),
        pd.Series(final_upper, index=high.index),
        pd.Series(final_lower, index=high.index),
    )


def atr_sl_finder(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
    factor: float = 1.5,
) -> pd.Series:
    """ATR Stop-Loss Finder — the distance to subtract (long) or add (short).

    atr = RMA(True Range, length) * factor
    long SL  = low  - atr
    short SL = high + atr
    This function returns the atr value per bar.
    """
    return rma(true_range(high, low, close), length) * factor
