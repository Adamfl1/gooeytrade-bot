"""Check GooeyTrade available symbols."""
import time
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state="tv_session.json",
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()

# Load GooeyTrade
page.goto("https://mtr.gooeytrade.com/app/trade", timeout=30000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
time.sleep(3)

# Get current symbol name
print(f"URL: {page.url}")

# Check what symbol is shown
try:
    # Look for symbol name in the header
    symbol_el = page.locator('[data-testid="symbol-name"]')
    if symbol_el.count() > 0:
        print(f"Symbol: {symbol_el.inner_text(timeout=3000)}")
except:
    pass

# Try to find a symbol search or market watch
try:
    # Look for watchlist or market selector
    watchlist = page.locator('[data-testid="watchlist"]')
    if watchlist.count() > 0:
        print("Watchlist found")
        # Get all items
        items = watchlist.locator("li, .item, [data-symbol]").all()
        for item in items[:20]:
            txt = item.inner_text(timeout=1000)
            if txt.strip():
                print(f"  - {txt.strip()}")
except Exception as e:
    print(f"Watchlist: {e}")

# Try to find all clickable elements with symbol-like text
try:
    all_text = page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('[data-testid]').forEach(el => {
            const testid = el.getAttribute('data-testid');
            const text = el.textContent?.trim().substring(0, 80);
            if (testid && text) results.push(testid + ': ' + text);
        });
        return results.slice(0, 50);
    }""")
    print("\nAll data-testid elements:")
    for t in all_text:
        print(f"  {t}")
except Exception as e:
    print(f"Eval failed: {e}")

# Get buy button text
try:
    buy = page.locator('[data-testid="order-panel-buy-button"]')
    print(f"\nBuy button: {buy.inner_text(timeout=3000)}")
except Exception as e:
    print(f"Buy: {e}")

try:
    sell = page.locator('[data-testid="order-panel-sell-button"]')
    print(f"Sell button: {sell.inner_text(timeout=3000)}")
except Exception as e:
    print(f"Sell: {e}")

page.screenshot(path="debug_gooey_symbols.png")

b.close()
p.stop()
