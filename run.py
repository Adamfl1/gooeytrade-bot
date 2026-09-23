"""
Main entry point for the GooeyTrade bot.

Two-phase flow (matches your Pine Script exactly):

  Phase A — Entry signal (every 5 min):
    1. Scrape TradingView for "BUY price" or "SELL price" label
    2. Validate freshness against live GooeyTrade price (reject if stale)
    3. If found → open market order on GooeyTrade
    4. Save pending entry for Phase B

  Phase B — SL/TP application (next 5-min scrape):
    1. Scrape TradingView for "SL price" and "TP price" labels
    2. If found → edit the open position to add SL/TP
    3. Remove from pending

  Signal source: TradingView label scrape ONLY. No local fallback.

Usage:
    python run.py                      # dry run (default)
    python run.py --live               # LIVE trading
    python run.py --live --yes         # skip confirmation
    python run.py --live --volume 0.05 # custom lot size
    python run.py --tv-only            # only scrape, don't trade
"""

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

from config import (
    TV_CHART_URL, TV_SESSION_FILE, GOOEYTRADE_URL, SESSION_FILE,
    DEFAULT_VOLUME, DRY_RUN,
)
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels, parse_signal
from state import (
    has_traded, record_trade, get_trade_count,
    get_signal_first_seen, record_signal_first_seen,
)
from execution import (
    execute_market_order, add_pending_sl, load_pending_sl, save_pending_sl,
)


def scrape_tv() -> dict | None:
    """Scrape TradingView chart. Returns raw result or None."""
    if not TV_SESSION_FILE.exists():
        print("  [TV] tv_session.json not found — skipping TV scrape")
        print("  [TV] Run: python capture_tv_session.py")
        return None

    print(f"  [TV] Scraping: {TV_CHART_URL}")
    try:
        raw = scrape_chart(TV_CHART_URL)
        print(f"  [TV] Labels found: {len(raw.get('labels', []))}")
        for label in raw.get("labels", []):
            print(f"       - {label}")
        return raw
    except Exception as e:
        print(f"  [TV] Scrape failed: {e}")
        return None


def _apply_sl_tp_dialog(page, direction: str, volume: float,
                        sl: float, tp: float) -> str | None:
    """Open the position edit dialog on GooeyTrade and save SL/TP values.

    Returns None on success, or an error-reason string on failure.
    """
    from execution import (
        _find_position_row, SEL_POSITION_TPSL_BTN,
        SEL_POSITION_EDIT_DIALOG, SEL_POSITION_EDIT_TOGGLE,
        SEL_POSITION_EDIT_VALUE, SEL_STEPPER_INPUT,
        SEL_POSITION_EDIT_SAVE,
    )

    try:
        row = _find_position_row(page, direction, volume)
        if row is None:
            print(f"  [Phase B] Position row not found for {direction.upper()}")
            return f"position row not found ({direction} vol={volume})"

        # Click TPSL button to open edit dialog
        print("  [Phase B] Opening SL/TP edit dialog...")
        tpsl_btn = row.locator(SEL_POSITION_TPSL_BTN)
        tpsl_btn.click()
        time.sleep(1)

        dialog = page.locator(SEL_POSITION_EDIT_DIALOG)
        dialog.wait_for(state="visible", timeout=5000)

        # Toggle SL and TP ON
        toggles = dialog.locator(SEL_POSITION_EDIT_TOGGLE)
        for idx, label in [(0, "Stop Loss"), (1, "Take Profit")]:
            toggle = toggles.nth(idx)
            value_input = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(idx)
            is_on = value_input.count() > 0 and value_input.is_visible()
            if not is_on:
                toggle.click()
                time.sleep(0.5)
                print(f"    Toggled {label} ON")

        # Type SL value
        print(f"  [Phase B] Setting SL = {sl:.2f}")
        sl_input = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(0).locator(SEL_STEPPER_INPUT)
        sl_input.wait_for(state="visible", timeout=5000)
        sl_input.click(click_count=3)
        time.sleep(0.05)
        page.keyboard.press("Control+a")
        page.keyboard.insert_text(str(sl))
        time.sleep(0.1)
        page.keyboard.press("Tab")
        time.sleep(0.3)

        # Type TP value
        print(f"  [Phase B] Setting TP = {tp:.2f}")
        tp_input = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(1).locator(SEL_STEPPER_INPUT)
        tp_input.wait_for(state="visible", timeout=5000)
        tp_input.click(click_count=3)
        time.sleep(0.05)
        page.keyboard.press("Control+a")
        page.keyboard.insert_text(str(tp))
        time.sleep(0.1)
        page.keyboard.press("Tab")
        time.sleep(0.3)

        # Save
        save_btn = dialog.locator(SEL_POSITION_EDIT_SAVE)
        save_btn.wait_for(state="visible", timeout=5000)
        start = time.time()
        while time.time() - start < 5:
            if save_btn.get_attribute("disabled") is None:
                break
            time.sleep(0.3)
        save_btn.click()
        time.sleep(2)

        # Handle confirmation popup after save
        confirm_btn = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
        if confirm_btn.count() > 0:
            try:
                confirm_btn.wait_for(state="visible", timeout=5000)
                confirm_btn.click()
                time.sleep(2)
                print("  [Phase B] Confirmed!")
            except Exception:
                pass

        print("  [Phase B] Position SL/TP saved!")
        return None

    except Exception as e:
        print(f"  [Phase B] ERROR: {e}")
        return f"{type(e).__name__}: {e}"


