"""Test GooeyTrade order form interaction — dry run."""
import time
from playwright.sync_api import sync_playwright
from config import GOOEYTRADE_URL, SESSION_FILE
from execution import (
    open_advanced_order, ensure_tp_sl_enabled,
    _type_in_stepper, SEL_VOLUME_CONTAINER, SEL_SL_CONTAINER,
    SEL_TP_CONTAINER, SEL_BUY_BTN, SEL_PENDING_TAB,
    SEL_ACTIVATION_PRICE,
)

print("Launching browser...")
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
context = browser.new_context(
    storage_state=str(SESSION_FILE),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = context.new_page()

print("Loading GooeyTrade...")
t = time.time()
page.goto(GOOEYTRADE_URL, timeout=30000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
print(f"  Page loaded in {time.time()-t:.2f}s")

# Read live price
try:
    buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
    price_el = buy_btn.locator(".ui-order-button__price")
    price_text = price_el.inner_text(timeout=5000)
    live_price = float(price_text.replace(",", ""))
    print(f"  Live price: {live_price:.2f}")
except Exception as e:
    print(f"  Price read failed: {e}")
    live_price = 0

# Test advanced order panel
print("\n--- Testing Advanced Order Panel ---")
try:
    t = time.time()
    open_advanced_order(page)
    print(f"  Opened advanced order: {time.time()-t:.2f}s")

    # Switch to Pending tab
    t = time.time()
    page.locator(SEL_PENDING_TAB).click()
    time.sleep(0.5)
    print(f"  Switched to Pending tab: {time.time()-t:.2f}s")

    # Fill activation price
    t = time.time()
    _type_in_stepper(page, SEL_ACTIVATION_PRICE, 4375.00)
    print(f"  Set activation price: {time.time()-t:.2f}s")

    # Fill volume
    t = time.time()
    _type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)
    print(f"  Set volume: {time.time()-t:.2f}s")

    # Enable SL/TP
    t = time.time()
    ensure_tp_sl_enabled(page)
    print(f"  SL/TP toggles: {time.time()-t:.2f}s")

    # Fill SL
    t = time.time()
    _type_in_stepper(page, SEL_SL_CONTAINER, 4360.00, activate=True)
    print(f"  Set SL: {time.time()-t:.2f}s")

    # Fill TP
    t = time.time()
    _type_in_stepper(page, SEL_TP_CONTAINER, 4420.00)
    print(f"  Set TP: {time.time()-t:.2f}s")

    # Check button state
    btn = page.locator(SEL_BUY_BTN)
    btn_text = btn.inner_text(timeout=3000)
    disabled = btn.get_attribute("disabled")
    print(f"\n  Buy button text: {btn_text.split(chr(10))[0]}")
    print(f"  Buy button disabled: {disabled}")

    # Screenshot
    page.screenshot(path="debug_order_form.png")
    print("  Screenshot saved: debug_order_form.png")

    print("\n  === ALL SELECTORS WORKING ===")

except Exception as e:
    print(f"  FAILED: {e}")
    page.screenshot(path="debug_order_form_error.png")

browser.close()
pw.stop()
