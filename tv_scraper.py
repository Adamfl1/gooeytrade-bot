"""
TradingView chart scraper — reads Pine Script label outputs via headless Chromium.

Your indicator draws labels in TWO phases:
  Phase A (entry candle+1): "BUY 4192.23" or "SELL 4192.23"
  Phase B (entry candle+2): "SL 4177.50" and "TP 4950.37"

This scraper detects both phases separately so the bot can:
  1. Open a trade on Phase A (entry signal)
  2. Apply SL/TP on Phase B (next candle)

Usage:
    from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels
    raw = scrape_chart("https://www.tradingview.com/chart/XXXXX/")
    entry = parse_entry_label(raw)   # Phase A
    tpsl = parse_tp_sl_labels(raw)   # Phase B
"""

import time
import re
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TV_SESSION_FILE = Path("tv_session.json")


def scrape_chart(chart_url: str, timeout: int = 30000) -> dict:
    """Open a TradingView chart and read all label/text output.

    Returns:
        {
            "labels": ["BUY 4192.23", "SL 4177.50", "TP 4950.37", ...],
            "tables": [{"cells": [...]}],
            "raw_text": "BUY 4192.23 | SL 4177.50 | ..."
        }
    """
    result = {"labels": [], "tables": [], "raw_text": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        if TV_SESSION_FILE.exists():
            context = browser.new_context(
                storage_state=str(TV_SESSION_FILE),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
        else:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )

        page = context.new_page()

        try:
            page.goto(chart_url, timeout=timeout)
            page.wait_for_selector(
                ".chart-markup-table, .chart-container, canvas",
                timeout=timeout,
            )
            # Wait for Pine Script to render labels
            time.sleep(10)

            # 1. Read Pine Script labels (label.new output)
            #    These are the most reliable source for signals
            result["labels"] = page.evaluate("""() => {
                const labels = [];
                // Method: data-entity-id elements (Pine label.new)
                document.querySelectorAll('[data-entity-id]').forEach(el => {
                    const t = el.textContent?.trim();
                    if (t && t.length > 0 && t.length < 200) labels.push(t);
                });
                // Also check for label elements in the chart overlay
                document.querySelectorAll('.chart-markup-table .label, .label').forEach(el => {
                    const t = el.textContent?.trim();
                    if (t && t.length > 0 && t.length < 200 && !labels.includes(t)) {
                        labels.push(t);
                    }
                });
                return labels;
            }""")

            # 2. Read table cells (table.new output — fallback)
            result["tables"] = page.evaluate("""() => {
                const tables = [];
                document.querySelectorAll('table').forEach(table => {
                    const cells = [];
                    table.querySelectorAll('td, th').forEach(cell => {
                        const t = cell.textContent?.trim();
                        if (t && t.length > 0) cells.push(t);
                    });
                    if (cells.length > 0) tables.push({cells});
                });
                return tables;
            }""")

            # 3. Read all signal-related text from the page
            result["raw_text"] = page.evaluate("""() => {
                const parts = [];
                const walk = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT
                );
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


# ── Phase A: Entry signal detection ──────────────────────────────────────────

def parse_entry_label(raw: dict) -> dict | None:
    """Phase A: Detect entry signal from labels.

    Your Pine Script draws: "BUY 4192.23" (green, label_up)
                        or: "SELL 4192.23" (red, label_down)

    Returns: {"direction": "buy"/"sell", "entry_price": 4192.23}
    """
    labels = raw.get("labels", [])

    for item in labels:
        text = str(item).strip()

        # Match "BUY 4192.23" or "SELL 4192.23"
        m = re.match(r'^(BUY|SELL)\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if m:
            direction = m.group(1).lower()
            price = float(m.group(2).replace(",", ""))
            if 1000 < price < 10000:  # sanity check for XAUUSD
                return {"direction": direction, "entry_price": price}

    # Fallback: check raw_text
    raw_text = raw.get("raw_text", "")
    m = re.search(r'\b(BUY|SELL)\s+([\d,]+\.?\d*)\b', raw_text, re.IGNORECASE)
    if m:
        direction = m.group(1).lower()
        price = float(m.group(2).replace(",", ""))
        if 1000 < price < 10000:
            return {"direction": direction, "entry_price": price}

    return None


# ── Phase B: SL/TP detection ────────────────────────────────────────────────

def parse_tp_sl_labels(raw: dict) -> dict | None:
    """Phase B: Detect TP and SL labels (appear on candle after entry).

    Your Pine Script draws: "SL 4177.50" (red) and "TP 4950.37" (teal)

    Returns: {"sl": 4177.50, "tp": 4950.37}
    """
    labels = raw.get("labels", [])
    sl = None
    tp = None

    for item in labels:
        text = str(item).strip()

        # Match "SL 4177.50"
        sl_match = re.match(r'^SL\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if sl_match:
            sl = float(sl_match.group(1).replace(",", ""))
            continue

        # Match "TP 4950.37"
        tp_match = re.match(r'^TP\s+([\d,]+\.?\d*)$', text, re.IGNORECASE)
        if tp_match:
            tp = float(tp_match.group(1).replace(",", ""))

    if sl is not None and tp is not None:
        return {"sl": sl, "tp": tp}

    # Fallback: check raw_text
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


# ── Combined signal (all-in-one, for backward compatibility) ─────────────────

def parse_signal(raw: dict) -> dict | None:
    """Try to parse a complete signal (direction + SL + TP) from one scrape.

    Returns dict with direction, entry_price, sl, tp if ALL found.
    For two-phase flow, use parse_entry_label() and parse_tp_sl_labels() instead.
    """
    # Method 1: tables
    signal = _parse_signal_from_tables(raw.get("tables", []))
    if signal:
        return signal

    # Method 2: labels — check if BUY/SELL + SL + TP all present
    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    if entry and tpsl:
        return {
            "direction": entry["direction"],
            "entry_price": entry["entry_price"],
            "sl": tpsl["sl"],
            "tp": tpsl["tp"],
        }

    return None


def _parse_signal_from_tables(tables: list[dict]) -> dict | None:
    """Parse table cells into a signal dict (legacy fallback)."""
    for table in tables:
        cells = table.get("cells", [])
        text = " ".join(cells).upper()

        direction = None
        if re.search(r'\b(BUY|LONG|ACHETER)\b', text):
            direction = "buy"
        elif re.search(r'\b(SELL|SHORT|VENDRE)\b', text):
            direction = "sell"
        else:
            continue

        sl = None
        tp = None
        for cell in cells:
            cell_upper = cell.upper().strip()
            sl_match = re.search(r'SL[:\s=]*(\d+\.?\d*)', cell_upper)
            tp_match = re.search(r'TP[:\s=]*(\d+\.?\d*)', cell_upper)
            if sl_match:
                sl = float(sl_match.group(1))
            if tp_match:
                tp = float(tp_match.group(1))

        if direction and sl and tp:
            return {"direction": direction, "sl": sl, "tp": tp}

    return None


# ── Quick test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tradingview.com/chart/"
    print(f"Scraping: {url}")
    raw = scrape_chart(url)
    print(f"Labels: {raw['labels']}")
    print(f"Tables: {raw['tables']}")
    print(f"Raw text: {raw['raw_text'][:300]}")
    print()

    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    combined = parse_signal(raw)

    print(f"Phase A (entry):  {entry}")
    print(f"Phase B (tp/sl):  {tpsl}")
    print(f"Combined signal:  {combined}")
