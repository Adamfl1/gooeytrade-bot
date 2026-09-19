"""
Signal detection — identifies Supertrend flips against EMA200 bias.

Logic (matches Pine Script strategy exactly):

  1. Directional bias:  EMA(200) on close
       price > EMA200 → only BUY
       price < EMA200 → only SELL

  2. Entry trigger:  Supertrend(10, 3) flip
       flip to uptrend + bias long  → BUY signal
       flip to downtrend + bias short → SELL signal
       Entry at the OPEN of the candle following the flip candle.

  3. Stop Loss (ATR SL Finder):
       atr = RMA(True Range, 14) * 1.5
       long  SL = low(entry candle)  - atr
       short SL = high(entry candle) + atr
       (entry candle = the candle whose open is the fill price)

  4. Take Profit:  R:R = 3
       long  TP = entry + (entry - SL) * 3
       short TP = entry - (SL - entry) * 3

Timing in a scheduled system (check every 5 min after candle close):
  - Flip detected on candle N → signal fires
  - Entry fills at candle N+1 open (next candle, same 5-min boundary or seconds later)
  - SL/TP computed once candle N+1 closes (using N+1's low/high + ATR from N+1)
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pandas as pd

from indicators import atr_sl_finder, ema, supertrend

MAX_SIGNAL_AGE_MINUTES = 15


@dataclass
class Signal:
    direction: str           # "buy" or "sell"
    flip_idx: pd.Timestamp   # timestamp of the flip candle
    flip_close: float        # close of the flip candle
    entry_idx: pd.Timestamp  # timestamp of the entry candle (flip + 1 bar)
    entry_price: float       # open of the entry candle
    sl: float                # stop-loss price
    tp: float                # take-profit price
    atr_sl: float            # raw ATR SL Finder value used
    ema200: float            # EMA200 value at flip candle
    supertrend_line: float   # Supertrend line at flip candle


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA200, Supertrend, and ATR SL Finder columns to the DataFrame.

    The DataFrame must have columns: Open, High, Low, Close.
    Returns the same DataFrame with new columns added.
    """
    df = df.copy()

    # EMA200
    df["EMA200"] = ema(df["Close"], 200)

    # Supertrend(10, 3)
    st_line, st_dir, _, _ = supertrend(df["High"], df["Low"], df["Close"], period=10, multiplier=3.0)
    df["ST_line"] = st_line
    df["ST_dir"] = st_dir

    # Previous Supertrend direction (for flip detection)
    df["ST_dir_prev"] = df["ST_dir"].shift(1)

    # ATR SL Finder(14, 1.5)
    df["ATR_SL"] = atr_sl_finder(df["High"], df["Low"], df["Close"], length=14, factor=1.5)

    return df


def detect_signal(df: pd.DataFrame) -> Signal | None:
    """Detect the most recent unprocessed Supertrend flip.

    Expects a DataFrame with at least 201 rows (for EMA200 warmup)
    and columns: Open, High, Low, Close.
    Computes indicators internally.

    Returns a Signal if a flip is found, None otherwise.
    """
    df = compute_indicators(df)

    if len(df) < 3:
        return None

    # Reject if the most recent candle is stale (> 15 min old)
    now = datetime.now(timezone.utc)
    last_candle_time = df.index[-1]
    if last_candle_time.tzinfo is None:
        last_candle_time = last_candle_time.replace(tzinfo=timezone.utc)
    age_minutes = (now - last_candle_time).total_seconds() / 60
    if age_minutes > MAX_SIGNAL_AGE_MINUTES:
        return None

    # Scan from the second-to-last bar backwards (we need flip+1 to exist
    # as the most recent bar for the entry candle).
    # We check the SECOND-TO-LAST closed candle for a flip,
    # because the entry candle (flip+1) must be the most recent closed bar.
    last_idx = len(df) - 1

    for i in range(last_idx - 1, 1, -1):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        st_dir = row["ST_dir"]
        st_dir_prev = prev["ST_dir"]
        close = row["Close"]
        ema200 = row["EMA200"]

        # Skip if indicators not yet warm
        if pd.isna(st_dir) or pd.isna(st_dir_prev) or pd.isna(ema200):
            continue

        # Check for direction flip
        flipped_up = st_dir_prev == -1 and st_dir == 1
        flipped_down = st_dir_prev == 1 and st_dir == -1

        if not (flipped_up or flipped_down):
            continue

        # Check EMA200 bias
        if flipped_up and close <= ema200:
            continue  # no long signal when price is below EMA200
        if flipped_down and close >= ema200:
            continue  # no short signal when price is above EMA200

        # Signal confirmed on candle i.  Entry candle is i+1.
        entry_row = df.iloc[i + 1]
        entry_price = entry_row["Open"]
        atr_sl = entry_row["ATR_SL"]

        if pd.isna(entry_price) or pd.isna(atr_sl):
            continue

        flip_ts = df.index[i]
        entry_ts = df.index[i + 1]

        if flipped_up:
            sl = entry_row["Low"] - atr_sl
            tp = entry_price + (entry_price - sl) * 3
            direction = "buy"
        else:
            sl = entry_row["High"] + atr_sl
            tp = entry_price - (sl - entry_price) * 3
            direction = "sell"

        return Signal(
            direction=direction,
            flip_idx=flip_ts,
            flip_close=close,
            entry_idx=entry_ts,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            atr_sl=atr_sl,
            ema200=ema200,
            supertrend_line=row["ST_line"],
        )

    return None


def get_latest_flip_info(df: pd.DataFrame) -> dict | None:
    """Return info about the most recent flip (even if no new entry candle yet).

    Useful for debugging / logging.  Returns a dict with flip details
    or None if no flip found in the data.
    """
    df = compute_indicators(df)
    if len(df) < 2:
        return None

    for i in range(len(df) - 1, 1, -1):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        st_dir = row["ST_dir"]
        st_dir_prev = prev["ST_dir"]

        if pd.isna(st_dir) or pd.isna(st_dir_prev):
            continue

        flipped_up = st_dir_prev == -1 and st_dir == 1
        flipped_down = st_dir_prev == 1 and st_dir == -1

        if flipped_up or flipped_down:
            return {
                "flip_time": df.index[i],
                "direction": "bullish" if flipped_up else "bearish",
                "close": row["Close"],
                "ema200": row["EMA200"],
                "st_line": row["ST_line"],
                "atr_sl": row["ATR_SL"],
                "bias_ok": (
                    (flipped_up and row["Close"] > row["EMA200"])
                    or (flipped_down and row["Close"] < row["EMA200"])
                ),
            }
    return None