def run_phase_b(page, raw: dict, dry_run: bool, volume: float) -> bool:
    """Phase B: after the entry candle closes, scrape SL/TP labels from
    the TradingView chart and apply them to the open position.

    Returns True if SL/TP was applied.
    """
    entries = load_pending_sl()
    unresolved = [e for e in entries if not e.get("resolved")]
    if not unresolved:
        print("  [Phase B] No pending trades waiting for SL/TP")
        return False

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    applied_any = False

    for entry in unresolved:
        direction = entry["direction"]
        entry_price = entry.get("entry_price", 0)
        trade_volume = entry.get("volume", volume)
        start = datetime.fromisoformat(entry["entry_candle_start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        # Strategy has no valid SL/TP before the entry candle closes
        elapsed = (now - start).total_seconds()
        if elapsed < 5 * 60:
            print(f"  [Phase B] {direction.upper()} entry candle still open "
                  f"({elapsed:.0f}s / 300s) — retrying next run")
            entry["last_attempt_at"] = now.isoformat()
            entry["last_error"] = f"candle still open ({elapsed:.0f}s)"
            continue

        # Candle closed — scrape SL/TP labels from this run's chart scrape
        tpsl = parse_tp_sl_labels(raw)
        if not tpsl:
            print(f"  [Phase B] No SL/TP labels on chart yet for "
                  f"{direction.upper()} @ {entry_price:.2f} — retrying next run")
            entry["last_attempt_at"] = now.isoformat()
            entry["last_error"] = "no SL/TP labels on chart (OCR scrape empty)"
            continue
        sl, tp = tpsl["sl"], tpsl["tp"]

        print(f"\n  [Phase B] SL/TP from chart: SL={sl:.2f}  TP={tp:.2f}")
        print(f"  [Phase B] Applying to: {direction.upper()} @ {entry_price:.2f}")

        if dry_run:
            print(f"  [DRY RUN] Would edit position SL/TP → SL={sl:.2f}  TP={tp:.2f}")
            entry["resolved"] = True
            entry["resolved_at"] = now.isoformat()
            entry["last_attempt_at"] = now.isoformat()
            entry.pop("last_error", None)
            entry["sl"] = sl
            entry["tp"] = tp
            applied_any = True
            continue

        err = _apply_sl_tp_dialog(page, direction, trade_volume, sl, tp)
        entry["last_attempt_at"] = now.isoformat()
        if err is None:
            entry["resolved"] = True
            entry["resolved_at"] = now.isoformat()
            entry.pop("last_error", None)
            entry["sl"] = sl
            entry["tp"] = tp
            applied_any = True
        else:
            entry["last_error"] = err
            print(f"  [Phase B] Failed to apply SL/TP ({err}) — retrying next run")

    save_pending_sl(entries)
    return applied_any


def run_phase_a(page, raw: dict, dry_run: bool, volume: float) -> bool:
    """Phase A: Find entry signal and open trade.

    Only trades signals from the LAST candle — rejects old signals
    where the entry price is far from current price.

    Returns True if a trade was opened.
    """
    entry = parse_entry_label(raw)
    if not entry:
        print("  [Phase A] No entry signal on chart")
        return False

    direction = entry["direction"]
    entry_price = entry["entry_price"]

    # Strategy has no valid SL/TP before the entry candle closes —
    # Phase B scrapes the SL/TP labels from the chart on a later run.
    sl = 0
    tp = 0

    print(f"\n  [Phase A] Entry signal: {direction.upper()} @ {entry_price:.2f} (SL/TP after candle close)")

    # Check OCR freshness status (Pine Script sends FRESH/OLD)
    signal_status = raw.get("signal_table", {}).get("status", "")
    if signal_status == "old":
        print("  [Phase A] Signal status: OLD (>120s) — skipping")
        return False
    elif signal_status == "fresh":
        print("  [Phase A] Signal status: FRESH (<120s)")
    elif signal_status:
        print(f"  [Phase A] Signal status: {signal_status}")

    # Get current price to check freshness — FAIL CLOSED if unreadable
    current_price = 0
    for attempt in range(5):
        try:
            buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
            price_el = buy_btn.locator(".ui-order-button__price")
            price_text = price_el.inner_text(timeout=5000)
            if price_text.strip():
                current_price = float(price_text.replace(",", ""))
                break
        except Exception:
            pass
        time.sleep(1)

    if current_price == 0:
        print("  [Phase A] Could not read live price from GooeyTrade — rejecting signal as precaution")
        return False

    print(f"  [Phase A] Current price: {current_price:.2f}")

    # Reject if entry price is too far from current price (old signal)
    distance_pct = abs(entry_price - current_price) / current_price * 100
    if distance_pct > 0.5:  # more than 0.5% away = old signal
        print(f"  [Phase A] Signal too old (price {distance_pct:.2f}% away from current) — skipping")
        return False

    # Time-based freshness check: reject signals older than 120 seconds
    from datetime import datetime, timezone
    signal_key = f"tv_{entry_price}_{direction}"
    now = datetime.now(timezone.utc)
    first_seen = get_signal_first_seen(signal_key)
    if first_seen:
        age_seconds = (now - first_seen).total_seconds()
        if age_seconds > 120:
            print(f"  [Phase A] Signal too old ({age_seconds:.0f}s > 120s) — skipping")
            return False
        print(f"  [Phase A] Signal age: {age_seconds:.0f}s (max 120s)")
    else:
        record_signal_first_seen(signal_key, now)
        print(f"  [Phase A] Signal first seen — 120s timer started")

    # Check for duplicate
    if has_traded(signal_key, direction):
        print(f"  [Phase A] Already traded {direction.upper()} at {entry_price} — skipping.")
        return False

    # Show signal info
    print(f"\n  SIGNAL (TradingView)")
    print(f"  Direction:  {direction.upper()}")
    print(f"  Entry:      {entry_price:.2f}")
    print(f"  SL/TP:      (will be applied on next candle)")
    print()

    if dry_run:
        print("  [DRY RUN] Would open trade — skipping execution")
        from datetime import datetime, timezone
        signal_time = datetime.now(timezone.utc).isoformat()
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=entry_price,
            sl=sl, tp=tp,
            volume=volume,
            signal_key=signal_key,
        )
        return True

    # Build signal object for execute_market_order
    class SimpleSignal:
        pass

    sig = SimpleSignal()
    sig.direction = direction
    sig.entry_price = entry_price
    sig.sl = sl
    sig.tp = tp

    live_price = execute_market_order(
        signal=sig,
        page=page,
        volume=volume,
        dry_run=False,
        skip_sl_tp=True,
    )

    if live_price > 0:
        print(f"\n  Trade opened. Live price: {live_price:.2f}")
        from datetime import datetime, timezone
        signal_time_dt = datetime.now(timezone.utc)
        signal_time = signal_time_dt.isoformat()
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=live_price,
            sl=sl, tp=tp,
            volume=volume,
            signal_key=signal_key,
        )

        # Queue for Phase B: SL/TP scraped from chart after candle close
        candle_start = signal_time_dt.replace(
            minute=(signal_time_dt.minute // 5) * 5, second=0, microsecond=0
        )
        add_pending_sl(direction, live_price, volume, candle_start)
        return True
    else:
        print("\n  Trade failed. Check output above.")
        return False


def main():
    parser = argparse.ArgumentParser(description="GooeyTrade Bot")
    parser.add_argument("--live", action="store_true",
                        help="Execute real trades")
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry-run mode")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME,
                        help=f"Lot size (default: {DEFAULT_VOLUME})")
    parser.add_argument("--tv-only", action="store_true",
                        help="Only scrape TradingView, don't trade")
    args = parser.parse_args()

    dry_run = DRY_RUN or not args.live or args.dry_run

    print("=" * 60)
    print("  GooeyTrade Bot")
    print(f"  Mode: {'LIVE' if not dry_run else 'DRY RUN'}")
    print(f"  Trades so far: {get_trade_count()}")
    pending = [e for e in load_pending_sl() if not e.get("resolved")]
    if pending:
        print(f"  Pending SL/TP: {len(pending)} trade(s)")
    print("=" * 60)
    print()

    # --- 1. Scrape TradingView ---
    raw = scrape_tv()

    if args.tv_only:
        if raw:
            entry = parse_entry_label(raw)
            tpsl = parse_tp_sl_labels(raw)
            sig = parse_signal(raw)
            print(f"\n  Entry signal: {entry}")
            print(f"  SL/TP labels: {tpsl}")
            print(f"  Signal status: {sig['status'] if sig else 'unknown'}")
            print(f"  All labels: {raw.get('labels', [])}")
        else:
            print("\n  No data scraped.")
        return

    if raw is None:
        print("\n  [TV] Scrape failed — cannot proceed without TradingView data.")
        print("  Make sure tv_session.json is valid (run: python capture_tv_session.py)")
        sys.exit(1)

    # --- 2. Check if session is valid (page loaded) ---
    if not SESSION_FILE.exists():
        print("  ERROR: session.json not found.")
        print("  Run: python capture_session.py")
        sys.exit(1)

    # --- 3. Launch browser for GooeyTrade ---
    import os
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context(
            storage_state=str(SESSION_FILE),
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(GOOEYTRADE_URL, timeout=60000)

        try:
            page.wait_for_selector(
                '[data-testid="order-panel-buy-button"]',
                timeout=30000,
            )
        except Exception:
            print("  ERROR: Trade page did not load. Session may be expired.")
            print("  Run: python capture_session.py")
            browser.close()
            sys.exit(1)

        print("  Trade page loaded.\n")

        # --- 4. Phase B: scrape SL/TP labels from chart (after candle close) ---
        phase_b_done = run_phase_b(page, raw, dry_run, args.volume)

        # --- 5. Phase A: check for new entry signal (SL/TP deferred) ---
        phase_a_done = run_phase_a(page, raw, dry_run, args.volume)

        if not phase_a_done and not phase_b_done:
            print("\n  No actionable signals from TradingView this run.")

        if not dry_run:
            print("\n  Waiting 5 seconds before closing...")
            time.sleep(5)

        browser.close()

    print("\n  Done.")


if __name__ == "__main__":
    main()
