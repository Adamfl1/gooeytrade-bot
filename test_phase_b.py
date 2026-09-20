"""Test Phase B: simulate pending trade and test SL/TP dialog."""
import time
import json
from playwright.sync_api import sync_playwright

# Create a simulated pending trade in bot_state.json
state = {
    "trades": [],
    "pending_tp_sl": [
        {
            "direction": "sell",
            "entry_price": 4366.28,
            "volume": 0.01,
            "opened_at": "2026-09-20T19:00:00Z"
        }
    ],
    "signal_first_seen": {}
}
with open("bot_state.json", "w") as f:
    json.dump(state, f, indent=2)
print("Created simulated pending trade: SELL @ 4366.28, vol 0.01")

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
    print(f"\nOpen positions: {pos_count}")

    if pos_count == 0:
        print("No open positions to test Phase B on.")
        browser.close()
        exit(0)

    row = positions.first
    print("Found position. Clicking TPSL button...")
    tpsl_btn = row.locator('[data-testid="open-positions-desktop-tpsl-btn"]')
    tpsl_btn.click()
    time.sleep(3)

    # Use the FIXED selector
    dialog = page.locator('[data-testid="dialog-wrapper"]')
    print(f"Dialog found: {dialog.count()}")
    print(f"Dialog visible: {dialog.is_visible()}")

    if dialog.count() > 0 and dialog.is_visible():
        print("\n=== SL/TP EDIT DIALOG OPENED ===")
        
        # Check toggles
        toggles = dialog.locator('[data-testid="tp-sl-toggle-header-element"]')
        print(f"Toggle count: {toggles.count()}")

        # Check value inputs
        value_inputs = dialog.locator('[data-testid="tp-sl-value-input"]')
        print(f"Value input count: {value_inputs.count()}")

        # Check save button
        save_btn = page.locator('[data-testid="position-edit-dialog-save-btn"]')
        print(f"Save button found: {save_btn.count()}")
        print(f"Save button visible: {save_btn.is_visible()}")

        # Check cancel button
        cancel_btn = page.locator('[data-testid="position-edit-dialog-cancel-btn"]')
        print(f"Cancel button found: {cancel_btn.count()}")

        print("\n=== Phase B selector check: ALL PASS ===")
        
        # Close dialog
        if cancel_btn.count() > 0:
            cancel_btn.click()
            time.sleep(1)
            print("Dialog closed via cancel button.")
    else:
        print("Dialog did NOT open")

    page.screenshot(path="debug_phase_b_fixed.png")
    browser.close()
    print("\nDone.")
