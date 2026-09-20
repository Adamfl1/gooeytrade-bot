"""Timing test — measures each step of the bot."""
import time
import sys

t_total = time.time()

# ── 1. Imports ──
t = time.time()
from config import TV_CHART_URL, TV_SESSION_FILE, GOOEYTRADE_URL, SESSION_FILE, DEFAULT_VOLUME
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels
print(f"[1] Imports:          {time.time()-t:.2f}s")

# ── 2. Scrape TradingView ──
t = time.time()
print("\n[2] Scraping TradingView...")
try:
    raw = scrape_chart(TV_CHART_URL)
    tv_time = time.time()-t
    labels = raw.get("labels", [])
    print(f"[2] TV Scrape:        {tv_time:.2f}s")
    print(f"    Labels found:     {len(labels)}")
    for l in labels:
        print(f"      - {l}")
    if raw.get("raw_text"):
        print(f"    Raw text:         {raw['raw_text'][:200]}")
except Exception as e:
    print(f"[2] TV Scrape FAILED: {time.time()-t:.2f}s — {e}")
    raw = {"labels": [], "tables": [], "raw_text": ""}

# ── 3. Parse signals ──
t = time.time()
entry = parse_entry_label(raw)
tpsl = parse_tp_sl_labels(raw)
print(f"\n[3] Parse signals:    {time.time()-t:.4f}s")
print(f"    Entry signal:     {entry}")
print(f"    SL/TP labels:     {tpsl}")

# ── 4. Launch GooeyTrade browser ──
t = time.time()
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
browser = p.chromium.launch(headless=True)
context = browser.new_context(
    storage_state=str(SESSION_FILE),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = context.new_page()
print(f"\n[4] Browser launch:   {time.time()-t:.2f}s")

# ── 5. Load GooeyTrade page ──
t = time.time()
try:
    page.goto(GOOEYTRADE_URL, timeout=30000)
    page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
    print(f"[5] GooeyTrade load:  {time.time()-t:.2f}s")
except Exception as e:
    print(f"[5] GooeyTrade FAILED: {time.time()-t:.2f}s — {e}")
    browser.close()
    p.stop()
    sys.exit(1)

# ── 6. Read live price ──
t = time.time()
try:
    buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
    price_el = buy_btn.locator(".ui-order-button__price")
    price_text = price_el.inner_text(timeout=5000)
    live_price = float(price_text.replace(",", ""))
    print(f"[6] Read live price:  {time.time()-t:.2f}s — {live_price:.2f}")
except Exception as e:
    print(f"[6] Read price FAILED: {time.time()-t:.2f}s — {e}")
    live_price = 0

# ── 7. Phase B check (pending TP/SL) ──
t = time.time()
from state import get_pending_tp_sl
pending = get_pending_tp_sl()
print(f"\n[7] Check pending:    {time.time()-t:.4f}s — {len(pending)} pending trade(s)")

# ── 8. Phase A check (entry signal) ──
t = time.time()
if entry:
    distance_pct = abs(entry["entry_price"] - live_price) / live_price * 100 if live_price else 999
    print(f"[8] Phase A check:    {time.time()-t:.4f}s")
    print(f"    Direction:        {entry['direction'].upper()}")
    print(f"    Entry price:      {entry['entry_price']:.2f}")
    print(f"    Live price:       {live_price:.2f}")
    print(f"    Distance:         {distance_pct:.2f}%")
    print(f"    Fresh (<0.5%):    {'YES' if distance_pct < 0.5 else 'NO — would reject'}")
else:
    print(f"[8] Phase A check:    {time.time()-t:.4f}s — no signal")

# ── Summary ──
print(f"\n{'='*50}")
print(f"  TOTAL TIME:         {time.time()-t_total:.2f}s")
print(f"  TV Scrape:          {tv_time:.2f}s")
print(f"  Browser + Gooey:    ~{(time.time()-t_total - tv_time):.2f}s")
print(f"{'='*50}")

browser.close()
p.stop()
