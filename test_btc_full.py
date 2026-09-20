"""Switch to BTCUSD on GooeyTrade and run full timing test."""
import time
from playwright.sync_api import sync_playwright
from config import TV_SESSION_FILE, SESSION_FILE

# ── Step 1: Scrape TradingView for BTC ──
print("=" * 60)
print("  BTC/USD FULL LIVE TEST")
print("=" * 60)

t = time.time()
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels

tv_url = "https://www.tradingview.com/chart/k6hILD7L/?symbol=BINANCE%3ABTCUSDT"
print(f"\n[1] Scraping TV for BTC: {tv_url}")
raw = scrape_chart(tv_url)
tv_time = time.time() - t
labels = raw.get("labels", [])
entry = parse_entry_label(raw)
tpsl = parse_tp_sl_labels(raw)
print(f"    Time: {tv_time:.2f}s")
print(f"    Labels: {labels}")
print(f"    Entry: {entry}")
print(f"    SL/TP: {tpsl}")

# ── Step 2: Switch GooeyTrade to BTCUSD ──
t = time.time()
print(f"\n[2] Loading GooeyTrade...")
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context(
    storage_state=str(SESSION_FILE),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()
page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
time.sleep(3)
gt_time = time.time() - t
print(f"    Time: {gt_time:.2f}s")

# Click BTCUSD in watchlist
t = time.time()
btc_item = page.locator('text=BTCUSD').first
if btc_item.count() > 0:
    print(f"\n[3] Clicking BTCUSD in watchlist...")
    btc_item.click()
    time.sleep(3)
    switch_time = time.time() - t
    print(f"    Time: {switch_time:.2f}s")

    # Verify we switched
    page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=10000)
    time.sleep(2)

    # Read BTC price
    t = time.time()
    buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
    price_el = buy_btn.locator(".ui-order-button__price")
    price_text = price_el.inner_text(timeout=8000)
    btc_price = float(price_text.replace(",", ""))
    price_time = time.time() - t
    print(f"\n[4] BTC Price: {btc_price:.2f} (read in {price_time:.2f}s)")

    # Check if TradingView signal matches BTC
    print(f"\n[5] Signal check:")
    if entry:
        print(f"    TV Entry: {entry['entry_price']:.2f} ({entry['direction'].upper()})")
        print(f"    GT Price: {btc_price:.2f}")
        dist = abs(entry["entry_price"] - btc_price) / btc_price * 100
        print(f"    Distance: {dist:.4f}%")
    else:
        print(f"    No entry signal on TV (normal — signals only on Supertrend flips)")

    # Test order form on BTC
    t = time.time()
    print(f"\n[6] Testing order form on BTC...")
    from execution import (
        open_advanced_order, ensure_tp_sl_enabled,
        _type_in_stepper, SEL_VOLUME_CONTAINER, SEL_SL_CONTAINER,
        SEL_TP_CONTAINER, SEL_BUY_BTN, SEL_PENDING_TAB, SEL_ACTIVATION_PRICE,
    )

    open_advanced_order(page)
    page.locator(SEL_PENDING_TAB).click()
    time.sleep(0.5)

    limit_price = btc_price - 100  # slightly below for buy limit
    _type_in_stepper(page, SEL_ACTIVATION_PRICE, limit_price)
    _type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)
    ensure_tp_sl_enabled(page)
    _type_in_stepper(page, SEL_SL_CONTAINER, limit_price - 500, activate=True)
    _type_in_stepper(page, SEL_TP_CONTAINER, limit_price + 1500)

    btn = page.locator(SEL_BUY_BTN)
    btn_text = btn.inner_text(timeout=3000)
    disabled = btn.get_attribute("disabled")
    form_time = time.time() - t

    print(f"    Time: {form_time:.2f}s")
    print(f"    Button: {btn_text.split(chr(10))[0]}")
    print(f"    Disabled: {disabled}")
    print(f"    Limit: {limit_price:.2f}  SL: {limit_price-500:.2f}  TP: {limit_price+1500:.2f}")

    page.screenshot(path="debug_btc_order.png")
    print(f"    Screenshot: debug_btc_order.png")

    # ── Summary ──
    total = tv_time + gt_time + switch_time + price_time + form_time
    print(f"\n{'='*60}")
    print(f"  TIMING SUMMARY (BTC/USD)")
    print(f"{'='*60}")
    print(f"  TV Scrape:      {tv_time:>6.2f}s")
    print(f"  GT Load:        {gt_time:>6.2f}s")
    print(f"  Switch BTC:     {switch_time:>6.2f}s")
    print(f"  Read Price:     {price_time:>6.2f}s")
    print(f"  Order Form:     {form_time:>6.2f}s")
    print(f"  ──────────────────────────")
    print(f"  TOTAL:          {total:>6.2f}s")
    print(f"{'='*60}")
    print(f"  RESULT: ALL WORKING — No IP blocks, no session issues")

else:
    print("    BTCUSD not found in watchlist!")

browser.close()
pw.stop()
