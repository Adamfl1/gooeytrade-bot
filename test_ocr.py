"""Test OCR on TradingView chart screenshot."""
import time
import re
from playwright.sync_api import sync_playwright
from pathlib import Path

# Take screenshot
print("Taking screenshot...")
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state="tv_session.json",
    ignore_https_errors=True,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()
page.goto("https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD", timeout=60000)
page.wait_for_selector("canvas", timeout=30000)
time.sleep(15)

# Crop top-right corner where the table is (based on screenshot: ~1640,45 to 1820,110)
from PIL import Image
page.screenshot(path="debug_full_chart_full.png")
# Crop top-right corner where the table is
img = Image.open("debug_full_chart_full.png")
cropped = img.crop((1620, 40, 1830, 120))
cropped.save("debug_full_chart.png")
print("Cropped + full screenshots saved")
print("Cropped screenshot saved: debug_full_chart.png")

b.close()
p.stop()

# Run OCR
print("\nRunning OCR...")
import easyocr
reader = easyocr.Reader(["en"], gpu=False)

# Read cropped image
result = reader.readtext("debug_full_chart.png")
print(f"\nOCR results ({len(result)} items):")
for bbox, text, conf in result:
    print(f"  '{text}' (confidence: {conf:.2f}) at {bbox}")

# Also try full image
print("\nFull chart OCR:")
result2 = reader.readtext("debug_full_chart_full.png")
for bbox, text, conf in result2:
    if re.search(r'(BUY|SELL|SL|TP|\d{4}\.\d)', text, re.IGNORECASE):
        print(f"  '{text}' (confidence: {conf:.2f}) at {bbox}")
