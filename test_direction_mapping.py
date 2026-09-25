"""Direction mapping trace, end to end.

  real BUY screenshot -> _ocr_table() -> run_phase_a signal dict
      -> execute_market_order's `direction = signal.direction.upper()`
      -> execution.submit_order() -> which [data-testid] button is clicked

Also synthesises a SELL table image so the same crop+regex path is checked
for SELL. Expected: buy -> advanced-order-buy-button,
                    sell -> advanced-order-sell-button.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

import execution
from tv_scraper import _ocr_table

BUY_SHOT = "debug_tv_screenshot.png"          # real TradingView capture
SELL_SHOT = "debug_direction_sell_synth.png"  # generated below
CROP = (1635, 42, 1815, 210)

EXPECTED = {"buy": "buy", "BUY": "buy", "sell": "sell", "SELL": "sell"}

HTML = """
<!doctype html><html><body>
<button data-testid="advanced-order-buy-button"
        onclick="window.__clicked='buy'">BUY 4274.70</button>
<button data-testid="advanced-order-sell-button"
        onclick="window.__clicked='sell'">SELL 4274.50</button>
</body></html>
"""


def make_sell_screenshot(path: str) -> None:
    """Render a Pine-style signal table so OCR sees a SELL row."""
    img = Image.new("RGB", (1920, 1080), (18, 18, 18))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    lines = ["SELL 4284.20", "SL 4296.37", "TP 4246.05", "Status FRESH"]
    y = CROP[1] + 6
    for line in lines:
        draw.text((CROP[0] + 6, y), line, fill=(255, 255, 255), font=font)
        y += 34
    img.save(path)


def click_with(page, value: str) -> str:
    page.evaluate("() => { window.__clicked = null; }")
    execution.submit_order(page, value)
    return page.evaluate("() => window.__clicked")


def main() -> int:
    failures: list[tuple[str, str, str]] = []

    # ── 1. OCR direction (real capture + synthesised SELL) ───────────────
    buy_dir = _ocr_table(BUY_SHOT).get("direction")
    make_sell_screenshot(SELL_SHOT)
    sell_dir = _ocr_table(SELL_SHOT).get("direction")
    print(f"\n  OCR {BUY_SHOT}      -> direction={buy_dir!r}")
    print(f"  OCR {SELL_SHOT} -> direction={sell_dir!r}\n")
    for got, want in ((buy_dir, "buy"), (sell_dir, "sell")):
        if got != want:
            failures.append((f"ocr({want})", want, str(got)))

    # ── 2. run_phase_a builds the signal exactly like this ───────────────
    class SimpleSignal:
        pass

    for direction in (buy_dir, sell_dir):
        sig = SimpleSignal()
        sig.direction = direction
        sig.entry_price = 0.0
        sig.sl = 0
        sig.tp = 0
        # the two lines that matter inside execute_market_order:
        d = sig.direction.upper()          # L347 (case: same as run_phase_a input)
        print(f"  run_phase_a signal.direction={sig.direction!r} "
              f"-> execute_market_order passes {d!r} to submit_order")

    # ── 3. real click through production submit_order ────────────────────
    print()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(HTML)

        for value in ("buy", buy_dir.upper(), "sell", sell_dir.upper()):
            clicked = click_with(page, value)
            want = EXPECTED[value]
            ok = clicked == want
            if not ok:
                failures.append((value, want, str(clicked)))
            print(f"  [{'OK ' if ok else 'BUG'}] submit_order({value!r:6}) -> "
                  f"clicked={clicked!r:6} expected={want!r}")

        browser.close()

    print()
    if failures:
        print(f"  DIRECTION INVERSION: {len(failures)} failure(s)")
        for case, want, got in failures:
            print(f"    {case}: expected {want!r}, got {got!r}")
        return 1
    print("  PASS: BUY -> buy button, SELL -> sell button")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
