"""Test updated scraper."""
import time
from playwright.sync_api import sync_playwright
from pathlib import Path

TV_SESSION = Path("tv_session.json")

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(
    storage_state=str(TV_SESSION) if TV_SESSION.exists() else None,
    ignore_https_errors=True,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = ctx.new_page()

url = "https://www.tradingview.com/chart/k6hILD7L/?symbol=OANDA%3AXAUUSD"
print(f"Loading: {url}")
page.goto(url, timeout=60000)

# Wait for chart
print("Waiting for canvas...")
page.wait_for_selector("canvas", timeout=30000)
print("Canvas found!")
time.sleep(12)

# Read signal table
print("\n=== Reading signal table ===")
signal_table = page.evaluate("""() => {
    const signal = {};
    const allCells = [];
    document.querySelectorAll('div, span, td, th').forEach(el => {
        if (el.children.length === 0) {
            const t = el.textContent?.trim();
            if (t && t.length > 0 && t.length < 50) {
                allCells.push(t);
            }
        }
    });
    console.log('Total cells:', allCells.length);

    for (let i = 0; i < allCells.length; i++) {
        const cell = allCells[i].toUpperCase();
        if (/^(BUY|SELL)$/.test(cell)) {
            signal.direction = cell.toLowerCase();
            if (i + 1 < allCells.length) {
                const priceStr = allCells[i + 1].replace(/,/g, '');
                if (/^\\d+\\.\\d+$/.test(priceStr)) {
                    signal.entry_price = parseFloat(priceStr);
                }
            }
        }
        if (cell === 'SL' && i + 1 < allCells.length) {
            const priceStr = allCells[i + 1].replace(/,/g, '');
            if (/^\\d+\\.\\d+$/.test(priceStr)) {
                signal.sl = parseFloat(priceStr);
            }
        }
        if (cell === 'TP' && i + 1 < allCells.length) {
            const priceStr = allCells[i + 1].replace(/,/g, '');
            if (/^\\d+\\.\\d+$/.test(priceStr)) {
                signal.tp = parseFloat(priceStr);
            }
        }
    }
    return signal;
}""")
print(f"Signal table: {signal_table}")

# Check all tables
print("\n=== HTML tables ===")
tables = page.evaluate("""() => {
    const results = [];
    document.querySelectorAll('table').forEach(t => {
        const cells = [];
        t.querySelectorAll('td, th').forEach(c => {
            const txt = c.textContent?.trim();
            if (txt) cells.push(txt);
        });
        if (cells.length > 0) results.push(cells);
    });
    return results;
}""")
for i, t in enumerate(tables):
    print(f"  Table {i}: {t[:10]}")

# Check for Pine table divs specifically
print("\n=== Pine table divs (data-name) ===")
pine_divs = page.evaluate("""() => {
    const results = [];
    document.querySelectorAll('[data-name]').forEach(el => {
        const name = el.getAttribute('data-name');
        const text = el.textContent?.trim()?.substring(0, 100);
        if (text && text.length > 0) results.push({name, text});
    });
    return results.slice(0, 30);
}""")
for d in pine_divs:
    print(f"  [{d['name']}] {d['text'][:60]}")

page.screenshot(path="debug_scraper_test.png")
b.close()
p.stop()
