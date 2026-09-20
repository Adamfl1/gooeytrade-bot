"""Test BTC/USD on TradingView + GooeyTrade."""
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

# Test BTC/USD
url = "https://www.tradingview.com/chart/k6hILD7L/?symbol=BINANCE%3ABTCUSDT"
print(f"TradingView: {url}")
page.goto(url, timeout=30000)
time.sleep(10)
print(f"  URL:   {page.url}")
print(f"  Title: {page.title()}")

entities = page.locator("[data-entity-id]").count()
print(f"  Entities: {entities}")

texts = page.evaluate("""() => {
    const parts = [];
    document.querySelectorAll('[data-entity-id]').forEach(el => {
        const t = el.textContent?.trim();
        if (t && t.length > 0 && t.length < 200) parts.push(t);
    });
    return parts;
}""")
print(f"  Texts: {texts}")
page.screenshot(path="debug_btc_tv.png")

# Check GooeyTrade BTC
print("\nGooeyTrade:")
page2 = ctx.new_page()
page2.goto("https://mtr.gooeytrade.com/app/trade", timeout=30000)
time.sleep(3)

# Try to search for BTC in GooeyTrade
try:
    # Look for symbol search
    search = page2.locator('[data-testid="symbol-search-input"]')
    if search.count() > 0:
        search.click()
        time.sleep(0.5)
        search.fill("BTC")
        time.sleep(2)
        print("  Searched for BTC")
        page2.screenshot(path="debug_btc_gooey_search.png")
    else:
        print("  No search input found")
except Exception as e:
    print(f"  Search failed: {e}")

# Try switching symbol via URL
page3 = ctx.new_page()
page3.goto("https://mtr.gooeytrade.com/app/trade?symbol=BTCUSD", timeout=30000)
time.sleep(5)
print(f"  URL: {page3.url}")
print(f"  Title: {page3.title()}")

try:
    buy_btn = page3.locator('[data-testid="order-panel-buy-button"]')
    price_el = buy_btn.locator(".ui-order-button__price")
    price_text = price_el.inner_text(timeout=5000)
    print(f"  BTC price: {price_text}")
except Exception as e:
    print(f"  Price read: {e}")

page3.screenshot(path="debug_btc_gooey.png")

b.close()
p.stop()
