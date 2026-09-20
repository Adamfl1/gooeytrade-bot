"""
Full BTC/USD integration test:
  1. Scrape TradingView BTC/USD chart via OCR
  2. Switch GooeyTrade to BTCUSD
  3. Open a market BUY order (0.01 lot)
  4. Verify position appears in open positions
  5. Apply SL/TP via Phase B (edit dialog)
  6. Report results

Usage:
  python test_btc_trade.py              # live test
  python test_btc_trade.py --dry-run    # dry run (no submit)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import TV_SESSION_FILE, SESSION_FILE, TV_CHART_URL
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels
from execution import (
    open_advanced_order, ensure_tp_sl_enabled, _type_in_stepper, submit_order,
    SEL_VOLUME_CONTAINER, SEL_SL_CONTAINER, SEL_TP_CONTAINER,
    SEL_BUY_BTN, SEL_SELL_BTN, SEL_OPEN_POSITIONS, SEL_POSITION_ROW,
    SEL_POSITION_TPSL_BTN, SEL_POSITION_EDIT_DIALOG, SEL_POSITION_EDIT_TOGGLE,
    SEL_POSITION_EDIT_VALUE, SEL_POSITION_EDIT_SAVE, SEL_STEPPER_INPUT,
)

BTC_TV_URL = "https://www.tradingview.com/chart/k6hILD7L/?symbol=BINANCE%3ABTCUSDT"
RESULTS = []


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    RESULTS.append(line)


def step(name):
    print(f"\n{'='*60}")
    print(f"  STEP: {name}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="BTC/USD Integration Test")
    parser.add_argument("--dry-run", action="store_true", help="Don't submit orders")
    args = parser.parse_args()
    dry_run = args.dry_run

    log(f"Starting BTC/USD integration test (dry_run={dry_run})")

    # ── Step 1: Scrape TradingView for BTC ──
    step("Scrape TradingView BTC/USD")
    t = time.time()
    try:
        raw = scrape_chart(BTC_TV_URL)
        entry = parse_entry_label(raw)
        tpsl = parse_tp_sl_labels(raw)
        tv_time = time.time() - t
        log(f"TV scrape: {tv_time:.1f}s")
        log(f"Signal table: {raw.get('signal_table', {})}")
        log(f"Entry: {entry}")
        log(f"SL/TP: {tpsl}")
    except Exception as e:
        log(f"TV scrape failed: {e}", "ERROR")
        entry = None
        tpsl = None

    # ── Step 2: Launch GooeyTrade ──
    step("Launch GooeyTrade")
    import os
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=is_ci)
    ctx = browser.new_context(
        storage_state=str(SESSION_FILE),
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = ctx.new_page()
    page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)

    try:
        page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=30000)
        log("Trade page loaded")
    except Exception:
        log("FAIL: Trade page did not load", "ERROR")
        browser.close()
        pw.stop()
        sys.exit(1)

    time.sleep(3)

    # ── Step 3: Switch to BTCUSD ──
    step("Switch to BTCUSD")
    t = time.time()
    btc_item = page.locator('text=BTCUSD').first
    if btc_item.count() > 0:
        btc_item.click()
        time.sleep(3)
        page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=10000)
        time.sleep(2)
        switch_time = time.time() - t
        log(f"Switched to BTCUSD ({switch_time:.1f}s)")
    else:
        log("BTCUSD not found in watchlist!", "ERROR")
        browser.close()
        pw.stop()
        sys.exit(1)

    # ── Step 4: Read BTC price ──
    step("Read BTC Price")
    btc_price = 0
    for attempt in range(5):
        try:
            buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
            buy_btn.wait_for(state="visible", timeout=10000)
            price_el = buy_btn.locator(".ui-order-button__price")
            price_text = price_el.inner_text(timeout=5000)
            if price_text.strip():
                btc_price = float(price_text.replace(",", ""))
                log(f"BTC price: {btc_price:.2f} (attempt {attempt+1})")
                break
        except Exception as e:
            log(f"Price read attempt {attempt+1} failed: {e}", "WARN")
            time.sleep(2)
    if btc_price == 0:
        page.screenshot(path="debug_btc_price_fail.png")
        log("Could not read BTC price after 5 attempts", "ERROR")

    if btc_price == 0:
        log("Cannot proceed without BTC price", "ERROR")
        browser.close()
        pw.stop()
        sys.exit(1)

    # ── Step 5: Open market BUY order ──
    step("Open Market BUY Order (0.01 lot)")
    t = time.time()

    if dry_run:
        log("DRY RUN - filling form only, not submitting")
        open_advanced_order(page)
        _type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)
        btn = page.locator(SEL_BUY_BTN)
        btn_text = btn.inner_text(timeout=3000)
        log(f"Button text: {btn_text.split(chr(10))[0]}")
        log(f"DRY RUN - skipping submit")
    else:
        try:
            open_advanced_order(page)
            _type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)
            time.sleep(1)

            log(f"Submitting BUY market order @ ~{btc_price:.2f}...")
            submit_order(page, "buy")
            time.sleep(3)

            # Verify position appeared
            positions = page.locator(SEL_POSITION_ROW)
            pos_count = positions.count()
            log(f"Open positions after trade: {pos_count}")

            if pos_count > 0:
                log("Market order placed successfully!")
            else:
                log("WARNING: No position found after submit", "WARN")
        except Exception as e:
            log(f"Trade failed: {e}", "ERROR")

    order_time = time.time() - t
    log(f"Order step: {order_time:.1f}s")

    # ── Step 6: Verify open positions ──
    step("Verify Open Positions")
    positions = page.locator(SEL_POSITION_ROW)
    pos_count = positions.count()
    log(f"Open positions: {pos_count}")

    if pos_count > 0:
        for i in range(pos_count):
            row = positions.nth(i)
            badges = row.locator(".ui-badge")
            badge_texts = []
            for bi in range(badges.count()):
                try:
                    badge_texts.append(badges.nth(bi).inner_text(timeout=2000).strip())
                except Exception:
                    pass
            log(f"  Position {i}: {badge_texts}")

    # ── Step 7: Test Phase B (SL/TP dialog) ──
    step("Test Phase B - SL/TP Edit Dialog")
    if pos_count > 0:
        row = positions.first
        tpsl_btn = row.locator(SEL_POSITION_TPSL_BTN)
        tpsl_btn.click()
        time.sleep(2)

        dialog = page.locator(SEL_POSITION_EDIT_DIALOG)
        if dialog.count() > 0 and dialog.is_visible():
            log("SL/TP dialog opened")

            toggles = dialog.locator(SEL_POSITION_EDIT_TOGGLE)
            log(f"Toggles: {toggles.count()}")

            value_inputs = dialog.locator(SEL_POSITION_EDIT_VALUE)
            log(f"Value inputs: {value_inputs.count()}")

            save_btn = page.locator(SEL_POSITION_EDIT_SAVE)
            cancel_btn = page.locator('[data-testid="position-edit-dialog-cancel-btn"]')
            log(f"Save button: visible={save_btn.is_visible()}")
            log(f"Cancel button: found={cancel_btn.count() > 0}")

            # Try toggling SL on and entering a value
            if toggles.count() > 0:
                toggle_sl = toggles.nth(0)
                toggle_sl.click()
                time.sleep(0.5)
                log("Toggled SL ON")

                # Enter SL value (slightly below current price)
                sl_input = value_inputs.nth(0).locator(SEL_STEPPER_INPUT)
                if sl_input.count() > 0:
                    sl_value = round(btc_price * 0.99, 2)  # 1% below
                    sl_input.click(click_count=3)
                    time.sleep(0.05)
                    page.keyboard.press("Control+a")
                    page.keyboard.insert_text(str(sl_value))
                    time.sleep(0.1)
                    log(f"Entered SL value: {sl_value}")

            if toggles.count() > 1:
                toggle_tp = toggles.nth(1)
                toggle_tp.click()
                time.sleep(0.5)
                log("Toggled TP ON")

                # Enter TP value (slightly above current price)
                tp_input = value_inputs.nth(1).locator(SEL_STEPPER_INPUT)
                if tp_input.count() > 0:
                    tp_value = round(btc_price * 1.03, 2)  # 3% above
                    tp_input.click(click_count=3)
                    time.sleep(0.05)
                    page.keyboard.press("Control+a")
                    page.keyboard.insert_text(str(tp_value))
                    time.sleep(0.1)
                    log(f"Entered TP value: {tp_value}")

            if dry_run:
                log("DRY RUN - clicking Cancel instead of Save")
                cancel_btn.click()
                time.sleep(1)
            else:
                # Click Save
                save_btn.wait_for(state="visible", timeout=5000)
                start = time.time()
                while time.time() - start < 5:
                    if save_btn.get_attribute("disabled") is None:
                        break
                    time.sleep(0.3)
                save_btn.click()
                time.sleep(2)

                # Handle confirmation popup
                confirm = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
                if confirm.count() > 0:
                    try:
                        confirm.wait_for(state="visible", timeout=5000)
                        confirm.click()
                        time.sleep(2)
                        log("Confirmed!")
                    except Exception:
                        pass

                log("SL/TP saved!")
        else:
            log("SL/TP dialog did NOT open", "ERROR")
    else:
        log("No open positions to test Phase B on", "WARN")

    # ── Summary ──
    step("TEST RESULTS")
    for line in RESULTS:
        print(line)

    print(f"\n{'='*60}")
    print(f"  BTC/USD INTEGRATION TEST COMPLETE")
    print(f"{'='*60}")

    browser.close()
    pw.stop()


if __name__ == "__main__":
    main()
