"""Scrape XAUUSD TV labels and analyze signals."""
import time
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from pathlib import Path

TV_SESSION = Path("tv_session.json")

# Scrape TV
print("Scraping TradingView XAUUSD...")
t = time.time()
p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state=str(TV_SESSION),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()
url = "https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD"
page.goto(url, timeout=30000)
time.sleep(10)
print(f"  Scrape time: {time.time()-t:.2f}s")

# Get ALL entity texts
entities = page.evaluate("""() => {
    const results = [];
    document.querySelectorAll('[data-entity-id]').forEach(el => {
        const id = el.getAttribute('data-entity-id');
        const text = el.textContent?.trim();
        const tag = el.tagName;
        results.push({id, text, tag});
    });
    return results;
}""")
print(f"\nAll {len(entities)} Pine entities:")
for e in entities:
    print(f"  [{e['tag']}] id={e['id'][:20]}... text={e['text'][:100]}")

# Get all text on the page that looks like signals
all_text = page.evaluate("""() => {
    const parts = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) {
        const t = walk.currentNode.textContent.trim();
        if (t.length > 0 && t.length < 200 &&
            /\\b(buy|sell|sl|tp|signal|entry|long|short|stop|take|\\d{4,}\\.\\d+)\\b/i.test(t)) {
            parts.push(t);
        }
    }
    return [...new Set(parts)];
}""")
print(f"\nSignal-related text on page ({len(all_text)}):")
for t in all_text:
    print(f"  {t}")

# Check for pine drawings (lines, labels, boxes, tables)
pine_labels = page.evaluate("""() => {
    const results = [];
    document.querySelectorAll('.pine-label, [class*="pine"]').forEach(el => {
        results.push(el.textContent?.trim());
    });
    return results.filter(t => t && t.length > 0 && t.length < 200);
}""")
print(f"\nPine labels: {pine_labels}")

pine_tables = page.evaluate("""() => {
    const results = [];
    document.querySelectorAll('table').forEach(table => {
        const cells = [];
        table.querySelectorAll('td, th').forEach(cell => {
            const t = cell.textContent?.trim();
            if (t && t.length > 0) cells.push(t);
        });
        if (cells.length > 0) results.push(cells);
    });
    return results;
}""")
print(f"\nPine tables: {pine_tables}")

# Check for any BUY/SELL/SL/TP in the entire page
buy_sell = page.evaluate("""() => {
    const results = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) {
        const t = walk.currentNode.textContent.trim();
        if (/^(BUY|SELL|SL|TP)\\s+\\d/i.test(t)) {
            results.push(t);
        }
    }
    return results;
}""")
print(f"\nExact BUY/SELL/SL/TP labels: {buy_sell}")

b.close()
p.stop()
