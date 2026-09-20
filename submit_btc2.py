"""Submit BTCUSD BUY LIMIT — fixed SL value."""
import time
from playwright.sync_api import sync_playwright
from config import SESSION_FILE
from execution import (
    open_advanced_order, ensure_tp_sl_enabled,
    _type_in_stepper, SEL_VOLUME_CONTAINER, SEL_SL_CONTAINER,
    SEL_TP_CONTAINER, SEL_BUY_BTN, SEL_PENDING_TAB, SEL_ACTIVATION_PRICE,
    SEL_STEPPER_INPUT,
)

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

# Calculate proper levels — SL well below entry, TP above
entry = btc_price - 100   # buy limit below market
sl = entry - 2000          # $2000 below entry
tp = entry + 4000          # $4000 above entry (2:1 RR)

print(f"Entry: {entry:.2f}  SL: {sl:.2f}  TP: {tp:.2f}")

# Open advanced order
print("\nOpening order form...")
open_advanced_order(page)
page.locator(SEL_PENDING_TAB).click()
time.sleep(0.5)

# Fill form
print("Setting activation price...")
_type_in_stepper(page, SEL_ACTIVATION_PRICE, entry)

print("Setting volume 0.01...")
_type_in_stepper(page, SEL_VOLUME_CONTAINER, 0.01)

# Enable and fill SL
print("Enabling SL...")
ensure_tp_sl_enabled(page)

print(f"Setting SL: {sl:.2f}")
sl_container = page.locator(SEL_SL_CONTAINER)
sl_input = sl_container.locator(SEL_STEPPER_INPUT)
sl_input.wait_for(state="visible", timeout=5000)
sl_input.click(click_count=3)
time.sleep(0.1)
page.keyboard.press("Control+a")
page.keyboard.insert_text(str(sl))
time.sleep(0.2)
page.keyboard.press("Tab")
time.sleep(0.5)

# Read back what SL actually shows
sl_value = sl_container.locator(SEL_STEPPER_INPUT).inner_text(timeout=2000)
print(f"  SL displayed: {sl_value}")

# Enable and fill TP
print(f"Setting TP: {tp:.2f}")
tp_container = page.locator(SEL_TP_CONTAINER)
tp_input = tp_container.locator(SEL_STEPPER_INPUT)
tp_input.wait_for(state="visible", timeout=5000)
tp_input.click(click_count=3)
time.sleep(0.1)
page.keyboard.press("Control+a")
page.keyboard.insert_text(str(tp))
time.sleep(0.2)
page.keyboard.press("Tab")
time.sleep(0.5)

tp_value = tp_container.locator(SEL_STEPPER_INPUT).inner_text(timeout=2000)
print(f"  TP displayed: {tp_value}")

page.screenshot(path="debug_btc_fixed.png")

# Check button
btn = page.locator(SEL_BUY_BTN)
disabled = btn.get_attribute("disabled")
btn_text = btn.inner_text(timeout=3000)
print(f"\nButton: {btn_text.split(chr(10))[0]}")
print(f"Disabled attr: {disabled}")

# Try clicking with force
print("\nClicking BUY LIMIT...")
try:
    btn.click(force=True, timeout=5000)
    time.sleep(3)
    print("Clicked!")
except Exception as e:
    print(f"Click failed: {e}")
    # Try JS click
    print("Trying JS click...")
    btn.evaluate("el => el.click()")
    time.sleep(3)

# Handle confirmation
confirm = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
if confirm.count() > 0:
    try:
        confirm.wait_for(state="visible", timeout=5000)
        confirm.click()
        time.sleep(2)
        print("Confirmed!")
    except:
        pass

page.screenshot(path="debug_btc_after_click.png")

# Check pending orders
try:
    page.locator("text=Pending Orders").first.click()
    time.sleep(2)
    page.screenshot(path="debug_btc_pending_final.png")
    print("Pending Orders tab checked")
except:
    pass

print("\nDone!")
b.close()
p.stop()
