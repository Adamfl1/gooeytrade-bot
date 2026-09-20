"""Test script: screenshot GooeyTrade from GitHub Actions runner."""
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state="session.json",
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = context.new_page()
    page.goto("https://mtr.gooeytrade.com/app/trade", timeout=60000)

    try:
        page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=30000)
        print("OK: Trade page loaded - buy button found")
    except Exception:
        print("WARN: buy button not found")

    time.sleep(5)

    title = page.title()
    url = page.url
    body_text = page.inner_text("body")[:3000]

    print(f"Page title: {title}")
    print(f"Page URL: {url}")

    lower = body_text.lower()
    if "checking your browser" in lower or "cloudflare" in lower:
        print("RESULT: CLOUDFLARE BLOCK DETECTED")
    elif "captcha" in lower:
        print("RESULT: CAPTCHA DETECTED")
    elif "just a moment" in lower:
        print("RESULT: CLOUDFLARE JUST A MOMENT PAGE")
    else:
        print("RESULT: NO BLOCK DETECTED - page loaded normally")

    print(f"Body preview: {body_text[:500]}")

    page.screenshot(path="screenshot_gooeytrade.png", full_page=True)
    print("Saved screenshot_gooeytrade.png")
    browser.close()
