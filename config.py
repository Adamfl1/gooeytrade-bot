"""
Configuration for GooeyTrade bot.

All tunables in one place. Override via environment variables in CI.
"""

import os
from pathlib import Path

# ── TradingView ──────────────────────────────────────────────────────────────

TV_CHART_URL = os.environ.get(
    "TV_CHART_URL",
    "https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD",
)
TV_SESSION_FILE = Path(os.environ.get("TV_SESSION_FILE", "tv_session.json"))

# ── GooeyTrade ───────────────────────────────────────────────────────────────

GOOEYTRADE_URL = "https://mtr.gooeytrade.com/app/trade"
SESSION_FILE = Path(os.environ.get("SESSION_FILE", "session.json"))

# ── Trade parameters ─────────────────────────────────────────────────────────

DEFAULT_VOLUME = float(os.environ.get("TRADE_VOLUME", "2.5"))
RR_RATIO = 3.0  # risk:reward ratio for TP

# ── Timing ───────────────────────────────────────────────────────────────────

CANDLE_INTERVAL_MIN = 5
MAX_SIGNAL_AGE_MINUTES = 15  # reject signals older than this

# ── State tracking ───────────────────────────────────────────────────────────

STATE_FILE = Path(os.environ.get("STATE_FILE", "bot_state.json"))

# ── Dry run ──────────────────────────────────────────────────────────────────

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("true", "1", "yes")
