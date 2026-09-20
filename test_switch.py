"""Find and switch symbol on GooeyTrade."""
import time
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state="session.json",
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()

page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
time.sleep(3)
print(f"Loaded: {page.title()}")

# Click on the XAUUSD symbol to open pair selector
xau = page.locator('text=XAUUSD').first
if xau.count() > 0:
    print("Clicking XAUUSD to open symbol search...")
    xau.click()
    time.sleep(2)
    page.screenshot(path="debug_symbol_search.png")

    # Look for search input
    search = page.locator('input[type="search"], input[placeholder*="Search"], input[placeholder*="search"], [data-testid*="search"]')
    print(f"Search inputs found: {search.count()}")
    for i in range(search.count()):
        placeholder = search.nth(i).get_attribute("placeholder") or ""
        print(f"  [{i}] placeholder: {placeholder}")

    # Try typing BTC
    if search.count() > 0:
        search.first.click()
        time.sleep(0.3)
        search.first.fill("BTC")
        time.sleep(2)
        page.screenshot(path="debug_btc_search.png")

        # Look for results
        results = page.locator('[data-testid*="result"], [data-testid*="item"], li').all()
        btc_found = False
        for r in results[:20]:
            try:
                txt = r.inner_text(timeout=500)
                if "BTC" in txt.upper():
                    print(f"  BTC result: {txt}")
                    btc_found = True
            except:
                pass

        if not btc_found:
            # Check all visible text
            all_text = page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('li, [role="option"], [data-testid]').forEach(el => {
                    const t = el.textContent?.trim();
                    if (t && t.includes('BTC')) results.push(t.substring(0, 100));
                });
                return results;
            }""")
            print(f"  BTC in page: {all_text}")

b.close()
p.stop()
