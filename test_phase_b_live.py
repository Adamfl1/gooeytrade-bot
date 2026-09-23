"""Live Phase B test — no Phase A, opens NO new trades.

1. Scrape TradingView → entry + SL/TP labels
2. Load GooeyTrade → scan positions with list_open_positions()
   (the exact same scan code run_phase_b uses)
3. Seed a pending_sl entry for a target UNSET position
4. Call run_phase_b(live) → real edit dialog → save, but ONLY if the
   scraped labels actually match (otherwise correct gating = no apply)
5. Write phase_b_test_result.json (committed by CI for observability)
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
    list_open_positions,
)
import run as runmod

RESULT_FILE = Path("phase_b_test_result.json")


def floor5(dt):
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def _ser_pos(p):
    return {k: v for k, v in p.items() if k != "row"}


def main():
    started = datetime.now(timezone.utc).isoformat()
    print("=" * 60)
    print("  PHASE B LIVE TEST")
    print("=" * 60)

    outcome = {
        "at": started,
        "labels": None,
        "entry": None,
        "positions": [],
        "target": None,
        "expect_apply": False,
        "applied": False,
        "pass": False,
        "note": "",
    }

    def write_result():
        outcome["at"] = started
        RESULT_FILE.write_text(json.dumps(outcome, indent=2, default=str),
                               encoding="utf-8")
        print(f"  wrote {RESULT_FILE}")

    # ── 1. Scrape TradingView ────────────────────────────────────────────
    print("\n[1] Scraping TradingView...")
    raw = scrape_chart(TV_CHART_URL)
    print(f"  signal_table: {raw.get('signal_table')}")
    entry = parse_entry_label(raw)
    tpsl = parse_tp_sl_labels(raw)
    print(f"  entry label:  {entry}")
    print(f"  SL/TP labels: {tpsl}")
    outcome["labels"] = tpsl
    outcome["entry"] = entry

    # ── 2. Load GooeyTrade + scan positions ──────────────────────────────
    print("\n[2] Loading GooeyTrade + scanning positions...")
    backup = Path("pending_sl.json.bak")
    if PENDING_SL_FILE.exists():
        shutil.copy2(PENDING_SL_FILE, backup)
        print(f"  backed up pending_sl.json → {backup}")

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
            outcome["note"] = f"page load fail: {type(e).__name__}"
            browser.close()
            _restore(backup)
            write_result()
            print("\nRESULT: SESSION/PAGE LOAD FAIL")
            raise SystemExit(1)
        print("  trade page loaded")
        page.screenshot(path="debug_phase_b_before.png")

        positions = list_open_positions(page)
        outcome["positions"] = [_ser_pos(x) for x in positions]
        print(f"  scanned {len(positions)} position(s):")
        for x in positions:
            print(f"    row#{x['index']} {x['direction']} vol={x['volume']} "
                  f"entry={x['entry_price']} SL={x['sl_text']!r} "
                  f"TP={x['tp_text']!r} set={x['sl_set'] and x['tp_set']}")

        target = next((x for x in positions
                       if x["direction"] and not (x["sl_set"] and x["tp_set"])),
                      None)
        if target is None:
            outcome["note"] = ("no unset position to test "
                               "(none open or all already have SL/TP)")
            outcome["pass"] = True
            print(f"  !! {outcome['note']}")
            browser.close()
            _restore(backup)
            write_result()
            print("\nRESULT: NO UNSET POSITION (scan path exercised — PASS)")
            return

        direction = target["direction"]
        volume = target["volume"] if target["volume"] is not None else 2.5
        outcome["target"] = _ser_pos(target)
        print(f"  target: row#{target['index']} {direction.upper()} vol={volume}")

        # Expect an apply only if chart labels exist AND match direction.
        # (Entry-price proximity uses the chart entry; seeded below.)
        chart_dir = entry["direction"] if entry else None
        chart_entry = entry["entry_price"] if entry else 0
        expect = bool(tpsl and chart_dir == direction)
        outcome["expect_apply"] = expect
        print(f"  chart_dir={chart_dir} chart_entry={chart_entry} "
              f"→ expect_apply={expect}")

        # ── 3. Seed pending entry (candle already closed) ────────────────
        now = datetime.now(timezone.utc)
        start = floor5(now - timedelta(minutes=10))
        entries = load_pending_sl()
        entries.append({
            "direction": direction,
            "entry_price": chart_entry if chart_dir == direction else 0,
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
        outcome["applied"] = bool(ok)
        print(f"  run_phase_b returned: {ok}")

        time.sleep(1)
        page.screenshot(path="debug_phase_b_after.png")
        outcome["pending_after"] = load_pending_sl()

        # ── 5. Verdict ───────────────────────────────────────────────────
        outcome["pass"] = (bool(ok) == expect)
        if outcome["pass"]:
            outcome["note"] = ("applied as expected" if ok
                               else "correctly skipped (no matching labels) — "
                                    "gating works")
        else:
            outcome["note"] = ("MISMATCH: expected apply but nothing applied"
                               if expect else "unexpected apply")
        browser.close()

    _restore(backup)
    write_result()
    print("\n" + "=" * 60)
    print(f"  RESULT: {'PASS' if outcome['pass'] else 'FAIL'} "
          f"(applied={outcome['applied']} expect={outcome['expect_apply']})")
    print("=" * 60)
    if not outcome["pass"]:
        raise SystemExit(1)


def _restore(backup: Path):
    if backup.exists():
        shutil.move(str(backup), str(PENDING_SL_FILE))
        print("  restored original pending_sl.json")


if __name__ == "__main__":
    main()
