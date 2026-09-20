"""Submit BTCUSD BUY LIMIT order on GooeyTrade."""
import time
from playwright.sync_api import sync_playwright
from config import SESSION_FILE

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state=str(SESSION_FILE),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()

print("Loading GooeyTrade...")
page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
time.sleep(3)

# Switch to BTCUSD
print("Switching to BTCUSD...")
page.locator("text=BTCUSD").first.click()
time.sleep(3)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=10000)
time.sleep(2)

# Read BTC price
buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
price_el = buy_btn.locator(".ui-order-button__price")
price_text = price_el.inner_text(timeout=5000)
btc_price = float(price_text.replace(",", ""))
print(f"BTC Price: {btc_price:.2f}")

# Open advanced order, fill form
from execution import (
    open_advanced_order, ensure_tp_sl_enabled,
    _type_in_stepper, SEL_VOLUME_CONTAINER, SEL_SL_CONTAINER,
    SEL_TP_CONTAINER, SEL_BUY_BTN, SEL_PENDING_TAB, SEL_ACTIVATION_PRICE,
    wait_for_button_enabled, submit_order,
)

limit_price = btc_price - 100
sl_price = limit_price - 500
tp_price = limit_price + 1500

print(f"Opening order form...")
open_advanced_order(page)
page.locator(SEL_PENDING_TAB).click()
time.sleep(0.5)

print(f"Setting price: {limit_price:.2f}")
_type_in_stepper(page, SEL_ACTIVATION_PRICE, limit_price)

print(f"Setting volume: 0.01")
_type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)

ensure_tp_sl_enabled(page)

print(f"Setting SL: {sl_price:.2f}")
_type_in_stepper(page, SEL_SL_CONTAINER, sl_price, activate=True)

print(f"Setting TP: {tp_price:.2f}")
_type_in_stepper(page, SEL_TP_CONTAINER, tp_price)

# Verify button
btn = page.locator(SEL_BUY_BTN)
btn_text = btn.inner_text(timeout=3000)
print(f"\nReady to submit: {btn_text.split(chr(10))[0]}")

# SUBMIT
print("\nSubmitting BUY LIMIT order...")
submit_order(page, "buy")
time.sleep(3)

# Verify
page.screenshot(path="debug_btc_submitted.png")

# Check pending orders
try:
    page.locator("text=Pending Orders").first.click()
    time.sleep(2)
    page.screenshot(path="debug_btc_pending.png")
    print("Checked Pending Orders tab")
except:
    pass

print("\nDone!")
b.close()
p.stop()
