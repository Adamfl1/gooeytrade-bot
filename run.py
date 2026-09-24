"""
Main entry point for the GooeyTrade bot.

Two-phase flow (matches your Pine Script exactly):

  Phase A — Entry signal (every 5 min):
    1. Scrape TradingView for "BUY price" or "SELL price" label
    2. Validate freshness against live GooeyTrade price (reject if stale)
    3. If found → open market order on GooeyTrade
    4. Save pending entry for Phase B

  Phase B — SL/TP application (next 5-min scrape):
    1. Scrape TradingView for "SL price" and "TP price" labels
    2. If found → edit the open position to add SL/TP
    3. Remove from pending

  Signal source: TradingView label scrape ONLY. No local fallback.

Usage:
    python run.py                      # dry run (default)
    python run.py --live               # LIVE trading
    python run.py --live --yes         # skip confirmation
    python run.py --live --volume 0.05 # custom lot size
    python run.py --tv-only            # only scrape, don't trade
"""

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

from config import (
    TV_CHART_URL, TV_SESSION_FILE, GOOEYTRADE_URL, SESSION_FILE,
    DEFAULT_VOLUME, DRY_RUN,
)
from tv_scraper import scrape_chart, parse_entry_label, parse_tp_sl_labels, parse_signal
from state import (
    has_traded, record_trade, get_trade_count,
    get_signal_first_seen, record_signal_first_seen,
)
from execution import (
    execute_market_order, add_pending_sl, load_pending_sl, save_pending_sl,
)


def scrape_tv() -> dict | None:
    """Scrape TradingView chart. Returns raw result or None."""
    if not TV_SESSION_FILE.exists():
        print("  [TV] tv_session.json not found — skipping TV scrape")
        print("  [TV] Run: python capture_tv_session.py")
        return None

    print(f"  [TV] Scraping: {TV_CHART_URL}")
    try:
        raw = scrape_chart(TV_CHART_URL)
        print(f"  [TV] Labels found: {len(raw.get('labels', []))}")
        for label in raw.get("labels", []):
            print(f"       - {label}")
        return raw
    except Exception as e:
        print(f"  [TV] Scrape failed: {e}")
        return None


def _match_sl_tp(raw: dict, position_direction: str, position_entry: float) -> tuple[float | None, float | None, str | None]:
    """Check if matching SL/TP labels exist on the chart for this position.

    Returns (sl, tp, reason). If reason is not None, no valid match was found yet
    (expected/normal until the entry candle closes and labels appear).
    """
    # 1. Require both SL and TP labels to be present
    tpsl = parse_tp_sl_labels(raw)
    if not tpsl or not tpsl.get("sl") or not tpsl.get("tp"):
        return None, None, "no match yet: SL/TP label pair not on chart yet (candle still open or not formed)"

    sl = tpsl["sl"]
    tp = tpsl["tp"]

    # 2. Check direction match from chart signal table / entry label
    entry_lbl = parse_entry_label(raw)
    st = raw.get("signal_table", {})
    chart_dir = (entry_lbl.get("direction") if entry_lbl else None) or st.get("direction")

    if not chart_dir:
        return None, None, "no match yet: SL/TP labels present but no matching chart direction found"

    if chart_dir.lower() != position_direction.lower():
        return None, None, f"no match yet: chart direction ({chart_dir}) != position direction ({position_direction})"

    # 3. Check entry price proximity (within 0.5% tolerance)
    chart_entry = (entry_lbl.get("entry_price") if entry_lbl else None) or st.get("entry_price")
    if chart_entry and position_entry > 0:
        dist_pct = abs(chart_entry - position_entry) / position_entry * 100
        if dist_pct > 0.5:
            return None, None, f"no match yet: chart entry ({chart_entry:.2f}) differs from position entry ({position_entry:.2f}) by {dist_pct:.2f}%"

    return sl, tp, None


