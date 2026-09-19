"""
Quick sanity check — verify indicator math and data pipeline.

Run: python verify.py

Tests:
  1. Downloads 5m gold data from yfinance
  2. Computes all indicators
  3. Prints the last few rows for manual comparison with TradingView
  4. Checks for any obvious issues (NaN where there shouldn't be, etc.)
"""

import sys
from pathlib import Path

import pandas as pd

from data_pipeline import CandleStore
from indicators import atr_sl_finder, ema, rma, supertrend, true_range
from signal_detector import compute_indicators, detect_signal, get_latest_flip_info


def main():
    print("=" * 60)
    print("  GooeyTrade Bot — Indicator Verification")
    print("=" * 60)
    print()

    # --- 1. Bootstrap data ---
    store = CandleStore("verify_candles.csv")
    if store.is_empty():
        n = store.bootstrap_yfinance()
        print(f"  Loaded {n} candles\n")
    else:
        print(f"  Using existing {store.candle_count()} candles\n")

    df = store.get_dataframe()
    print(f"  Date range: {df.index[0]} → {df.index[-1]}")
    print(f"  Last 5 candles:")
    print(df.tail(5).to_string())
    print()

    # --- 2. Compute indicators ---
    df_ind = compute_indicators(df)

    print("  Last 10 rows with indicators:")
    cols = ["Close", "EMA200", "ST_line", "ST_dir", "ATR_SL"]
    print(df_ind[cols].tail(10).to_string())
    print()

    # --- 3. Sanity checks ---
    errors = []

    # EMA200 should not be all NaN
    ema_valid = df_ind["EMA200"].notna().sum()
    if ema_valid == 0:
        errors.append("EMA200 is all NaN")
    else:
        print(f"  EMA200 valid values: {ema_valid}/{len(df_ind)}")

    # Supertrend direction should have values after warmup
    st_valid = df_ind["ST_dir"].notna().sum()
    if st_valid == 0:
        errors.append("Supertrend direction is all NaN")
    else:
        print(f"  Supertrend valid values: {st_valid}/{len(df_ind)}")

    # ATR SL Finder
    atr_valid = df_ind["ATR_SL"].notna().sum()
    if atr_valid == 0:
        errors.append("ATR SL Finder is all NaN")
    else:
        print(f"  ATR SL Finder valid values: {atr_valid}/{len(df_ind)}")

    print()

    # --- 4. Signal detection ---
    signal = detect_signal(df)
    if signal:
        print("  *** SIGNAL DETECTED ***")
        print(f"  Direction:    {signal.direction}")
        print(f"  Flip time:    {signal.flip_idx}")
        print(f"  Flip close:   {signal.flip_close:.2f}")
        print(f"  Entry time:   {signal.entry_idx}")
        print(f"  Entry price:  {signal.entry_price:.2f}")
        print(f"  Stop Loss:    {signal.sl:.2f}")
        print(f"  Take Profit:  {signal.tp:.2f}")
        print(f"  ATR SL value: {signal.atr_sl:.2f}")
        print(f"  EMA200:       {signal.ema200:.2f}")
        print(f"  Supertrend:   {signal.supertrend_line:.2f}")
        print()

        # Risk:Reward check
        risk = abs(signal.entry_price - signal.sl)
        reward = abs(signal.tp - signal.entry_price)
        rr = reward / risk if risk > 0 else 0
        print(f"  Risk:   {risk:.2f}")
        print(f"  Reward: {reward:.2f}")
        print(f"  R:R:    {rr:.2f} (should be 3.0)")
    else:
        print("  No signal currently active.")
        flip_info = get_latest_flip_info(df)
        if flip_info:
            print(f"  Most recent flip: {flip_info['direction']} at {flip_info['flip_time']}")
            print(f"    Bias OK: {flip_info['bias_ok']}")
        else:
            print("  No flips found in data.")

    print()

    # --- 5. RMA spot-check ---
    # Compare RMA against known values if we have enough data
    if len(df) > 30:
        tr = true_range(df["High"], df["Low"], df["Close"])
        rma_14 = rma(tr, 14)
        # The last RMA value should match pandas .ewm(alpha=1/14)
        rma_pandas = tr.ewm(alpha=1 / 14, adjust=False).mean()
        last_rma = rma_14.iloc[-1]
        last_pandas = rma_pandas.iloc[-1]
        diff = abs(last_rma - last_pandas)
        status = "PASS" if diff < 0.001 else f"FAIL (diff={diff:.6f})"
        print(f"  RMA vs pandas ewm check: {status}")
        print(f"    RMA last:   {last_rma:.4f}")
        print(f"    ewm last:   {last_pandas:.4f}")
    print()

    # --- 6. Cleanup ---
    Path("verify_candles.csv").unlink(missing_ok=True)
    print("  Cleaned up verify_candles.csv")

    if errors:
        print(f"\n  ERRORS: {errors}")
        sys.exit(1)
    else:
        print("\n  All checks passed.")


if __name__ == "__main__":
    main()
