"""Test Phase B: debug TPSL dialog opening."""
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
        print("OK: Trade page loaded")
    except Exception:
        print("FAIL: Trade page did not load")
        browser.close()
        exit(1)

    time.sleep(5)

    # Find open positions
    positions = page.locator('[data-testid="open-positions-desktop-list-row"]')
    pos_count = positions.count()
    print(f"Open positions: {pos_count}")

    if pos_count > 0:
        row = positions.first
        print("Clicking TPSL button on first position...")
        tpsl_btn = row.locator('[data-testid="open-positions-desktop-tpsl-btn"]')
        print(f"TPSL button count: {tpsl_btn.count()}")
        print(f"TPSL button visible: {tpsl_btn.is_visible()}")
        tpsl_btn.click()
        
        # Wait longer and check for dialog
        print("Waiting 5 seconds for dialog...")
        time.sleep(5)
        
        page.screenshot(path="debug_tpsl_after_click.png")
        
        # Check for dialog with various selectors
        for sel in [
            '[data-testid="trade-open-position-tp-sl-edit"]',
            '[data-testid="position-edit-dialog"]',
            '.modal',
            '[role="dialog"]',
            '[data-testid="tp-sl-edit"]',
        ]:
            el = page.locator(sel)
            cnt = el.count()
            vis = el.is_visible() if cnt > 0 else False
            print(f"  Selector '{sel}': count={cnt}, visible={vis}")
        
        # Also check if there are any overlays/modals
        overlays = page.locator('.overlay, .modal, [role="dialog"], [data-testid*="dialog"], [data-testid*="modal"]')
        print(f"\nOverlay/modal elements found: {overlays.count()}")
        for i in range(min(overlays.count(), 5)):
            ov = overlays.nth(i)
            tag = ov.evaluate("el => el.tagName")
            testid = ov.get_attribute("data-testid") or "none"
            cls = ov.get_attribute("class") or "none"
            print(f"  [{i}] <{tag}> data-testid={testid} class={cls[:80]}")

    browser.close()
    print("\nDone.")
