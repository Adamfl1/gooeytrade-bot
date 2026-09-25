"""
Order execution module for GooeyTrade bot.

Handles placing Pending/Limit trades AND Market orders via Playwright on the
GooeyTrade web app.  Phase A submits market orders without SL/TP; Phase B
edits open positions to add SL/TP after the entry candle closes.
"""

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from signal_detector import Signal

SESSION_FILE = Path("session.json")
GOOEYTRADE_URL = "https://mtr.gooeytrade.com/app/trade"
PENDING_SL_FILE = Path("pending_sl.json")
DEFAULT_VOLUME = 2.5

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
# TP/SL column cells inside a position row ("-" = unset side)
SEL_POSITION_SECURITY = '[data-testid="security-orders"]'

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
    """Click the buy/sell button, handle confirmation dialog, wait for fill.

    `direction` is normalised (case/whitespace insensitive) because callers
    pass both `signal.direction.upper()` ("BUY"/"SELL") and lowercase
    literals. Anything that is not buy/sell raises instead of silently
    falling through to the sell button.
    """
    dir_norm = (direction or "").strip().lower()
    if dir_norm not in ("buy", "sell"):
        raise ValueError(f"submit_order: unknown direction {direction!r}")

    selector = SEL_BUY_BTN if dir_norm == "buy" else SEL_SELL_BTN
    print(f"  [direction] {direction!r} -> {dir_norm} button "
          f"({'advanced-order-buy-button' if dir_norm == 'buy' else 'advanced-order-sell-button'})")

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


