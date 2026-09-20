"""
State tracking — prevents duplicate trades and tracks pending TP/SL updates.

Persists to bot_state.json so the bot survives restarts.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from config import STATE_FILE


def _load() -> dict:
    if not STATE_FILE.exists():
        return {"trades": [], "pending_tp_sl": [], "last_signal_time": None}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if "trades" not in data:
            data["trades"] = []
        if "pending_tp_sl" not in data:
            data["pending_tp_sl"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"trades": [], "pending_tp_sl": [], "last_signal_time": None}


def _save(data: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


# ── Trade history (duplicate prevention) ─────────────────────────────────────

def has_traded(signal_time: str, direction: str) -> bool:
    """Check if we already traded this exact signal."""
    data = _load()
    for trade in data["trades"]:
        if trade.get("signal_time") == signal_time and trade.get("direction") == direction:
            return True
    return False


def record_trade(signal_time: str, direction: str, entry_price: float,
                 sl: float, tp: float, volume: float) -> None:
    """Record an executed trade."""
    data = _load()
    data["trades"].append({
        "signal_time": signal_time,
        "direction": direction,
        "entry_price": entry_price,
        "sl": sl,
        "tp": tp,
        "volume": volume,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    })
    data["last_signal_time"] = signal_time
    _save(data)


def get_trade_count() -> int:
    return len(_load()["trades"])


# ── Pending TP/SL (Phase B tracking) ────────────────────────────────────────

def add_pending_tp_sl(direction: str, entry_price: float, volume: float) -> None:
    """Record a trade that needs SL/TP applied on next candle (Phase B)."""
    data = _load()
    data["pending_tp_sl"].append({
        "direction": direction,
        "entry_price": entry_price,
        "volume": volume,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(data)


def get_pending_tp_sl() -> list[dict]:
    """Get all trades waiting for SL/TP to be applied."""
    return _load().get("pending_tp_sl", [])


def remove_pending_tp_sl(direction: str, entry_price: float) -> bool:
    """Remove a pending entry after SL/TP has been applied. Returns True if found."""
    data = _load()
    original = len(data["pending_tp_sl"])
    data["pending_tp_sl"] = [
        p for p in data["pending_tp_sl"]
        if not (p.get("direction") == direction
                and abs(p.get("entry_price", 0) - entry_price) < 5.0)
    ]
    _save(data)
    return len(data["pending_tp_sl"]) < original


def cleanup_old(days: int = 30) -> int:
    """Remove trades older than N days. Returns count removed."""
    data = _load()
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    original = len(data["trades"])
    data["trades"] = [
        t for t in data["trades"]
        if datetime.fromisoformat(t["executed_at"]).timestamp() > cutoff
    ]
    _save(data)
    return original - len(data["trades"])


# ── Signal freshness tracking (120s timeout) ────────────────────────────────

def get_signal_first_seen(signal_key: str) -> datetime | None:
    """Get when a signal was first seen. Returns None if new."""
    data = _load()
    first_seen_str = data.get("signal_first_seen", {}).get(signal_key)
    if first_seen_str:
        dt = datetime.fromisoformat(first_seen_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def record_signal_first_seen(signal_key: str, when: datetime) -> None:
    """Record when a signal was first seen."""
    data = _load()
    if "signal_first_seen" not in data:
        data["signal_first_seen"] = {}
    data["signal_first_seen"][signal_key] = when.isoformat()
    _save(data)


def cleanup_old_signals(max_age_seconds: int = 300) -> int:
    """Remove signal timestamps older than N seconds. Returns count removed."""
    data = _load()
    first_seen = data.get("signal_first_seen", {})
    if not first_seen:
        return 0
    now = datetime.now(timezone.utc)
    to_remove = []
    for key, ts_str in first_seen.items():
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if (now - dt).total_seconds() > max_age_seconds:
            to_remove.append(key)
    for key in to_remove:
        del first_seen[key]
    if to_remove:
        _save(data)
    return len(to_remove)
