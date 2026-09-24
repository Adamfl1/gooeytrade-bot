"""
Capture a Playwright storageState from GooeyTrade.

Run this ONCE manually:
    python capture_session.py

A headed browser opens → log in to GooeyTrade manually →
press Enter in the terminal → cookies/storageState saved to session.json.

The saved file is reused by the trading bot on every scheduled run.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

SESSION_FILE = Path("session.json")
GOOEYTRADE_URL = "https://mtr.gooeytrade.com/app/trade"


def main():
    print("=" * 60)
    print("  GooeyTrade Session Capture")
    print("=" * 60)
    print()
    print("A browser window will open to GooeyTrade.")
    print("Log in manually (competition account is fine).")
    print("Once you are logged in and can see the trade page,")
    print("come back here and press Enter.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(GOOEYTRADE_URL)

        # Auto-detect login: wait for the trade page buy button (up to 5 min)
        print("Log in to GooeyTrade in the browser window...")
        print("Waiting for trade page to load (auto-detects login)...")
        try:
            page.wait_for_selector(
                '[data-testid="order-panel-buy-button"]',
                timeout=300000,
            )
            print("Buy button found — session looks good!")
        except Exception:
            print("WARNING: Could not find buy button. Session may be invalid.")

        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    print(f"Session saved to {SESSION_FILE}")
    print(f"File size: {SESSION_FILE.stat().st_size} bytes")


if __name__ == "__main__":
    main()