def _apply_sl_tp_dialog(page, direction: str, volume: float,
                        sl: float, tp: float, row=None) -> str | None:
    """Open the position edit dialog on GooeyTrade and save SL/TP values.

    If `row` (a position row locator) is given it is used directly,
    otherwise the row is looked up by direction + volume.
    Returns None on success, or an error-reason string on failure.
    """
    from execution import (
        _find_position_row, SEL_POSITION_TPSL_BTN,
        SEL_POSITION_EDIT_DIALOG, SEL_POSITION_EDIT_TOGGLE,
        SEL_POSITION_EDIT_VALUE, SEL_STEPPER_INPUT,
        SEL_POSITION_EDIT_SAVE, SEL_POSITION_EDIT_CANCEL,
    )

    def _set_stepper(idx: int, value: float, label: str) -> str:
        """Type a value into a dialog stepper like a human (trusted key
        events) so the app's input component registers the change.

        Returns the read-back field text.
        """
        container = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(idx)
        stepper = container.locator(SEL_STEPPER_INPUT)
        stepper.wait_for(state="visible", timeout=5000)
        try:
            initial = stepper.inner_text(timeout=2000).strip()
        except Exception:
            initial = "?"
        print(f"  [Phase B] {label} field initially shows {initial!r}")
        stepper.click(click_count=3)
        time.sleep(0.05)
        page.keyboard.press("Control+a")
        time.sleep(0.05)
        page.keyboard.press("Backspace")
        time.sleep(0.1)
        # Human-like typing: trusted per-key events the component listens to
        page.keyboard.type(str(value), delay=60)
        time.sleep(0.2)
        # Fire the DOM events the app's input component listens to
        try:
            stepper.evaluate("""el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
            }""")
        except Exception:
            pass
        time.sleep(0.2)
        page.keyboard.press("Tab")
        time.sleep(0.3)
        try:
            shown = stepper.inner_text(timeout=2000).strip()
        except Exception:
            shown = "?"
        print(f"  [Phase B] {label} field shows {shown!r} (wanted {value})")
        return shown

    def _save_enabled() -> bool:
        try:
            return save_btn.get_attribute("disabled") is None
        except Exception:
            return False

    dialog = None

    def _close_dialog():
        if dialog is None:
            return
        try:
            cancel = dialog.locator(SEL_POSITION_EDIT_CANCEL)
            if cancel.count() > 0:
                cancel.click()
                time.sleep(0.5)
                return
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

    def _validation_hint() -> str:
        try:
            hints = dialog.locator('[class*="error"], [class*="invalid"], [class*="hint"]')
            texts = []
            for i in range(min(hints.count(), 4)):
                try:
                    t = hints.nth(i).inner_text(timeout=1000).strip()
                    if t:
                        texts.append(t[:120])
                except Exception:
                    pass
            return " | ".join(texts)
        except Exception:
            return ""

    try:
        if row is None:
            row = _find_position_row(page, direction, volume)
        if row is None:
            print(f"  [Phase B] Position row not found for {direction.upper()}")
            return f"position row not found ({direction} vol={volume})"

        # Click TPSL button to open edit dialog
        print("  [Phase B] Opening SL/TP edit dialog...")
        tpsl_btn = row.locator(SEL_POSITION_TPSL_BTN)
        tpsl_btn.click()
        time.sleep(1)

        dialog = page.locator(SEL_POSITION_EDIT_DIALOG)
        dialog.wait_for(state="visible", timeout=5000)
        save_btn = dialog.locator(SEL_POSITION_EDIT_SAVE)

        # Toggle SL and TP ON
        toggles = dialog.locator(SEL_POSITION_EDIT_TOGGLE)
        for idx, label in [(0, "Stop Loss"), (1, "Take Profit")]:
            toggle = toggles.nth(idx)
            value_input = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(idx)
            is_on = value_input.count() > 0 and value_input.is_visible()
            if not is_on:
                toggle.click()
                time.sleep(0.5)
                print(f"    Toggled {label} ON")

        # Type SL + TP values (nth(0) = SL, nth(1) = TP)
        print(f"  [Phase B] Setting SL = {sl:.2f}")
        _set_stepper(0, sl, "SL")
        print(f"  [Phase B] Setting TP = {tp:.2f}")
        _set_stepper(1, tp, "TP")

        # Wait for the form to register the change (fast-fail, no 30s click)
        start = time.time()
        while time.time() - start < 8:
            if _save_enabled():
                break
            time.sleep(0.3)

        if not _save_enabled():
            # Fallback 1: press Enter in each field (some forms commit on Enter)
            try:
                for idx in (0, 1):
                    fld = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(idx) \
                        .locator(SEL_STEPPER_INPUT)
                    fld.click()
                    time.sleep(0.2)
                    page.keyboard.press("Enter")
                    time.sleep(1)
            except Exception:
                pass
            # Fallback 2: +/- button forces the component's own handler
            if not _save_enabled():
                try:
                    plus = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(0) \
                        .locator('button[data-testid="input-stepper-horizontal-button"]').last
                    if plus.count() > 0:
                        plus.click()
                        time.sleep(0.5)
                        _set_stepper(0, sl, "SL(retry)")
                        time.sleep(1)
                except Exception:
                    pass
            if not _save_enabled():
                hint = _validation_hint()
                _close_dialog()
                msg = (f"save button stayed disabled after typing "
                       f"(SL={sl} TP={tp})" + (f" — dialog says: {hint}" if hint else ""))
                print(f"  [Phase B] {msg}")
                return msg

        save_btn.click()
        time.sleep(2)

        # Handle confirmation popup after save
        confirm_btn = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
        if confirm_btn.count() > 0:
            try:
                confirm_btn.wait_for(state="visible", timeout=5000)
                confirm_btn.click()
                time.sleep(2)
                print("  [Phase B] Confirmed!")
            except Exception:
                pass

        print("  [Phase B] Position SL/TP saved!")
        return None

    except Exception as e:
        print(f"  [Phase B] ERROR: {e}")
        try:
            _close_dialog()
        except Exception:
            pass
        return f"{type(e).__name__}: {e}"


