"""Quick test: does the GooeyTrade session work?"""
import time
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state="session.json",
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()
if HAS_STEALTH:
    stealth_sync(page)
    print("Stealth mode: ON")
else:
    print("Stealth mode: OFF (not installed)")

page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)
time.sleep(10)
print(f"URL: {page.url}")
print(f"Title: {page.title()}")

el = page.locator('[data-testid="order-panel-buy-button"]')
if el.count() > 0:
    print("SUCCESS: Buy button found!")
else:
    print("FAIL: No buy button")
    page.screenshot(path="debug_test.png")

b.close()
p.stop()
