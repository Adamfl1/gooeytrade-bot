"""
Order execution module for GooeyTrade bot.

Handles placing Pending/Limit trades AND Market orders via Playwright on the
GooeyTrade web app.  Phase A submits market orders without SL/TP; Phase B
edits open positions to add SL/TP after the entry candle closes.
"""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from signal_detector import Signal

SESSION_FILE = Path("session.json")
GOOEYTRADE_URL = "https://mtr.gooeytrade.com/app/trade"
PENDING_SL_FILE = Path("pending_sl.json")
DEFAULT_VOLUME = 0.01

# Selectors
SEL_ADVANCED_ORDER_BTN = '[data-testid="advanced-order-button"]'
SEL_PENDING_TAB = '[data-testid="pending-order-tab"]'
SEL_ACTIVATION_PRICE = '[data-testid="pending-activation-price-input"]'
SEL_VOLUME_CONTAINER = '[data-testid="advanced-order-volume-input"]'
SEL_SL_CONTAINER = '[data-testid="stop-loss-value-input"]'
SEL_TP_CONTAINER = '[data-testid="take-profit-value-input"]'
SEL_STEPPER_INPUT = '[data-testid="input-stepper-input"]'
SEL_BUY_BTN = '[data-testid="advanced-order-buy-button"]'
SEL_SELL_BTN = '[data-testid="advanced-order-sell-button"]'
SEL_OPEN_POSITIONS = '[data-testid="portfolio-open-positions"]'
SEL_EMPTY_POSITIONS = '[data-testid="empty-open-positions-disclaimer"]'
SEL_EMPTY_PENDING = '[data-testid="empty-pending-orders-disclaimer"]'
SEL_TP_SL_TOGGLE = '[data-testid="tp-sl-toggle-header-element"]'
SEL_SL_SECTION = '[data-testid="advanced-order-tp-sl-stop-loss"]'
SEL_TP_SECTION = '[data-testid="advanced-order-tp-sl-take-profit"]'
SEL_STEPPER_MINUS = '[data-testid="input-stepper-horizontal-button"]'

# Open positions list
SEL_POSITION_ROW = '[data-testid="open-positions-desktop-list-row"]'
SEL_POSITION_VOLUME = '[data-testid="open-position-volume"]'
SEL_POSITION_TPSL_BTN = '[data-testid="open-positions-desktop-tpsl-btn"]'

# Position edit dialog (SL/TP)
SEL_POSITION_EDIT_DIALOG = '[data-testid="dialog-wrapper"]'
SEL_POSITION_EDIT_TOGGLE = '[data-testid="tp-sl-toggle-header-element"]'
SEL_POSITION_EDIT_VALUE = '[data-testid="tp-sl-value-input"]'
SEL_POSITION_EDIT_SAVE = '[data-testid="position-edit-dialog-save-btn"]'
SEL_POSITION_EDIT_CANCEL = '[data-testid="position-edit-dialog-cancel-btn"]'


def _type_in_stepper(page: Page, container_selector: str, value: float,
                     activate: bool = False, timeout: int = 5000) -> None:
    """Type a value into a contentEditable stepper input.

    Uses triple-click + Ctrl+A + insertText + Tab.
    If activate=True, clicks the minus button first (needed for SL field).
    """
    container = page.locator(container_selector)
    input_el = container.locator(SEL_STEPPER_INPUT)

    input_el.wait_for(state="visible", timeout=timeout)

    if activate:
        container.locator(SEL_STEPPER_MINUS).first.click()
        time.sleep(0.2)

    input_el.click(click_count=3)
    time.sleep(0.05)
    page.keyboard.press("Control+a")
    page.keyboard.insert_text(str(value))
    time.sleep(0.1)
    page.keyboard.press("Tab")
    time.sleep(0.3)


def open_advanced_order(page: Page, timeout: int = 10000) -> None:
    """Click the advanced order button to open the order ticket panel."""
    btn = page.locator(SEL_ADVANCED_ORDER_BTN)
    btn.wait_for(state="visible", timeout=timeout)
    btn.click()
    time.sleep(0.5)


