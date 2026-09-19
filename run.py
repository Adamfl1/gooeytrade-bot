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
        return False

    tpsl = parse_tp_sl_labels(raw)
    if not tpsl:
        print("  [Phase B] No SL/TP labels found on chart")
        return False

    sl = tpsl["sl"]
    tp = tpsl["tp"]
    print(f"\n  [Phase B] SL/TP labels found: SL={sl:.2f}  TP={tp:.2f}")

    # Apply to the most recent pending trade
    trade = pending[-1]
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    trade_volume = trade.get("volume", volume)

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

    print(f"\n  [Phase A] Entry signal: {direction.upper()} @ {entry_price:.2f}")

    # Get current price to check freshness — FAIL CLOSED if unreadable
    try:
        buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
        price_el = buy_btn.locator(".ui-order-button__price")
        current_price = float(price_el.inner_text(timeout=3000).replace(",", ""))
    except Exception as e:
        print(f"  [Phase A] Could not read live price from GooeyTrade — rejecting signal as precaution: {e}")
        return False

    print(f"  [Phase A] Current price: {current_price:.2f}")

    # Reject if entry price is too far from current price (old signal)
    distance_pct = abs(entry_price - current_price) / current_price * 100
    if distance_pct > 0.5:  # more than 0.5% away = old signal
        print(f"  [Phase A] Signal too old (price {distance_pct:.2f}% away from current) — skipping")
        return False

    # Check for duplicate
    signal_time = f"tv_{entry_price}_{direction}"
    if has_traded(signal_time, direction):
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
        # Record so we don't re-trade
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=entry_price,
            sl=0, tp=0,
            volume=volume,
        )
        add_pending_tp_sl(direction, entry_price, volume)
        return True

    # Build signal object for execute_market_order
    class SimpleSignal:
        pass

    sig = SimpleSignal()
    sig.direction = direction
    sig.entry_price = entry_price
    sig.sl = entry_price  # placeholder — SL/TP applied in Phase B
    sig.tp = entry_price  # placeholder

    live_price = execute_market_order(
        signal=sig,
        page=page,
        volume=volume,
        dry_run=False,
        skip_sl_tp=True,
    )

    if live_price > 0:
        print(f"\n  Trade opened. Live price: {live_price:.2f}")
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=live_price,
            sl=0, tp=0,
            volume=volume,
        )
        add_pending_tp_sl(direction, live_price, volume)
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
            print(f"\n  Entry signal: {entry}")
            print(f"  SL/TP labels: {tpsl}")
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
        context = browser.new_context(storage_state=str(SESSION_FILE))
        page = context.new_page()
        page.goto(GOOEYTRADE_URL)

        try:
            page.wait_for_selector(
                '[data-testid="order-panel-buy-button"]',
                timeout=15000,
            )
        except Exception:
            print("  ERROR: Trade page did not load. Session may be expired.")
            print("  Run: python capture_session.py")
            browser.close()
            sys.exit(1)

        print("  Trade page loaded.\n")

        # --- 4. Phase B first: apply SL/TP to pending trade ---
        phase_b_done = run_phase_b(page, raw, dry_run, args.volume)

        # --- 5. Phase A: check for new entry signal ---
        phase_a_done = run_phase_a(page, raw, dry_run, args.volume)

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