def _find_matching_pending(entries: list[dict], direction: str,
                           volume: float | None):
    """Find an unresolved pending_sl entry for direction + volume.

    AUDIT ONLY — the result is used solely to stamp logging fields
    (resolved / last_attempt_at / last_error / sl / tp). It never gates
    which positions are scanned or edited; those decisions come only from
    the live GooeyTrade rows.
    """
    for e in entries:
        if e.get("resolved"):
            continue
        if (e.get("direction") or "").lower() != direction.lower():
            continue
        pv = e.get("volume")
        if pv is None or volume is None:
            return e
        try:
            if abs(float(pv) - float(volume)) < 0.0001:
                return e
        except (TypeError, ValueError):
            return e
    return None


def run_phase_b(page, raw: dict, dry_run: bool, volume: float) -> bool:
    """Phase B: scan ALL live open positions; for every row where BOTH TP and
    SL show "-" (unset), require a matching SL+TP label pair on the
    TradingView chart (same direction + entry price) before applying anything.

    The live GooeyTrade row list is the ONLY source of truth for "what needs
    work". pending_sl.json is audit/history only — it is written (resolved
    flags, last_attempt_at, last_error) but NEVER influences which positions
    get scanned or edited.

    Returns True if SL/TP was applied to any position.
    """
    from execution import list_open_positions

    positions = list_open_positions(page)
    if not positions:
        print("  [Phase B] No open positions found — nothing to scan")
        return False

    print(f"  [Phase B] Scanning {len(positions)} open position(s) for unset TP/SL...")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    entries = load_pending_sl()
    applied_any = False

    for pos in positions:
        direction = pos["direction"]
        vol = pos["volume"]
        row_entry = pos["entry_price"]
        sl_text, tp_text = pos["sl_text"], pos["tp_text"]
        tag = (f"{(direction or '?').upper()} "
               f"@ {row_entry:.2f}" if row_entry else f"{(direction or '?').upper()} "
               f"(entry unknown)")

        if direction is None:
            print(f"  [Phase B] Position row #{pos['index']}: "
                  f"direction unreadable — skipping")
            continue

        # Row already has TP and/or SL set → nothing to do (audit only)
        if pos["sl_set"] or pos["tp_set"]:
            print(f"  [Phase B] {tag}: TP/SL already present "
                  f"(SL={sl_text} TP={tp_text}) — skipping")
            pending = _find_matching_pending(entries, direction, vol)
            if pending is not None and not pending.get("resolved"):
                pending["resolved"] = True
                pending["resolved_at"] = now.isoformat()
                pending.pop("last_error", None)
            continue

        # Candidate: BOTH TP and SL show "-" in the live row
        print(f"  [Phase B] {tag}: TP/SL unset "
              f"(SL={sl_text!r} TP={tp_text!r}) — checking chart labels...")

        # Entry price for proximity matching comes ONLY from the live row.
        # pending_sl.json is audit-only and never influences matching.
        pending = _find_matching_pending(entries, direction, vol)
        match_entry = row_entry or 0

        # Require an actual SL label AND TP label for this direction+entry —
        # a BUY/SELL entry label alone is NEVER enough.
        sl, tp, match_reason = _match_sl_tp(raw, direction, match_entry)
        if pending is not None:
            pending["last_attempt_at"] = now.isoformat()

        if match_reason is not None:
            print(f"  [Phase B] {tag}: no SL/TP label yet, skipping — "
                  f"{match_reason} (expected/normal)")
            if pending is not None:
                pending["last_error"] = match_reason
            continue

        print(f"  [Phase B] {tag}: matching SL/TP label found! "
              f"SL={sl:.2f} TP={tp:.2f} — applying...")

        if dry_run:
            print(f"  [DRY RUN] Would edit position SL/TP → SL={sl:.2f} TP={tp:.2f}")
            if pending is not None:
                pending["resolved"] = True
                pending["resolved_at"] = now.isoformat()
                pending.pop("last_error", None)
                pending["sl"] = sl
                pending["tp"] = tp
            applied_any = True
            continue

        err = _apply_sl_tp_dialog(page, direction, vol if vol is not None else volume,
                                  sl, tp, row=pos["row"])
        if err is None:
            print(f"  [Phase B] {tag}: SL/TP applied successfully!")
            if pending is not None:
                pending["resolved"] = True
                pending["resolved_at"] = now.isoformat()
                pending.pop("last_error", None)
                pending["sl"] = sl
                pending["tp"] = tp
            applied_any = True
        else:
            print(f"  [Phase B] {tag}: failed to apply SL/TP ({err}) — retrying next run")
            if pending is not None:
                pending["last_error"] = err

    save_pending_sl(entries)
    return applied_any


