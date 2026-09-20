"""OCR the Pine table from TradingView screenshot."""
import os
os.environ["PATH"] = r"C:\Program Files\Tesseract-OCR;" + os.environ.get("PATH", "")

import time
import re
from playwright.sync_api import sync_playwright
from PIL import Image
import pytesseract

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
page.screenshot(path="debug_ocr_test.png")
b.close()
p.stop()

# Crop the table region (top-right, based on screenshot: x:1640-1810, y:45-100)
img = Image.open("debug_ocr_test.png")
cropped = img.crop((1635, 42, 1815, 130))
cropped.save("debug_table_crop.png")
print(f"Cropped: {cropped.size}")

# Run Tesseract OCR
text = pytesseract.image_to_string(cropped, config="--psm 6")
print(f"\nRaw OCR output:\n{text}")

# Parse the output
lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
print(f"\nParsed lines: {lines}")

# Extract signal
signal = {}
for i, line in enumerate(lines):
    if "BUY" in line.upper():
        signal["direction"] = "buy"
        # Price might be on same line or next
        m = re.search(r'(\d[\d,]*\.?\d+)', line)
        if m:
            signal["entry_price"] = float(m.group(1).replace(",", ""))
    elif "SELL" in line.upper():
        signal["direction"] = "sell"
        m = re.search(r'(\d[\d,]*\.?\d+)', line)
        if m:
            signal["entry_price"] = float(m.group(1).replace(",", ""))
    elif "SL" in line.upper():
        m = re.search(r'(\d[\d,]*\.?\d+)', line)
        if m:
            signal["sl"] = float(m.group(1).replace(",", ""))
    elif "TP" in line.upper():
        m = re.search(r'(\d[\d,]*\.?\d+)', line)
        if m:
            signal["tp"] = float(m.group(1).replace(",", ""))

print(f"\nParsed signal: {signal}")

# Also try with different PSM modes
for psm in [3, 4, 6, 11, 12]:
    text2 = pytesseract.image_to_string(cropped, config=f"--psm {psm}")
    if text2.strip():
        print(f"\nPSM {psm}: {text2.strip()}")