def add_pending_sl(direction: str, entry_price: float, volume: float,
                   entry_candle_start: datetime) -> None:
    """Queue a live entry for Phase B (SL/TP applied after candle close)."""
    entries = load_pending_sl()
    entries.append({
        "direction": direction,
        "entry_price": entry_price,
        "volume": volume,
        "entry_candle_start": entry_candle_start.isoformat(),
        "resolved": False,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    })
    save_pending_sl(entries)


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
    """Try to read the entry (open) price from an open-position row DOM.

    Probes known testids first, then falls back to a structural probe: the
    open-price column is the <ui-list-row-item> with no data-testid of its
    own and no TP/SL, profit, or symbol content (per live DOM:
    symbol / volume / open-price / TP-SL / profit / actions).
    Returns None if nothing works.
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
    try:
        items = row.locator("ui-list-row-item")
        for i in range(min(items.count(), 12)):
            item = items.nth(i)
            try:
                if item.get_attribute("data-testid"):
                    continue  # volume cell etc.
                html = item.inner_html(timeout=1500)
            except Exception:
                continue
            if ("security-orders" in html or "open-position-profit" in html
                    or "instrument-symbol-name" in html or "ui-badge" in html
                    or "button" in html):
                continue  # TP/SL, profit, symbol, or action cell
            try:
                text = item.inner_text(timeout=1500).strip().replace(",", "")
            except Exception:
                continue
            try:
                return float(text)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _read_security_orders(row) -> tuple[str | None, str | None]:
    """Read the live TP/SL column state from [data-testid="security-orders"]
    cells inside a position row.

    An unset side shows "-" (or empty). Cell text may carry labels
    ("TP: -" / "SL: 123.45" on one or more lines) or be bare values.
    Returns (tp_text, sl_text); each is None if no cell was found.
    """
    cells = row.locator(SEL_POSITION_SECURITY)
    try:
        n = cells.count()
    except Exception:
        return None, None
    texts: list[str] = []
    for i in range(min(n, 10)):
        try:
            t = cells.nth(i).inner_text(timeout=1500).strip()
        except Exception:
            continue
        if t:
            texts.append(t)
    if not texts:
        return None, None

    tp_text, sl_text = None, None
    unlabeled: list[str] = []
    for text in texts:
        labeled = False
        for line in text.splitlines():
            t = line.strip()
            if re.match(r"(?i)^TP\b", t):
                tp_text = re.sub(r"(?i)^TP\s*:?\s*", "", t).strip()
                labeled = True
            elif re.match(r"(?i)^SL\b", t):
                sl_text = re.sub(r"(?i)^SL\s*:?\s*", "", t).strip()
                labeled = True
        if not labeled:
            unlabeled.append(text)
    if tp_text is None and unlabeled:
        # Bare values without labels — document order is TP, then SL
        # (matches the "TP/SL" column header).
        tp_text = unlabeled.pop(0)
    if sl_text is None and unlabeled:
        sl_text = unlabeled.pop(0)
    return tp_text, sl_text


def _read_sl_tp_from_row(row) -> tuple[str | None, str | None]:
    """Read the displayed SL/TP cell texts from an open-position row.

    Primary source of truth: [data-testid="security-orders"] cells
    ("-" = unset). Falls back to the legacy fuzzy testid probe only when
    no security-orders cell exists in the row.

    Returns (sl_text, tp_text); each is None if no matching cell was found.
    Unset cells typically show "-" / "—" / "".
    """
    tp_t, sl_t = _read_security_orders(row)
    if tp_t is not None or sl_t is not None:
        return sl_t, tp_t
    sl_text, tp_text = None, None
    cands = row.locator(
        '[data-testid*="sl"], [data-testid*="stop"], '
        '[data-testid*="tp"], [data-testid*="take"]'
    )
    try:
        n = cands.count()
    except Exception:
        return None, None
    for i in range(min(n, 20)):
        el = cands.nth(i)
        try:
            tid = (el.get_attribute("data-testid") or "").lower()
        except Exception:
            continue
        # Skip action buttons / dialog toggles, not value cells
        if "btn" in tid or "toggle" in tid or "dialog" in tid or "header" in tid:
            continue
        try:
            text = el.inner_text(timeout=1500).strip()
        except Exception:
            continue
        is_sl = ("stop" in tid) or ("-sl" in tid) or ("_sl" in tid) or tid.endswith("sl")
        is_tp = ("take" in tid) or ("tp" in tid and "tpsl-btn" not in tid)
        if is_sl and sl_text is None:
            sl_text = text
        elif is_tp and tp_text is None:
            tp_text = text
        elif not is_sl and not is_tp:
            continue
    return sl_text, tp_text


def _sl_tp_value_is_set(text: str | None) -> bool:
    """True if a row SL/TP cell shows a real numeric value (not '-' / empty)."""
    if text is None:
        return False
    t = text.strip()
    if t in ("", "-", "—", "–", "N/A", "n/a", "none", "None"):
        return False
    try:
        float(t.replace(",", ""))
        return True
    except ValueError:
        return False


def ensure_positions_tab(page: Page) -> None:
    """Make sure the Open Positions / Positions ouvertes tab is active so the
    position rows render. No-op if rows are already visible."""
    try:
        rows = page.locator(SEL_POSITION_ROW)
        if rows.count() > 0 and rows.first.is_visible():
            return
    except Exception:
        pass
    for name in ("Positions ouvertes", "Open Positions"):
        try:
            tab = page.get_by_text(name, exact=False)
            if tab.count() > 0:
                tab.first.click(timeout=5000)
                page.wait_for_timeout(800)
                return
        except Exception:
            continue


def list_open_positions(page: Page, timeout: int = 8000) -> list[dict]:
    """Scan ALL live open-position rows on the trade page.

    This is the ONLY source of truth for "what needs work": the TP/SL column
    state is read from each row's [data-testid="security-orders"] cells.
    A row is a candidate needing SL/TP only when BOTH sides show "-"
    (needs_sl_tp=True).

    Returns a list of dicts: {index, direction, volume, entry_price,
    sl_text, tp_text, sl_set, tp_set, needs_sl_tp, row}.
    Empty list if none visible.
    """
    ensure_positions_tab(page)
    rows = page.locator(SEL_POSITION_ROW)
    try:
        rows.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeout:
        return []

    positions = []
    try:
        count = rows.count()
    except Exception:
        return []
    for i in range(count):
        row = rows.nth(i)
        direction = None
        try:
            badges = row.locator(".ui-badge")
            for bi in range(badges.count()):
                try:
                    t = badges.nth(bi).inner_text(timeout=2000).strip().lower()
                except Exception:
                    continue
                if t in ("acheter", "buy", "long"):
                    direction = "buy"
                    break
                if t in ("vendre", "sell", "short"):
                    direction = "sell"
                    break
        except Exception:
            pass
        volume = None
        try:
            vol_el = row.locator(SEL_POSITION_VOLUME)
            if vol_el.count() > 0:
                volume = float(
                    vol_el.first.inner_text(timeout=2000).strip().replace(",", "")
                )
        except (ValueError, Exception):
            volume = None
        entry_price = _read_entry_price_from_row(row)
        sl_text, tp_text = _read_sl_tp_from_row(row)
        sl_set = _sl_tp_value_is_set(sl_text)
        tp_set = _sl_tp_value_is_set(tp_text)
        positions.append({
            "index": i,
            "direction": direction,
            "volume": volume,
            "entry_price": entry_price,
            "sl_text": sl_text,
            "tp_text": tp_text,
            "sl_set": sl_set,
            "tp_set": tp_set,
            # Candidate needing SL/TP: BOTH sides "-" in the live row.
            "needs_sl_tp": not sl_set and not tp_set,
            "row": row,
        })
    return positions