def ensure_tp_sl_enabled(page: Page) -> None:
    """Make sure both SL and TP toggles are ON. Only click if currently OFF."""
    for section_sel, input_sel, label in [
        (SEL_SL_SECTION, SEL_SL_CONTAINER, "Stop Loss"),
        (SEL_TP_SECTION, SEL_TP_CONTAINER, "Take Profit"),
    ]:
        section = page.locator(section_sel)
        toggle = section.locator(SEL_TP_SL_TOGGLE)
        if toggle.count() == 0:
            continue
        input_field = page.locator(input_sel)
        is_on = input_field.count() > 0 and input_field.is_visible()
        if not is_on:
            toggle.click()
            time.sleep(0.5)
            print(f"    Toggled {label} ON")
        else:
            print(f"    {label} already ON")


def wait_for_button_enabled(page: Page, selector: str, timeout: int = 15000) -> bool:
    """Wait until a submit button is no longer disabled."""
    btn = page.locator(selector)
    try:
        btn.wait_for(state="visible", timeout=timeout)
        start = time.time()
        while time.time() - start < timeout / 1000:
            disabled = btn.get_attribute("disabled")
            if disabled is None:
                return True
            time.sleep(0.3)
    except PlaywrightTimeout:
        pass
    return False


def submit_order(page: Page, direction: str, timeout: int = 15000) -> bool:
    """Click the buy/sell button, handle confirmation dialog, wait for fill."""
    selector = SEL_BUY_BTN if direction == "buy" else SEL_SELL_BTN

    if not wait_for_button_enabled(page, selector, timeout):
        print("  WARNING: Submit button still disabled after waiting")
        return False

    page.locator(selector).click()
    time.sleep(2)

    # Handle confirmation popup if it appears
    confirm_btn = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
    if confirm_btn.count() > 0:
        try:
            confirm_btn.wait_for(state="visible", timeout=5000)
            confirm_btn.click()
            time.sleep(2)
            print("  Order confirmed via popup")
        except Exception:
            pass

    return True


