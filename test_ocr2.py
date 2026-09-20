"""Test ddddocr on TradingView chart table."""
import time
from playwright.sync_api import sync_playwright
from PIL import Image
import ddddocr

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

# Full screenshot
page.screenshot(path="debug_ocr_full.png")
b.close()
p.stop()

# Crop the table region (top-right corner based on screenshot)
img = Image.open("debug_ocr_full.png")
# Table is roughly at x:1640-1820, y:45-110
cropped = img.crop((1630, 40, 1830, 115))
cropped.save("debug_ocr_crop.png")
print(f"Cropped: {cropped.size}")

# Run OCR
ocr = ddddocr.DdddOcr(show_ad=False)
with open("debug_ocr_crop.png", "rb") as f:
    img_bytes = f.read()
result = ocr.classification(img_bytes)
print(f"\nOCR result: '{result}'")

# Also try full image
print("\nFull image OCR (filtered):")
with open("debug_ocr_full.png", "rb") as f:
    img_bytes = f.read()
result2 = ocr.classification(img_bytes)
print(f"  {result2[:500]}")
