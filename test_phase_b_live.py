"""Live Phase B test — no Phase A, opens NO new trades.

1. Scrape TradingView → SL/TP labels
2. Load GooeyTrade → list open positions
3. Seed a backdated pending_sl entry matching an open position
4. Call run_phase_b(live) → real edit dialog → save
5. Verify + screenshots; restore original pending_sl.json
"""

import json
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import TV_CHART_URL, GOOEYTRADE_URL, SESSION_FILE
from tv_scraper import scrape_chart, parse_tp_sl_labels, parse_entry_label
from execution import (
    load_pending_sl, save_pending_sl, PENDING_SL_FILE,
    SEL_POSITION_ROW, SEL_POSITION_VOLUME,
)
import run as runmod


def floor5(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def main():
    print("=" * 60)
    print("  PHASE B LIVE TEST")
    print("=" * 60)

    # ── 1. Scrape TradingView ────────────────────────────────────────────
    print("\n[1] Scraping TradingView...")
    raw = scrape_chart(TV_CHART_URL)
    print(f"  signal_table: {raw.get('signal_table')}")
    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    print(f"  entry label:  {entry}")
    print(f"  SL/TP labels: {tpsl}")

    if not tpsl:
        print("  !! No SL/TP labels readable on chart — dialog test will be "
              "skipped (gate path still tested)")

    # ── 2. Load GooeyTrade + list positions ──────────────────────────────
    print("\n[2] Loading GooeyTrade...")
    backup = Path("pending_sl.json.bak")
    if PENDING_SL_FILE.exists():
        shutil.copy2(PENDING_SL_FILE, backup)
        print(f"  backed up pending_sl.json → {backup}")

    result = {"labels": tpsl, "positions": [], "phase_b": None,
              "after": None}

    with sync_playwright() as p:
        import os
        is_ci = (os.environ.get("CI") == "true"
                 or os.environ.get("GITHUB_ACTIONS") == "true")
        browser = p.chromium.launch(headless=is_ci)
        context = browser.new_context(
            storage_state=str(SESSION_FILE),
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto(GOOEYTRADE_URL, timeout=60000)
        try:
            page.wait_for_selector(
                '[data-testid="order-panel-buy-button"]', timeout=30000)
        except Exception as e:
            page.screenshot(path="debug_phase_b_fail.png")
            print(f"  !! Trade page did not load: {e}")
            browser.close()
            _restore(backup)
            print("\nRESULT: SESSION/PAGE LOAD FAIL")
            raise SystemExit(1)
        print("  trade page loaded")
        page.screenshot(path="debug_phase_b_before.png")

        rows = page.locator(SEL_POSITION_ROW)
        try:
            rows.first.wait_for(state="visible", timeout=8000)
        except Exception:
            print("  !! No open positions on the account — cannot live-test "
                  "the dialog")
            browser.close()
            _restore(backup)
            print("\nRESULT: NO OPEN POSITION")
            return

        positions = []
        for i in range(rows.count()):
            row = rows.nth(i)
            badges = row.locator(".ui-badge")
            badge_texts = []
            for bi in range(badges.count()):
                badge_texts.append(badges.nth(bi).inner_text(timeout=2000).strip())
            vol_el = row.locator(SEL_POSITION_VOLUME)
            vol_text = vol_el.first.inner_text(timeout=2000).strip() if vol_el.count() else "?"
            direction = None
            for t in badge_texts:
                tl = t.lower()
                if tl in ("acheter", "buy", "long"):
                    direction = "buy"
                elif tl in ("vendre", "sell", "short"):
                    direction = "sell"
            positions.append({"i": i, "badges": badge_texts,
                              "volume_text": vol_text, "direction": direction})
        result["positions"] = positions
        print(f"  open positions: {positions}")

        target = next((x for x in positions if x["direction"]), None)
        if target is None:
            print("  !! Could not determine direction of any position")
            browser.close()
            _restore(backup)
            print("\nRESULT: NO MATCHABLE POSITION")
            return

        direction = target["direction"]
        try:
            volume = float(target["volume_text"].replace(",", ""))
        except ValueError:
            volume = 2.5
        print(f"  target: {direction.upper()} volume={volume}")

        # ── 3. Seed backdated pending entry (candle already closed) ──────
        now = datetime.now(timezone.utc)
        start = floor5(now - timedelta(minutes=10))
        entries = load_pending_sl()
        entries.append({
            "direction": direction,
            "entry_price": 0,
            "volume": volume,
            "entry_candle_start": start.isoformat(),
            "resolved": False,
            "opened_at": now.isoformat(),
            "test": True,
        })
        save_pending_sl(entries)
        print(f"  seeded pending_sl (candle {start.isoformat()}, closed)")

        # ── 4. Run Phase B live ──────────────────────────────────────────
        print("\n[3] Running run_phase_b(live)...")
        ok = runmod.run_phase_b(page, raw, dry_run=False, volume=volume)
        result["phase_b"] = ok
        print(f"  run_phase_b returned: {ok}")

        time.sleep(1)
        page.screenshot(path="debug_phase_b_after.png")

        # ── 5. Verify: re-read pending state + position row ─────────────
        after = load_pending_sl()
        result["after"] = after
        print(f"\n  pending_sl after: {json.dumps(after, indent=2)}")

        rows2 = page.locator(SEL_POSITION_ROW)
        try:
            rows2.first.wait_for(state="visible", timeout=5000)
            r = rows2.nth(target["i"]) if target["i"] < rows2.count() else rows2.first
            tpsl_cells = r.locator(
                '[data-testid*="sl"], [data-testid*="tp"], '
                '[data-testid*="stop"], [data-testid*="take"]')
            cell_texts = []
            for ci in range(min(tpsl_cells.count(), 8)):
                el = tpsl_cells.nth(ci)
                try:
                    cell_texts.append({
                        "testid": el.get_attribute("data-testid"),
                        "text": el.inner_text(timeout=1500).strip(),
                    })
                except Exception:
                    pass
            print(f"  position SL/TP cells: {cell_texts}")
            result["cells"] = cell_texts
        except Exception as e:
            print(f"  could not re-read position row: {e}")

        browser.close()

    _restore(backup)
    print("\n" + "=" * 60)
    print(f"  RESULT: phase_b={'PASS' if result['phase_b'] else 'FAIL'}")
    print(f"  labels={tpsl}  positions={len(result['positions'])}")
    print("  screenshots: debug_phase_b_before.png / debug_phase_b_after.png")
    print("=" * 60)
    if not result["phase_b"]:
        raise SystemExit(1)


def _restore(backup: Path):
    if backup.exists():
        shutil.move(str(backup), str(PENDING_SL_FILE))
        print("  restored original pending_sl.json")


if __name__ == "__main__":
    main()