def verify_order_placed(page: Page, timeout: int = 10000) -> bool:
    """Check that a new pending order appeared in the Pending Orders tab."""
    try:
        page.get_by_text("Pending Orders", exact=True).click()
        time.sleep(1)
        empty = page.locator(SEL_EMPTY_PENDING)
        if empty.count() > 0:
            empty.wait_for(state="hidden", timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def execute_trade(signal: Signal, page: Page, volume: float = DEFAULT_VOLUME,
                  dry_run: bool = True) -> bool:
    """Execute a trade based on a Signal object using a Pending/Limit order."""
    direction = signal.direction.upper()
    entry = signal.entry_price
    sl = round(signal.sl, 2)
    tp = round(signal.tp, 2)

    print(f"\n  === EXECUTING {direction} LIMIT ===")
    print(f"  Entry (limit):   {entry:.2f}")
    print(f"  Stop Loss:        {sl:.2f}")
    print(f"  Take Profit:      {tp:.2f}")
    print(f"  Volume:           {volume}")
    print(f"  Dry Run:          {dry_run}")
    print()

    if dry_run:
        print("  [DRY RUN] Filling form to verify selectors (no submit)")
        try:
            open_advanced_order(page)
            page.locator(SEL_PENDING_TAB).click()
            time.sleep(0.5)

            _type_in_stepper(page, SEL_ACTIVATION_PRICE, entry)
            _type_in_stepper(page, SEL_VOLUME_CONTAINER, volume)

            ensure_tp_sl_enabled(page)

            _type_in_stepper(page, SEL_SL_CONTAINER, sl, activate=True)
            _type_in_stepper(page, SEL_TP_CONTAINER, tp)

            btn = page.locator(SEL_BUY_BTN if signal.direction == "buy" else SEL_SELL_BTN)
            btn_text = btn.inner_text(timeout=3000)
            print(f"  [DRY RUN] Button: {btn_text.split(chr(10))[0]}")
            print("  [DRY RUN] Skipping submit")
        except Exception as e:
            print(f"  [DRY RUN] ERROR: {e}")
        return True

    try:
        print("  Opening advanced order panel...")
        open_advanced_order(page)

        print("  Switching to Pending tab...")
        page.locator(SEL_PENDING_TAB).click()
        time.sleep(0.5)

        print(f"  Setting activation price to {entry:.2f}...")
        _type_in_stepper(page, SEL_ACTIVATION_PRICE, entry)

        print(f"  Setting volume to {volume}...")
        _type_in_stepper(page, SEL_VOLUME_CONTAINER, volume)

        print("  Ensuring TP/SL toggle is ON...")
        ensure_tp_sl_enabled(page)

        print(f"  Setting Stop Loss to {sl}...")
        _type_in_stepper(page, SEL_SL_CONTAINER, sl, activate=True)

        print(f"  Setting Take Profit to {tp}...")
        _type_in_stepper(page, SEL_TP_CONTAINER, tp)

        print(f"  Submitting {direction} LIMIT order...")
        if not submit_order(page, direction):
            print("  ERROR: Failed to submit order")
            return False

        print("  Verifying order placed...")
        if verify_order_placed(page):
            print("  Pending order confirmed!")
            return True
        else:
            print("  WARNING: Could not confirm pending order")
            return True

    except Exception as e:
        print(f"  ERROR during execution: {e}")
        return False


def get_live_price(page: Page) -> float | None:
    """Read the live price from the quick-order panel buy/sell buttons."""
    for selector in [SEL_BUY_BTN, SEL_SELL_BTN]:
        try:
            btn = page.locator(selector)
            price_el = btn.locator(".ui-order-button__price")
            text = price_el.inner_text(timeout=3000)
            return float(text.replace(",", ""))
        except Exception:
            continue
    return None


# ── pending_sl.json management ───────────────────────────────────────────────

def load_pending_sl() -> list[dict]:
    """Load pending SL entries from disk. Returns [] if file missing."""
    if not PENDING_SL_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_SL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_pending_sl(entries: list[dict]) -> None:
    """Persist pending SL entries to disk."""
    PENDING_SL_FILE.write_text(
        json.dumps(entries, indent=2, default=str), encoding="utf-8"
    )


# ── Git state persistence ────────────────────────────────────────────────────

def commit_state(message: str = None) -> bool:
    """Stage pending_sl.json (+ candles.csv if present) and commit.

    Returns True on success, False if git is unavailable or fails.
    """
    if message is None:
        message = f"bot: update state {datetime.now(timezone.utc).isoformat()}"

    files_to_add = [str(PENDING_SL_FILE)]
    if Path("candles.csv").exists():
        files_to_add.append("candles.csv")

    try:
        subprocess.run(
            ["git", "add"] + files_to_add,
            check=True, capture_output=True, timeout=10,
        )
        # Check if there's actually something to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            # Nothing staged — nothing to commit
            return True
        subprocess.run(
            ["git", "commit", "-m", message,
             "--author=gooeytrade-bot <gooeytrade-bot@local>"],
            check=True, capture_output=True, timeout=10,
        )
        # Try push — silently ignore if no remote configured
        subprocess.run(
            ["git", "push"], capture_output=True, timeout=30,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# ── Phase A: Market order with SL/TP ────────────────────────────────────────

def execute_market_order(signal: Signal, page: Page, volume: float = DEFAULT_VOLUME,
                         dry_run: bool = True, skip_sl_tp: bool = False) -> float:
    """Submit a MARKET order via the advanced-order panel.

    Args:
        skip_sl_tp: If True, submit without SL/TP (Phase A — TP/SL added later in Phase B).
                    If False, submit WITH SL/TP (local fallback or immediate trade).

    Returns the LIVE price at the time of submission.
    Returns 0.0 on failure.
    """
    direction = signal.direction.upper()
    sl = round(signal.sl, 2)
    tp = round(signal.tp, 2)

    print(f"\n  === EXECUTING {direction} MARKET ===")
    if not skip_sl_tp:
        print(f"  Stop Loss:    {sl}")
        print(f"  Take Profit:  {tp}")
    else:
        print(f"  SL/TP:        (will be added on next candle)")
    print(f"  Volume:       {volume}")
    print(f"  Dry Run:      {dry_run}")
    print()

    if dry_run:
        print("  [DRY RUN] Filling form to verify selectors (no submit)")
        try:
            open_advanced_order(page)
            _type_in_stepper(page, SEL_VOLUME_CONTAINER, volume)
            if not skip_sl_tp:
                ensure_tp_sl_enabled(page)
                _type_in_stepper(page, SEL_SL_CONTAINER, sl, activate=True)
                _type_in_stepper(page, SEL_TP_CONTAINER, tp)
            btn = page.locator(SEL_BUY_BTN if signal.direction == "buy" else SEL_SELL_BTN)
            btn.wait_for(state="visible", timeout=5000)
            live_price = _get_live_price_from_button(btn)
            btn_text = btn.inner_text(timeout=3000)
            print(f"  [DRY RUN] Button: {btn_text.split(chr(10))[0]}")
            print(f"  [DRY RUN] Live price: {live_price:.2f}")
            print("  [DRY RUN] Skipping submit")
            return live_price
        except Exception as e:
            print(f"  [DRY RUN] ERROR: {e}")
            return 0.0

    try:
        print("  Opening advanced order panel...")
        open_advanced_order(page)

        print(f"  Setting volume to {volume}...")
        _type_in_stepper(page, SEL_VOLUME_CONTAINER, volume)

        if not skip_sl_tp:
            print("  Enabling SL/TP toggles...")
            ensure_tp_sl_enabled(page)

            print(f"  Setting Stop Loss to {sl}...")
            _type_in_stepper(page, SEL_SL_CONTAINER, sl, activate=True)

            print(f"  Setting Take Profit to {tp}...")
            _type_in_stepper(page, SEL_TP_CONTAINER, tp)

        btn = page.locator(SEL_BUY_BTN if signal.direction == "buy" else SEL_SELL_BTN)
        btn.wait_for(state="visible", timeout=5000)
        live_price = _get_live_price_from_button(btn)
        print(f"  Live price at submission: {live_price:.2f}")

        print(f"  Submitting {direction} MARKET order...")
        if not submit_order(page, direction):
            print("  ERROR: Failed to submit market order")
            return 0.0

        time.sleep(2)
        print("  Market order submitted successfully.")
        return live_price

    except Exception as e:
        print(f"  ERROR during market order execution: {e}")
        return 0.0


def _get_live_price_from_button(btn) -> float:
    """Read price from a buy/sell button's .ui-order-button__price element."""
    try:
        price_el = btn.locator(".ui-order-button__price")
        text = price_el.inner_text(timeout=3000)
        return float(text.replace(",", ""))
    except Exception:
        return 0.0


# ── Phase B: resolve pending SL/TP ───────────────────────────────────────────

def _find_position_row(page: Page, direction: str, volume: float,
                       timeout: int = 5000):
    """Find the open-position row matching direction + volume.

    Returns the Playwright Locator for the matching row, or None.
    """
    rows = page.locator(SEL_POSITION_ROW)
    try:
        rows.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeout:
        return None

    direction_lower = direction.lower()
    vol_str = f"{volume}"

    for i in range(rows.count()):
        row = rows.nth(i)
        badges = row.locator(".ui-badge")
        dir_match = False
        for bi in range(badges.count()):
            text = badges.nth(bi).inner_text(timeout=2000).strip().lower()
            if direction_lower == "buy" and text in ("acheter", "buy"):
                dir_match = True
                break
            if direction_lower == "sell" and text in ("vendre", "sell"):
                dir_match = True
                break
        if not dir_match:
            continue
        vol_el = row.locator(SEL_POSITION_VOLUME)
        if vol_el.count() == 0:
            continue
        vol_text = vol_el.inner_text(timeout=2000).strip()
        if vol_str in vol_text:
            return row
    return None


def _read_entry_price_from_row(row) -> float | None:
    """Try to read the entry price from an open-position row DOM.

    Probes several likely selectors; returns None if nothing works.
    """
    for sel in [
        '[data-testid="open-position-entry-price"]',
        '[data-testid="open-position-price"]',
        ".open-position__entry-price",
        ".open-position__price",
    ]:
        el = row.locator(sel)
        if el.count() > 0:
            try:
                text = el.first.inner_text(timeout=2000).strip()
                return float(text.replace(",", ""))
            except (ValueError, Exception):
                continue
    return None


def resolve_pending_sl(page: Page, candle_store, dry_run: bool = False) -> int:
    """Phase B: resolve all pending SL/TP entries whose entry candle has closed.

    For each unresolved entry in pending_sl.json whose entry candle has
    closed (we have the full 5m bar), compute SL and TP from that candle,
    find the matching open position row, and edit SL/TP into it.

    Returns the number of entries resolved.
    """
    entries = load_pending_sl()
    unresolved = [e for e in entries if not e.get("resolved", False)]
    if not unresolved:
        return 0

    print(f"\n  === PHASE B: RESOLVING PENDING SL/TP ({len(unresolved)} pending) ===\n")

    from indicators import atr_sl_finder

    df = candle_store.get_dataframe()
    now = datetime.now(timezone.utc)
    resolved_count = 0

    for entry in unresolved:
        direction = entry["direction"]
        entry_candle_start = datetime.fromisoformat(entry["entry_candle_start"])
        entry_candle_start = entry_candle_start.replace(tzinfo=timezone.utc) \
            if entry_candle_start.tzinfo is None else entry_candle_start
        volume = entry.get("volume", DEFAULT_VOLUME)
        pending_entry_price = entry.get("entry_price", 0)

        # Check if 5+ minutes have elapsed since entry candle start
        elapsed = (now - entry_candle_start).total_seconds()
        if elapsed < 5 * 60:
            print(f"  [{direction.upper()}] Entry candle still open "
                  f"({elapsed:.0f}s / 300s) -- skipping")
            continue

        # Look up the entry candle in the store
        if entry_candle_start not in df.index:
            print(f"  [{direction.upper()}] Entry candle {entry_candle_start} "
                  f"not in store -- skipping")
            continue

        entry_candle = df.loc[entry_candle_start]
        high = entry_candle["High"]
        low = entry_candle["Low"]

        # Compute ATR SL Finder for this candle
        atr_series = atr_sl_finder(df["High"], df["Low"], df["Close"],
                                   length=14, factor=1.5)
        atr_val = atr_series.loc[entry_candle_start]
        if atr_val != atr_val:  # NaN check
            print(f"  [{direction.upper()}] ATR value is NaN for "
                  f"{entry_candle_start} -- skipping")
            continue

        # Find the matching position row and read actual fill price
        row = _find_position_row(page, direction, volume)
        actual_entry = pending_entry_price
        if row is not None:
            fill_price = _read_entry_price_from_row(row)
            if fill_price is not None:
                actual_entry = fill_price
                print(f"  [{direction.upper()}] Read actual fill price: "
                      f"{actual_entry:.2f}")
            else:
                print(f"  [{direction.upper()}] Could not read fill price "
                      f"from DOM -- using signal price: {actual_entry:.2f}")
        else:
            print(f"  [{direction.upper()}] Position row not found "
                  f"-- using signal price: {actual_entry:.2f}")

        # Compute SL and TP
        if direction == "buy":
            sl = round(low - atr_val, 2)
            tp = round(actual_entry + (actual_entry - sl) * 3, 2)
        else:
            sl = round(high + atr_val, 2)
            tp = round(actual_entry - (sl - actual_entry) * 3, 2)

        risk = abs(actual_entry - sl)
        reward = abs(tp - actual_entry)
        rr = reward / risk if risk > 0 else 0

        print(f"  [{direction.upper()}] Entry: {actual_entry:.2f}  "
              f"SL: {sl:.2f}  TP: {tp:.2f}  R:R: {rr:.2f}")

        if dry_run:
            print(f"  [DRY RUN] Would edit position SL/TP -- skipping dialog")
            entry["resolved"] = True
            entry["resolved_at"] = now.isoformat()
            entry["actual_entry"] = actual_entry
            resolved_count += 1
            continue

        if row is None:
            print(f"  [{direction.upper()}] WARNING: Position row not found "
                  f"-- cannot edit SL/TP")
            continue

        # Click the TPSL (pencil) button to open the edit dialog
        print(f"  Opening SL/TP edit dialog...")
        tpsl_btn = row.locator(SEL_POSITION_TPSL_BTN)
        tpsl_btn.click()
        time.sleep(1)

        # Wait for dialog
        dialog = page.locator(SEL_POSITION_EDIT_DIALOG)
        try:
            dialog.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeout:
            print(f"  [{direction.upper()}] ERROR: Edit dialog did not appear")
            continue

        # Toggle SL and TP ON (.nth(0) = SL, .nth(1) = TP)
        toggles = dialog.locator(SEL_POSITION_EDIT_TOGGLE)
        for idx, label in [(0, "Stop Loss"), (1, "Take Profit")]:
            toggle = toggles.nth(idx)
            value_inputs = dialog.locator(SEL_POSITION_EDIT_VALUE)
            value_input = value_inputs.nth(idx)
            # Check if the value input is already visible/enabled
            is_on = value_input.count() > 0 and value_input.is_visible()
            if not is_on:
                toggle.click()
                time.sleep(0.5)
                print(f"    Toggled {label} ON")
            else:
                print(f"    {label} already ON")

        # Type SL value (nth(0))
        print(f"  Setting Stop Loss to {sl}...")
        sl_container = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(0)
        sl_stepper = sl_container.locator(SEL_STEPPER_INPUT)
        sl_stepper.wait_for(state="visible", timeout=5000)
        sl_stepper.click(click_count=3)
        time.sleep(0.05)
        page.keyboard.press("Control+a")
        page.keyboard.insert_text(str(sl))
        time.sleep(0.1)
        page.keyboard.press("Tab")
        time.sleep(0.3)

        # Type TP value (nth(1))
        print(f"  Setting Take Profit to {tp}...")
        tp_container = dialog.locator(SEL_POSITION_EDIT_VALUE).nth(1)
        tp_stepper = tp_container.locator(SEL_STEPPER_INPUT)
        tp_stepper.wait_for(state="visible", timeout=5000)
        tp_stepper.click(click_count=3)
        time.sleep(0.05)
        page.keyboard.press("Control+a")
        page.keyboard.insert_text(str(tp))
        time.sleep(0.1)
        # Defocus TP by pressing Tab then clicking back into the dialog
        page.keyboard.press("Tab")
        time.sleep(0.3)

        # Click Save
        save_btn = dialog.locator(SEL_POSITION_EDIT_SAVE)
        try:
            save_btn.wait_for(state="visible", timeout=5000)
            # Wait for button to become enabled
            start = time.time()
            while time.time() - start < 5:
                disabled = save_btn.get_attribute("disabled")
                if disabled is None:
                    break
                time.sleep(0.3)
            save_btn.click()
            time.sleep(2)

            # Handle confirmation popup after save
            confirm_btn = page.locator('[data-testid="overlay-confirm-actions-confirm"]')
            if confirm_btn.count() > 0:
                try:
                    confirm_btn.wait_for(state="visible", timeout=5000)
                    confirm_btn.click()
                    time.sleep(2)
                    print(f"  Confirmation confirmed!")
                except Exception:
                    pass

            print(f"  Position SL/TP saved!")
        except PlaywrightTimeout:
            print(f"  [{direction.upper()}] ERROR: Save button not found/enabled")
            continue

        # Mark resolved
        entry["resolved"] = True
        entry["resolved_at"] = now.isoformat()
        entry["actual_entry"] = actual_entry
        entry["sl"] = sl
        entry["tp"] = tp
        resolved_count += 1

    # Persist
    save_pending_sl(entries)
    if resolved_count > 0:
        commit_state(f"bot: resolve {resolved_count} pending SL/TP")

    return resolved_count
