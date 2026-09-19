"""
Data pipeline for GooeyTrade bot.

Responsibilities:
  1. Bootstrap historical 5m OHLCV candles from yfinance (GC=F proxy for XAUUSD).
  2. Build new 5-minute candles from repeated live price reads.
  3. Persist candle history to a local CSV so state survives across runs.
  4. Provide the latest candle series for indicator computation.

Usage:
    from data_pipeline import CandleStore

    store = CandleStore("candles.csv")
    if store.is_empty():
        store.bootstrap_yfinance()          # one-time historical seed
    store.append_price(price, timestamp)    # call every ~5 min with live price
    df = store.get_dataframe()              # full OHLCV for indicators
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CANDLE_INTERVAL_MIN = 5
YFINANCE_TICKER = "GC=F"            # gold futures — closest proxy to XAUUSD
YFINANCE_INTERVAL = "5m"
LOCAL_CSV = Path("candles.csv")

# ---------------------------------------------------------------------------
# Candle builder — accumulates tick prices into 5m OHLC bars
# ---------------------------------------------------------------------------

class CandleBuilder:
    """Accumulates individual price ticks and produces closed 5m candles."""

    def __init__(self, interval_min: int = CANDLE_INTERVAL_MIN):
        self.interval_min = interval_min
        self._current_open: float | None = None
        self._current_high: float | None = None
        self._current_low: float | None = None
        self._current_close: float | None = None
        self._current_start: datetime | None = None

    @staticmethod
    def _floor_to_interval(dt: datetime, interval_min: int) -> datetime:
        """Floor a datetime to the start of its 5-minute bucket (UTC)."""
        minute = (dt.minute // interval_min) * interval_min
        return dt.replace(minute=minute, second=0, microsecond=0)

    def on_price(self, price: float, ts: datetime) -> pd.Series | None:
        """Feed a new price tick.

        Returns a closed candle (pd.Series with Open/High/Low/Close/Volume)
        if the tick belongs to a NEW interval (i.e. the previous candle is
        now closed).  Returns None while accumulating the current candle.
        """
        bucket = self._floor_to_interval(ts, self.interval_min)

        if self._current_start is None:
            self._start_new(price, bucket)
            return None

        if bucket == self._current_start:
            # still in the same interval — update OHLC
            self._current_high = max(self._current_high, price)
            self._current_low = min(self._current_low, price)
            self._current_close = price
            return None

        # new interval started → close the previous candle
        candle = self._close_candle()
        self._start_new(price, bucket)
        return candle

    def flush_current(self) -> pd.Series | None:
        """Return the in-progress candle (even if not yet closed)."""
        if self._current_start is None:
            return None
        return self._close_candle()

    # ---- internals ----

    def _start_new(self, price: float, bucket: datetime):
        self._current_open = price
        self._current_high = price
        self._current_low = price
        self._current_close = price
        self._current_start = bucket

    def _close_candle(self) -> pd.Series:
        s = pd.Series(
            {
                "Open": self._current_open,
                "High": self._current_high,
                "Low": self._current_low,
                "Close": self._current_close,
                "Volume": 0,  # GooeyTrade doesn't expose volume
            },
            name=self._current_start,
        )
        return s

# ---------------------------------------------------------------------------
# CandleStore — persistence + bootstrap
# ---------------------------------------------------------------------------

class CandleStore:
    """Manages the historical candle series on disk."""

    def __init__(self, csv_path: str | Path = LOCAL_CSV):
        self.csv_path = Path(csv_path)
        self.df: pd.DataFrame = self._load()
        self.builder = CandleBuilder()

    # ---- public API ----

    def is_empty(self) -> bool:
        return self.df.empty

    def bootstrap_yfinance(self, ticker: str = YFINANCE_TICKER) -> int:
        """Download historical 5m candles from yfinance.

        yfinance limits 5m data to the last 60 days.  That gives ~1700
        candles — plenty for EMA200 warmup.

        Returns the number of candles loaded.
        """
        print(f"Downloading {ticker} 5m data from yfinance...")
        raw = yf.download(ticker, period="60d", interval=YFINANCE_INTERVAL, progress=False)

        if raw.empty:
            raise RuntimeError(f"yfinance returned no data for {ticker}")

        # yfinance may return MultiIndex columns — flatten
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        # Ensure UTC timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df.index.name = "Datetime"
        df = df.sort_index()

        # Drop any all-NaN rows
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        self.df = df
        self._save()
        print(f"  Bootstrapped {len(df)} candles from yfinance ({df.index[0]} → {df.index[-1]})")
        return len(df)

    def append_candle(self, candle: pd.Series):
        """Append a single closed candle to the store."""
        ts = candle.name
        if ts in self.df.index:
            return  # already have this candle
        self.df.loc[ts] = candle[["Open", "High", "Low", "Close", "Volume"]]
        self.df.sort_index(inplace=True)
        self._save()

    def append_price(self, price: float, ts: datetime) -> bool:
        """Feed a live price tick.  Returns True if a new closed candle was produced."""
        closed = self.builder.on_price(price, ts)
        if closed is not None:
            self.append_candle(closed)
            return True
        return False

    def flush_current(self) -> bool:
        """Force-write the in-progress candle (e.g. at end of session)."""
        candle = self.builder.flush_current()
        if candle is not None:
            self.append_candle(candle)
            return True
        return False

    def get_dataframe(self) -> pd.DataFrame:
        """Return a copy of the full OHLCV DataFrame."""
        return self.df.copy()

    def last_candle_time(self) -> pd.Timestamp | None:
        if self.df.empty:
            return None
        return self.df.index[-1]

    def candle_count(self) -> int:
        return len(self.df)

    # ---- internal ----

    def _load(self) -> pd.DataFrame:
        if not self.csv_path.exists():
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = pd.read_csv(self.csv_path, index_col=0, parse_dates=True)
        df.index.name = "Datetime"
        return df

    def _save(self):
        self.df.to_csv(self.csv_path)
