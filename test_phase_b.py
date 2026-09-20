"""Test Phase B: check if we can find open positions and edit SL/TP."""
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
        page.screenshot(path="debug_phase_b.png")
        browser.close()
        exit(1)

    time.sleep(3)

    # Check for open positions
    positions = page.locator('[data-testid="open-positions-desktop-list-row"]')
    pos_count = positions.count()
    print(f"\nOpen positions found: {pos_count}")

    if pos_count > 0:
        for i in range(pos_count):
            row = positions.nth(i)
            # Read direction badge
            badges = row.locator(".ui-badge")
            for bi in range(badges.count()):
                text = badges.nth(bi).inner_text(timeout=2000).strip()
                print(f"  Position {i}: badge = {text}")
            # Read volume
            vol_el = row.locator('[data-testid="open-position-volume"]')
            if vol_el.count() > 0:
                print(f"  Position {i}: volume = {vol_el.inner_text(timeout=2000).strip()}")
            # Try clicking TPSL button
            tpsl_btn = row.locator('[data-testid="open-positions-desktop-tpsl-btn"]')
            if tpsl_btn.count() > 0:
                print(f"  Position {i}: TPSL button found - clicking...")
                tpsl_btn.click()
                time.sleep(2)
                # Check if edit dialog appeared
                dialog = page.locator('[data-testid="trade-open-position-tp-sl-edit"]')
                if dialog.count() > 0 and dialog.is_visible():
                    print(f"  Position {i}: SL/TP EDIT DIALOG OPENED SUCCESSFULLY")
                    # Check toggles
                    toggles = dialog.locator('[data-testid="tp-sl-toggle-header-element"]')
                    print(f"  Position {i}: Toggle count = {toggles.count()}")
                    # Close dialog
                    cancel = page.locator('[data-testid="position-edit-dialog-cancel-btn"]')
                    if cancel.count() > 0:
                        cancel.click()
                        time.sleep(1)
                else:
                    print(f"  Position {i}: SL/TP dialog did NOT appear")
    else:
        print("No open positions. Checking pending orders...")
        # Check pending orders tab
        try:
            page.get_by_text("Pending Orders", exact=True).click()
            time.sleep(2)
            empty = page.locator('[data-testid="empty-pending-orders-disclaimer"]')
            if empty.count() > 0:
                print("No pending orders either.")
            else:
                print("Pending orders found!")
        except Exception as e:
            print(f"Could not check pending orders: {e}")

    # Take a screenshot for evidence
    page.screenshot(path="debug_phase_b.png")
    print("\nScreenshot saved: debug_phase_b.png")
    browser.close()