def run_phase_a(page, raw: dict, dry_run: bool, volume: float) -> bool:
    """Phase A: Find entry signal and open trade.

    Only trades signals from the LAST candle — rejects old signals
    where the entry price is far from current price.

    Returns True if a trade was opened.
    """
    entry = parse_entry_label(raw)
    if not entry:
        print("  [Phase A] No entry signal on chart")
        return False

    direction = entry["direction"]
    entry_price = entry["entry_price"]

    # Strategy has no valid SL/TP before the entry candle closes —
    # Phase B scrapes the SL/TP labels from the chart on a later run.
    sl = 0
    tp = 0

    print(f"\n  [Phase A] Entry signal: {direction.upper()} @ {entry_price:.2f} (SL/TP after candle close)")

    # Check OCR freshness status (Pine Script sends FRESH/OLD)
    signal_status = raw.get("signal_table", {}).get("status", "")
    if signal_status == "old":
        print("  [Phase A] Signal status: OLD (>120s) — skipping")
        return False
    elif signal_status == "fresh":
        print("  [Phase A] Signal status: FRESH (<120s)")
    elif signal_status:
        print(f"  [Phase A] Signal status: {signal_status}")

    # Get current price to check freshness — FAIL CLOSED if unreadable
    current_price = 0
    for attempt in range(5):
        try:
            buy_btn = page.locator('[data-testid="order-panel-buy-button"]')
            price_el = buy_btn.locator(".ui-order-button__price")
            price_text = price_el.inner_text(timeout=5000)
            if price_text.strip():
                current_price = float(price_text.replace(",", ""))
                break
        except Exception:
            pass
        time.sleep(1)

    if current_price == 0:
        print("  [Phase A] Could not read live price from GooeyTrade — rejecting signal as precaution")
        return False

    print(f"  [Phase A] Current price: {current_price:.2f}")

    # Reject if entry price is too far from current price (old signal)
    distance_pct = abs(entry_price - current_price) / current_price * 100
    if distance_pct > 0.5:  # more than 0.5% away = old signal
        print(f"  [Phase A] Signal too old (price {distance_pct:.2f}% away from current) — skipping")
        return False

    # Time-based freshness check: reject signals older than 120 seconds
    from datetime import datetime, timezone
    signal_key = f"tv_{entry_price}_{direction}"
    now = datetime.now(timezone.utc)
    first_seen = get_signal_first_seen(signal_key)
    if first_seen:
        age_seconds = (now - first_seen).total_seconds()
        if age_seconds > 120:
            print(f"  [Phase A] Signal too old ({age_seconds:.0f}s > 120s) — skipping")
            return False
        print(f"  [Phase A] Signal age: {age_seconds:.0f}s (max 120s)")
    else:
        record_signal_first_seen(signal_key, now)
        print(f"  [Phase A] Signal first seen — 120s timer started")

    # Check for duplicate
    if has_traded(signal_key, direction):
        print(f"  [Phase A] Already traded {direction.upper()} at {entry_price} — skipping.")
        return False

    # Show signal info
    print(f"\n  SIGNAL (TradingView)")
    print(f"  Direction:  {direction.upper()}")
    print(f"  Entry:      {entry_price:.2f}")
    print(f"  SL/TP:      (will be applied on next candle)")
    print()

    if dry_run:
        print("  [DRY RUN] Would open trade — skipping execution")
        from datetime import datetime, timezone
        signal_time = datetime.now(timezone.utc).isoformat()
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=entry_price,
            sl=sl, tp=tp,
            volume=volume,
            signal_key=signal_key,
        )
        return True

    # Build signal object for execute_market_order
    class SimpleSignal:
        pass

    sig = SimpleSignal()
    sig.direction = direction
    sig.entry_price = entry_price
    sig.sl = sl
    sig.tp = tp

    live_price = execute_market_order(
        signal=sig,
        page=page,
        volume=volume,
        dry_run=False,
        skip_sl_tp=True,
    )

    if live_price > 0:
        print(f"\n  Trade opened. Live price: {live_price:.2f}")
        from datetime import datetime, timezone
        signal_time_dt = datetime.now(timezone.utc)
        signal_time = signal_time_dt.isoformat()
        record_trade(
            signal_time=signal_time,
            direction=direction,
            entry_price=live_price,
            sl=sl, tp=tp,
            volume=volume,
            signal_key=signal_key,
        )

        # Queue for Phase B: SL/TP scraped from chart after candle close
        candle_start = signal_time_dt.replace(
            minute=(signal_time_dt.minute // 5) * 5, second=0, microsecond=0
        )
        add_pending_sl(direction, live_price, volume, candle_start)
        return True
    else:
        print("\n  Trade failed. Check output above.")
        return False


def main():
    parser = argparse.ArgumentParser(description="GooeyTrade Bot")
    parser.add_argument("--live", action="store_true",
                        help="Execute real trades")
    parser.add_argument("--dry-run", action="store_true",
                        help="Force dry-run mode")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME,
                        help=f"Lot size (default: {DEFAULT_VOLUME})")
    parser.add_argument("--tv-only", action="store_true",
                        help="Only scrape TradingView, don't trade")
    args = parser.parse_args()

    dry_run = DRY_RUN or not args.live or args.dry_run

    print("=" * 60)
    print("  GooeyTrade Bot")
    print(f"  Mode: {'LIVE' if not dry_run else 'DRY RUN'}")
    print(f"  Trades so far: {get_trade_count()}")
    pending = [e for e in load_pending_sl() if not e.get("resolved")]
    if pending:
        print(f"  Pending SL/TP: {len(pending)} trade(s)")
    print("=" * 60)
    print()

    # --- 1. Scrape TradingView ---
    raw = scrape_tv()

    if args.tv_only:
        if raw:
            entry = parse_entry_label(raw)
            tpsl = parse_tp_sl_labels(raw)
            sig = parse_signal(raw)
            print(f"\n  Entry signal: {entry}")
            print(f"  SL/TP labels: {tpsl}")
            print(f"  Signal status: {sig['status'] if sig else 'unknown'}")
            print(f"  All labels: {raw.get('labels', [])}")
        else:
            print("\n  No data scraped.")
        return

    if raw is None:
        print("\n  [TV] Scrape failed — cannot proceed without TradingView data.")
        print("  Make sure tv_session.json is valid (run: python capture_tv_session.py)")
        sys.exit(1)

    # --- 2. Check if session is valid (page loaded) ---
    if not SESSION_FILE.exists():
        print("  ERROR: session.json not found.")
        print("  Run: python capture_session.py")
        sys.exit(1)

    # --- 3. Launch browser for GooeyTrade ---
    import os
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

    with sync_playwright() as p:
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
                '[data-testid="order-panel-buy-button"]',
                timeout=30000,
            )
        except Exception:
            print("  ERROR: Trade page did not load. Session may be expired.")
            print("  Run: python capture_session.py")
            browser.close()
            sys.exit(1)

        print("  Trade page loaded.\n")

        # --- 4. Phase B: scrape SL/TP labels from chart (after candle close) ---
        phase_b_done = run_phase_b(page, raw, dry_run, args.volume)

        # --- 5. Phase A: check for new entry signal (SL/TP deferred) ---
        phase_a_done = run_phase_a(page, raw, dry_run, args.volume)

        if not phase_a_done and not phase_b_done:
            print("\n  No actionable signals from TradingView this run.")

        if not dry_run:
            print("\n  Waiting 5 seconds before closing...")
            time.sleep(5)

        browser.close()

    print("\n  Done.")


if __name__ == "__main__":
    main()
