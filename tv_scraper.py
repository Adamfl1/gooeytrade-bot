"""
TradingView chart scraper — reads Pine Script signal table via OCR.

The Pine Script outputs a table (table.new) in the top-right corner:
  Row 0: "BUY" / "SELL" + entry price
  Row 1: "SL" + SL price
  Row 2: "TP" + TP price

The table renders on canvas (not DOM), so we screenshot + OCR to read it.

Usage:
    from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels
    raw = scrape_chart("https://www.tradingview.com/chart/XXXXX/")
    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
"""

import os
import time
import re
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from PIL import Image

# Ensure Tesseract is on PATH
_tesseract_path = r"C:\Program Files\Tesseract-OCR"
if os.path.isdir(_tesseract_path) and _tesseract_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _tesseract_path + ";" + os.environ.get("PATH", "")

try:
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    print("  [scraper] WARNING: pytesseract not installed — OCR disabled")

TV_SESSION_FILE = Path("tv_session.json")

# Table crop region (top-right corner of 1920x1080 chart, includes all 4 rows: BUY/SL/TP/FRESH)
TABLE_CROP = (1635, 42, 1815, 210)


def scrape_chart(chart_url: str, timeout: int = 30000) -> dict:
    """Open a TradingView chart, screenshot it, OCR the signal table.

    Returns:
        {
            "signal_table": {"direction": "buy", "entry_price": 4340.28, "sl": 4167.58, "tp": 4858.37},
            "ocr_text": "BUY 4340.28\\nSL 4167.58\\nTP 4858.37",
            "labels": [...],   # legacy fallback
            "tables": [...],   # legacy fallback
            "raw_text": "...", # legacy fallback
        }
    """
    result = {
        "signal_table": {},
        "ocr_text": "",
        "labels": [],
        "tables": [],
        "raw_text": "",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        session_file = TV_SESSION_FILE
        ctx_kwargs = {
            "ignore_https_errors": True,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
        }
        if session_file.exists():
            ctx_kwargs["storage_state"] = str(session_file)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            page.goto(chart_url, timeout=timeout)
            page.wait_for_selector("canvas", timeout=timeout)
            # Wait for Pine Script to render
            time.sleep(15)

            # Screenshot + OCR
            if HAS_OCR:
                screenshot_path = "debug_tv_screenshot.png"
                page.screenshot(path=screenshot_path)
                result["signal_table"] = _ocr_table(screenshot_path)

            # Legacy DOM methods (fallback)
            result["labels"] = page.evaluate("""() => {
                const labels = [];
                document.querySelectorAll('[data-entity-id]').forEach(el => {
                    const t = el.textContent?.trim();
                    if (t && t.length > 0 && t.length < 200) labels.push(t);
                });
                return labels;
            }""")

            result["raw_text"] = page.evaluate("""() => {
                const parts = [];
                const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walk.nextNode()) {
                    const t = walk.currentNode.textContent.trim();
                    if (t.length > 0 && t.length < 200 &&
                        /\\b(buy|sell|sl|tp|signal|entry|long|short|stop|take)\\b/i.test(t)) {
                        parts.push(t);
                    }
                }
                return [...new Set(parts)].join(' | ');
            }""")

        except PlaywrightTimeout:
            print("  [scraper] Timeout waiting for chart to load")
        except Exception as e:
            print(f"  [scraper] Error: {e}")
        finally:
            browser.close()

    return result


def _ocr_table(screenshot_path: str) -> dict:
    """OCR the signal table from a chart screenshot."""
    try:
        import pytesseract
    except ImportError:
        return {}

    img = Image.open(screenshot_path)
    cropped = img.crop(TABLE_CROP)
    cropped.save("debug_ocr_crop.png")

    # Run OCR
    text = pytesseract.image_to_string(cropped, config="--psm 6")
    print(f"  [OCR] Raw text: {repr(text)}")
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    print(f"  [OCR] Lines: {lines}")

    signal = {}
    for line in lines:
        upper = line.upper()

        if re.match(r'^(BUY|SELL)\b', upper):
            m = re.search(r'(\d[\d,]*\.?\d+)', line)
            if m:
                price = float(m.group(1).replace(",", ""))
                if 100 < price < 100000:
                    signal["direction"] = upper.split()[0].lower()
                    signal["entry_price"] = price

        elif re.match(r'^SL\b', upper):
            m = re.search(r'(\d[\d,]*\.?\d+)', line)
            if m:
                price = float(m.group(1).replace(",", ""))
                if 100 < price < 100000:
                    signal["sl"] = price

        elif re.match(r'^TP\b', upper):
            m = re.search(r'(\d[\d,]*\.?\d+)', line)
            if m:
                price = float(m.group(1).replace(",", ""))
                if 100 < price < 100000:
                    signal["tp"] = price

        elif "FRESH" in upper:
            signal["status"] = "fresh"
        elif "OLD" in upper:
            signal["status"] = "old"

    print(f"  [OCR] Parsed: {signal}")
    return signal


# ── Phase A: Entry signal detection ──────────────────────────────────────────

def parse_entry_label(raw: dict) -> dict | None:
    """Detect entry signal. Checks OCR table first, then DOM fallbacks."""

    # Method 1: OCR table (primary)
    st = raw.get("signal_table", {})
    if st.get("direction") and st.get("entry_price"):
        return {"direction": st["direction"], "entry_price": st["entry_price"]}

    # Method 2: canvas labels (legacy)
    labels = raw.get("labels", [])
    for item in labels:
        text = str(item).strip()
        m = re.match(r'^(BUY|SELL)\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if m:
            direction = m.group(1).lower()
            price = float(m.group(2).replace(",", ""))
            if 100 < price < 100000:
                return {"direction": direction, "entry_price": price}

    # Method 3: raw_text fallback
    raw_text = raw.get("raw_text", "")
    m = re.search(r'\b(BUY|SELL)\s+([\d,]+\.?\d*)\b', raw_text, re.IGNORECASE)
    if m:
        direction = m.group(1).lower()
        price = float(m.group(2).replace(",", ""))
        if 100 < price < 100000:
            return {"direction": direction, "entry_price": price}

    return None


# ── Phase B: SL/TP detection ────────────────────────────────────────────────

def parse_tp_sl_labels(raw: dict) -> dict | None:
    """Detect TP and SL labels. Checks OCR table first."""

    # Method 1: OCR table (primary)
    st = raw.get("signal_table", {})
    if st.get("sl") and st.get("tp"):
        return {"sl": st["sl"], "tp": st["tp"]}

    # Method 2: canvas labels (legacy)
    labels = raw.get("labels", [])
    sl = None
    tp = None
    for item in labels:
        text = str(item).strip()
        sl_match = re.match(r'^SL\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if sl_match:
            sl = float(sl_match.group(1).replace(",", ""))
            continue
        tp_match = re.match(r'^TP\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if tp_match:
            tp = float(tp_match.group(1).replace(",", ""))
    if sl is not None and tp is not None:
        return {"sl": sl, "tp": tp}

    # Method 3: raw_text fallback
    raw_text = raw.get("raw_text", "")
    if sl is None:
        sl_m = re.search(r'\bSL\s+([\d,]+\.?\d*)\b', raw_text, re.IGNORECASE)
        if sl_m:
            sl = float(sl_m.group(1).replace(",", ""))
    if tp is None:
        tp_m = re.search(r'\bTP\s+([\d,]+\.?\d*)\b', raw_text, re.IGNORECASE)
        if tp_m:
            tp = float(tp_m.group(1).replace(",", ""))
    if sl is not None and tp is not None:
        return {"sl": sl, "tp": tp}

    return None


# ── Combined signal ──────────────────────────────────────────────────────────

def parse_signal(raw: dict) -> dict | None:
    """Parse a complete signal (direction + SL + TP + status) from one scrape."""
    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    if entry and tpsl:
        st = raw.get("signal_table", {})
        return {
            "direction": entry["direction"],
            "entry_price": entry["entry_price"],
            "sl": tpsl["sl"],
            "tp": tpsl["tp"],
            "status": st.get("status", "unknown"),
        }
    return None


# ── Quick test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD"
    print(f"Scraping: {url}")
    raw = scrape_chart(url)
    print(f"Signal table: {raw['signal_table']}")
    print(f"OCR text: {raw['ocr_text']}")
    print(f"Labels: {raw['labels']}")
    print()

    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    combined = parse_signal(raw)

    print(f"Phase A (entry):  {entry}")
    print(f"Phase B (tp/sl):  {tpsl}")
    print(f"Combined signal:  {combined}")
