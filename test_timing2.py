"""Full timing test v3 — with retries."""
import time, sys
from pathlib import Path

results = {}

def tick(name):
    return time.time()

def tock(name, t):
    elapsed = time.time() - t
    results[name] = elapsed
    return elapsed

print("=" * 60)
print("  GooeyTrade Bot — Full Timing Test (v3)")
print("=" * 60)

# ── 1. Imports ──
t = tick("imports")
from config import TV_CHART_URL, TV_SESSION_FILE, GOOEYTRADE_URL, SESSION_FILE, DEFAULT_VOLUME
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels
from state import get_pending_tp_sl, get_trade_count
tock("imports", t)
print(f"\n  [1] Imports:              {results['imports']:.2f}s")

# ── 2. Scrape TradingView ──
t = tick("tv_scrape")
print(f"\n  [2] Scraping TradingView...")
raw = scrape_chart(TV_CHART_URL)
tv_time = tock("tv_scrape", t)
labels = raw.get("labels", [])
print(f"      Time:                 {tv_time:.2f}s")
print(f"      Labels found:         {len(labels)}")
for l in labels:
    print(f"        - {l}")

# ── 3. Parse signals ──
t = tick("parse")
entry = parse_entry_label(raw)
tpsl = parse_tp_sl_labels(raw)
tock("parse", t)
print(f"\n  [3] Parse signals:        {results['parse']:.4f}s")
print(f"      Entry:                {entry}")
print(f"      SL/TP:                {tpsl}")

# ── 4. Launch GooeyTrade browser ──
t = tick("browser_launch")
from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
context = browser.new_context(
    storage_state=str(SESSION_FILE),
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    viewport={"width": 1920, "height": 1080},
)
page = context.new_page()
tock("browser_launch", t)
print(f"\n  [4] Browser launch:       {results['browser_launch']:.2f}s")

# ── 5. Load GooeyTrade ──
t = tick("gt_load")
page.goto(GOOEYTRADE_URL, timeout=30000)
page.wait_for_selector('[data-testid="order-panel-buy-button"]', timeout=20000)
tock("gt_load", t)
print(f"\n  [5] GooeyTrade load:      {results['gt_load']:.2f}s")

# ── 6. Read live price (with retry) ──
t = tick("read_price")
live_price = 0
for attempt in range(5):
    try:
        buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
        price_el = buy_btn.locator(".ui-order-button__price")
        price_text = price_el.inner_text(timeout=3000)
        if price_text.strip():
            live_price = float(price_text.replace(",", ""))
            break
    except Exception:
        pass
    time.sleep(1)
tock("read_price", t)
print(f"\n  [6] Read live price:      {results['read_price']:.2f}s — {live_price:.2f}" if live_price else f"\n  [6] Read live price:      FAILED after 5 attempts")

# ── 7. Check state ──
t = tick("state_check")
pending = get_pending_tp_sl()
trade_count = get_trade_count()
tock("state_check", t)
print(f"\n  [7] State check:          {results['state_check']:.4f}s")
print(f"      Trades so far:        {trade_count}")
print(f"      Pending SL/TP:        {len(pending)}")

# ── 8. Phase B evaluation ──
t = tick("phase_b")
phase_b_action = bool(pending and tpsl)
tock("phase_b", t)
print(f"\n  [8] Phase B eval:         {results['phase_b']:.4f}s")
print(f"      Would execute:        {'YES' if phase_b_action else 'NO'}")

# ── 9. Phase A evaluation ──
t = tick("phase_a")
phase_a_action = False
if entry and live_price > 0:
    dist = abs(entry["entry_price"] - live_price) / live_price * 100
    fresh = dist < 0.5
    from state import has_traded
    sig_key = f"tv_{entry['entry_price']}_{entry['direction']}"
    dup = has_traded(sig_key, entry["direction"])
    phase_a_action = fresh and not dup
    print(f"      Entry price:          {entry['entry_price']:.2f}")
    print(f"      Live price:           {live_price:.2f}")
    print(f"      Distance:             {dist:.3f}%")
    print(f"      Fresh (<0.5%):        {fresh}")
    print(f"      Duplicate:            {dup}")
tock("phase_a", t)
print(f"\n  [9] Phase A eval:         {results['phase_a']:.4f}s")
print(f"      Would execute:        {'YES' if phase_a_action else 'NO'}")

# ── 10. Cleanup ──
t = tick("cleanup")
browser.close()
pw.stop()
tock("cleanup", t)
print(f"\n  [10] Browser close:       {results['cleanup']:.2f}s")

# ── Summary ──
total = sum(results.values())
print(f"\n{'=' * 60}")
print(f"  TIMING SUMMARY")
print(f"{'=' * 60}")
for name, elapsed in results.items():
    pct = elapsed / total * 100
    bar = "█" * max(1, int(pct / 2.5))
    print(f"  {name:<20} {elapsed:>6.2f}s  ({pct:>4.1f}%)  {bar}")
print(f"  {'─' * 54}")
print(f"  {'TOTAL':<20} {total:>6.2f}s  (100%)")
print(f"{'=' * 60}")

if live_price > 0:
    print(f"\n  RESULT: All steps working. Bot would {'TRADE' if phase_a_action or phase_b_action else 'WAIT'}.")
else:
    print(f"\n  RESULT: GooeyTrade price read failed — check session.")
