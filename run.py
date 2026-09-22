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
    add_pending_tp_sl, get_pending_tp_sl, remove_pending_tp_sl,
    get_signal_first_seen, record_signal_first_seen,
)
from execution import execute_market_order


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


def run_phase_b(page, raw: dict, dry_run: bool, volume: float) -> bool:
    """Phase B: Find SL/TP labels and apply to pending trade.

    Returns True if SL/TP was applied.
    """
    pending = get_pending_tp_sl()
    if not pending:
        print("  [Phase B] No pending trades waiting for SL/TP")
        return False

    # Apply to the most recent pending trade
    trade = pending[-1]
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    trade_volume = trade.get("volume", volume)

    sl = trade.get("sl", 0)
    tp = trade.get("tp", 0)

    if not sl or not tp:
        tpsl = parse_tp_sl_labels(raw)
        if tpsl:
            sl = tpsl["sl"]
            tp = tpsl["tp"]

    if not sl or not tp:
        print("  [Phase B] No SL/TP values available — retrying next run")
        return False

    print(f"\n  [Phase B] SL/TP to apply: SL={sl:.2f}  TP={tp:.2f}")
    print(f"  [Phase B] Applying to: {direction.upper()} @ {entry_price:.2f}")

    if dry_run:
        print(f"  [DRY RUN] Would edit position SL/TP → SL={sl:.2f}  TP={tp:.2f}")
        remove_pending_tp_sl(direction, entry_price)
        return True

    # Edit the position on GooeyTrade
    try:
        from execution import (
            _find_position_row, SEL_POSITION_TPSL_BTN,
            SEL_POSITION_EDIT_DIALOG, SEL_POSITION_EDIT_TOGGLE,
            SEL_POSITION_EDIT_VALUE, SEL_STEPPER_INPUT,
            SEL_POSITION_EDIT_SAVE,
        )

        row = _find_position_row(page, direction, trade_volume)
        if row is None:
            print(f"  [Phase B] WARNING: Position row not found for {direction.upper()}")
            return False

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

        remove_pending_tp_sl(direction, entry_price)
        return True

    except Exception as e:
        print(f"  [Phase B] ERROR: {e}")
        return False


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

    tpsl = parse_tp_sl_labels(raw)
    sl = tpsl["sl"] if tpsl else 0
    tp = tpsl["tp"] if tpsl else 0

    print(f"\n  [Phase A] Entry signal: {direction.upper()} @ {entry_price:.2f} (SL: {sl}, TP: {tp})")

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
        )
        add_pending_tp_sl(direction, entry_price, volume, sl, tp)
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
        signal_time = datetime.now(timezone.utc).isoformat()
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=live_price,
            sl=sl, tp=tp,
            volume=volume,
        )
        add_pending_tp_sl(direction, live_price, volume, sl, tp)
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
    pending = get_pending_tp_sl()
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

        # --- 4. Phase B (first pass): apply any lingering pending trades ---
        phase_b_done_1 = run_phase_b(page, raw, dry_run, args.volume)

        # --- 5. Phase A: check for new entry signal ---
        phase_a_done = run_phase_a(page, raw, dry_run, args.volume)

        # --- 6. Phase B (second pass): apply SL/TP for trade just opened in Phase A ---
        phase_b_done_2 = run_phase_b(page, raw, dry_run, args.volume)
        phase_b_done = phase_b_done_1 or phase_b_done_2

        # --- 6. Nothing to do if no TV signal this run ---
        if not phase_a_done and not phase_b_done:
            print("\n  No actionable signals from TradingView this run.")

        if not dry_run:
            print("\n  Waiting 5 seconds before closing...")
            time.sleep(5)

        browser.close()

    print("\n  Done.")


if __name__ == "__main__":
    main()
