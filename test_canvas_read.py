"""Read Pine table from canvas using color detection."""
import time
import re
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from pathlib import Path

def scrape_chart_ocr(chart_url: str) -> dict:
    """Screenshot the chart, crop the table region, return raw OCR data."""
    result = {"signal_table": {}, "screenshot_path": None}

    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(
        storage_state="tv_session.json" if Path("tv_session.json").exists() else None,
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page = ctx.new_page()
    page.goto(chart_url, timeout=60000)
    page.wait_for_selector("canvas", timeout=30000)
    time.sleep(15)

    # Take full screenshot
    page.screenshot(path="debug_chart_screenshot.png")
    b.close()
    p.stop()

    # Load image
    img = Image.open("debug_chart_screenshot.png")
    arr = np.array(img)

    # The Pine table is in the top-right corner
    # From our screenshots: roughly x:1640-1820, y:45-110
    # Scan for the table by looking for the dark background with colored text

    # Crop a generous region
    table_region = arr[30:130, 1600:1850]

    # Detect colored pixels (non-dark, non-grey)
    # White text (BUY/SELL, prices): R>200, G>200, B>200
    # Red text (SL): R>200, G<100, B<100
    # Teal text (TP): R<100, G>150, B>150

    h, w = table_region.shape[:2]

    # Find rows of text by looking for bright pixels
    bright_mask = np.any(table_region > 150, axis=2)
    row_has_text = np.any(bright_mask, axis=1)

    # Find text rows
    text_rows = []
    in_text = False
    start = 0
    for i in range(h):
        if row_has_text[i] and not in_text:
            start = i
            in_text = True
        elif not row_has_text[i] and in_text:
            text_rows.append((start, i))
            in_text = False
    if in_text:
        text_rows.append((start, h))

    print(f"Found {len(text_rows)} text rows in table region")
    for i, (y1, y2) in enumerate(text_rows):
        row_slice = table_region[y1:y2, :]

        # Detect dominant color
        bright_pixels = row_slice[np.any(row_slice > 150, axis=2)]
        if len(bright_pixels) == 0:
            continue
        avg_color = bright_pixels.mean(axis=0)

        if avg_color[0] > 200 and avg_color[1] < 100:
            color = "RED"
        elif avg_color[1] > 150 and avg_color[2] > 150:
            color = "TEAL"
        elif avg_color[0] > 200 and avg_color[1] > 200:
            color = "WHITE"
        else:
            color = "OTHER"

        # Save row as image for debugging
        row_img = Image.fromarray(row_slice)
        row_img.save(f"debug_row_{i}.png")

        print(f"  Row {i}: y={y1}-{y2} color={color} avg_rgb=({avg_color[0]:.0f},{avg_color[1]:.0f},{avg_color[2]:.0f})")

    return result

# Test
print("Scraping XAUUSD chart...")
scrape_chart_ocr("https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD")
