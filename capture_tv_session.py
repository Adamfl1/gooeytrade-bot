"""
Capture a Playwright storageState from TradingView.

Run this ONCE manually:
    python capture_tv_session.py

A headed browser opens -> log in to TradingView manually ->
press Enter in the terminal -> cookies/storageState saved to tv_session.json.

The saved file is reused by the bot on every scheduled run.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

SESSION_FILE = Path("tv_session.json")
TRADINGVIEW_URL = "https://www.tradingview.com/"


def main():
    print("=" * 60)
    print("  TradingView Session Capture")
    print("=" * 60)
    print()
    print("A browser window will open to TradingView.")
    print("Log in manually with your account.")
    print("Once you are logged in and can see your charts,")
    print("come back here and press Enter.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(TRADINGVIEW_URL)
        input("Press Enter after you are logged in...")

        # Verify we're logged in
        try:
            page.wait_for_selector(
                '[data-name="header-profile"]',
                timeout=5000,
            )
            print("Profile icon found — session looks good!")
        except Exception:
            print("WARNING: Could not find profile icon. Session may be invalid.")

        context.storage_state(path=str(SESSION_FILE))
        browser.close()

    print(f"Session saved to {SESSION_FILE}")
    print(f"File size: {SESSION_FILE.stat().st_size} bytes")


if __name__ == "__main__":
    main()
