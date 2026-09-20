"""Test GooeyTrade with BTC symbol search."""
import time, json
from pathlib import Path
from playwright.sync_api import sync_playwright

session = json.loads(Path("session.json").read_text())
print(f"Session cookies: {len(session.get('cookies', []))}")

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state="session.json",
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()

print("Loading GooeyTrade...")
page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)
time.sleep(10)
print(f"  URL: {page.url}")
print(f"  Title: {page.title()}")

# Screenshot to see state
page.screenshot(path="debug_gooey_fresh.png")

# Check if we're on the trade page
buy = page.locator('[data-testid="order-panel-buy-button"]')
if buy.count() > 0:
    try:
        price_el = buy.locator(".ui-order-button__price")
        price_text = price_el.inner_text(timeout=5000)
        print(f"  Current price: {price_text}")
    except:
        print("  Buy button exists but no price")

    # Look for symbol name
    try:
        # Try various selectors for symbol display
        for sel in ['[data-testid="symbol-name"]', '.symbol-name', '.chart-symbol']:
            el = page.locator(sel)
            if el.count() > 0:
                print(f"  Symbol: {el.inner_text(timeout=2000)}")
                break
    except:
        pass
else:
    print("  No buy button - checking page state...")
    # Maybe need to wait more or page redirected
    time.sleep(5)
    page.screenshot(path="debug_gooey_fresh2.png")
    print(f"  Final URL: {page.url}")

    # Check for any error messages
    errors = page.locator(".error, .alert, [data-testid*='error']").all()
    for e in errors:
        print(f"  Error: {e.inner_text(timeout=1000)}")

# Try clicking on the symbol to open search
try:
    # Look for symbol area / pair selector
    pair_btns = page.locator('[data-testid*="pair"], [data-testid*="symbol"], [data-testid*="instrument"]').all()
    for btn in pair_btns[:5]:
        txt = btn.inner_text(timeout=1000)
        print(f"  Found: {txt}")
except:
    pass

b.close()
p.stop()
