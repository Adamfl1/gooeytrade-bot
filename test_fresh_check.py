"""Take a fresh screenshot and check if FRESH/OLD row was added to the table."""
import os
os.environ["PATH"] = r"C:\Program Files\Tesseract-OCR;" + os.environ.get("PATH", "")

import time
import pytesseract
from playwright.sync_api import sync_playwright
from PIL import Image

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
page.screenshot(path="debug_fresh_check.png")
b.close()
p.stop()

img = Image.open("debug_fresh_check.png")

# Wide crop of table area (top-right)
top_right = img.crop((1550, 30, 1850, 250))
top_right.save("debug_fresh_crop.png")

txt = pytesseract.image_to_string(top_right, config="--psm 6")
print("Table OCR:")
print(txt)

# Check for FRESH or OLD
upper = txt.upper()
if "FRESH" in upper:
    print(">>> SIGNAL IS FRESH!")
elif "OLD" in upper:
    print(">>> SIGNAL IS OLD / EXPIRED")
else:
    print(">>> NO FRESH/OLD row found in table")
