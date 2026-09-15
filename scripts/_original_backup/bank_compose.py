import tkinter as tk
import threading
import time
import os
import random
import ctypes
import ctypes.wintypes
import numpy as np
import cv2
import mss
import pyautogui

from _paths import app_root

try:
    if not ctypes.windll.user32.IsProcessDPIAware():
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

_DIR = os.path.join(app_root(), 'reference_images')
WAREHOUSE_TITLE_PATH = os.path.join(_DIR, 'warehouse_title.jpg')
REMOTE_COMPOSE_PATH = os.path.join(_DIR, 'remote_compose.jpg')
CANCEL_PATH = os.path.join(_DIR, 'cancel.jpg')
VIP_EXIT_PATH = os.path.join(_DIR, 'vip_exit.jpg')
INVENTORY_TITLE_PATH = os.path.join(_DIR, 'inventory_title.jpg')
COMPOSE_ITEMS_PATH = os.path.join(_DIR, 'compose_items.jpg')
EMPTYCELL_PATH = os.path.join(_DIR, 'emptycell.jpg')

WH_OFFSET_X  = 120
WH_OFFSET_Y  = 40
WH_SLOT_W    = 43
WH_SLOT_H    = 43
WH_COLS      = 5
WH_ROWS      = 4

WH_TAB_X     = 5
WH_TAB_Y     = 26
WH_TAB_W     = 110
WH_TAB_H     = 31
WH_TAB_COUNT = 7

CONF_WH      = 0.35
CONF_EMPTY   = 0.9
CLICK_MULTIPLIER = 1.5  # extra clicks over the detected item count, e.g. 1.5 = +50%
MEM_WINDOW   = 'GhostArrow'  # partial game window title, same as navigation.py
VIS          = 1
CYCLES       = 7
WAREHOUSE_MIN_ITEMS = 20  # only run the compose flow if the warehouse has at least this many items
VIP_BTN_PATH = os.path.join(_DIR, 'vip_btn.png')  # VIP button image, searched for on screen

# ── Inventory / compose dialog ─────────────────────────────────────────────────
CONF_INV     = 0.3
CONF_DIALOG  = 0.6
INV_OFFSET_X = 18
INV_OFFSET_Y = 10
INV_SLOT_W   = 43
INV_SLOT_H   = 43
INV_COLS     = 5
INV_ROWS     = 8

# Fixed offsets from the compose dialog's top-left corner.
DIALOG_MAIN_OFF    = (68,  80)   # center of the left (Main) slot
DIALOG_MINOR_OFF   = (200, 80)   # center of the right (Minor) slot
DIALOG_COMPOSE_OFF = (167, 260)  # center of the Compose button
DIALOG_CANCEL_OFF  = (244, 260)  # center of the Cancel button

WAIT = 0.4  # seconds between compose sub-steps

_tmpl_wh = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_inv = cv2.imread(INVENTORY_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_dialog = cv2.imread(COMPOSE_ITEMS_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_empty = cv2.imread(EMPTYCELL_PATH, cv2.IMREAD_GRAYSCALE)


def _count_warehouse_items(gray, wh_panel):
    """Counts non-empty cells in the warehouse grid."""
    px, py, _, _ = wh_panel
    ox, oy = px + WH_OFFSET_X, py + WH_OFFSET_Y
    count = 0
    for r in range(WH_ROWS):
        for c in range(WH_COLS):
            x1 = ox + c * WH_SLOT_W
            y1 = oy + r * WH_SLOT_H
            crop = gray[y1:y1 + WH_SLOT_H, x1:x1 + WH_SLOT_W]
            if _tmpl_empty is not None and crop.shape[0] >= _tmpl_empty.shape[0] and crop.shape[1] >= _tmpl_empty.shape[1]:
                res = cv2.matchTemplate(crop, _tmpl_empty, cv2.TM_CCOEFF_NORMED)
                if cv2.minMaxLoc(res)[1] >= CONF_EMPTY:
                    continue
            count += 1
    return count


def _load_badge(name, threshold):
    bgra = cv2.imread(os.path.join(_DIR, f'{name}.png'), cv2.IMREAD_UNCHANGED)
    bgr  = bgra[:, :, :3]
    mask = bgra[:, :, 3]
    n_px   = float(np.count_nonzero(mask)) * 3
    max_sq = n_px * (255.0 ** 2)

    def score(cell_bgr):
        th, tw = bgr.shape[:2]
        if cell_bgr.shape[0] < th or cell_bgr.shape[1] < tw:
            return 0.0
        res = cv2.matchTemplate(cell_bgr, bgr, cv2.TM_SQDIFF, mask=mask)
        return 1.0 - (float(cv2.minMaxLoc(res)[0]) / max_sq)

    return {'label': f'+{name[-1]}', 'score': score, 'threshold': threshold}


BADGES = [
    _load_badge('plus1', 0.95),
    _load_badge('plus2', 0.96),
    _load_badge('plus3', 0.96),
    _load_badge('plus4', 0.96),
]

DRAGONBALL = _load_badge('dragonball', 0.95)
DRAGONBALL['label'] = 'dragonball'
DRAGONBALL_NEED = 10  # right-click one once at least this many are in the inventory, to convert them into a scroll

_ui = {'main': None, 'minor': None, 'compose': None, 'cancel': None}


def _focus_game_window():
    found_hwnd = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        if MEM_WINDOW.lower() in buf.value.lower():
            found_hwnd.value = hwnd
            return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    if found_hwnd.value:
        ctypes.windll.user32.ShowWindow(found_hwnd.value, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(found_hwnd.value)
        time.sleep(0.2)
        return True
    return False


def _best_match_score(path):
    tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tmpl is None:
        return 0.0
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    if gray.shape[0] < tmpl.shape[0] or gray.shape[1] < tmpl.shape[1]:
        return 0.0
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(res)[1])


def _click_image(path, confidence=0.8):
    try:
        loc = pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except pyautogui.ImageNotFoundException:
        loc = None
    if not loc:
        best = _best_match_score(path)
        print(f'  [CLICK] not found: {os.path.basename(path)}  (best score={best:.2f}, need {confidence})')
        return False
    x, y = pyautogui.center(loc)
    _focus_game_window()
    pyautogui.moveTo(x, y, duration=0.15)
    time.sleep(0.1)
    pyautogui.mouseDown()
    time.sleep(0.08)
    pyautogui.mouseUp()
    print(f'  [CLICK] {os.path.basename(path)} @ ({x},{y})')
    return True


def _is_visible(path, confidence=0.8):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True) is not None
    except pyautogui.ImageNotFoundException:
        return False


def _warehouse_visible():
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[0])), cv2.COLOR_BGRA2GRAY)
    return _find(gray, _tmpl_wh, threshold=CONF_WH) is not None


def _click_verify(path, verify_fn, retries=5, delay=0.5, confidence=0.8):
    """Clicks `path`, then calls verify_fn() to confirm it worked; retries the click if not."""
    for attempt in range(retries):
        _click_image(path, confidence=confidence)
        time.sleep(delay)
        if verify_fn():
            return True
        print(f'  [CHECK] {os.path.basename(path)} click not confirmed — retrying ({attempt + 1}/{retries})')
    print(f'  [CHECK] {os.path.basename(path)} still not confirmed after retries.')
    return False


def _click_vip_btn(verify_fn, retries=5, delay=0.5, confidence=0.5):
    """Finds and clicks the VIP button on screen, then calls verify_fn() to confirm it
    worked; retries if not found or not confirmed."""
    for attempt in range(retries):
        _click_image(VIP_BTN_PATH, confidence=confidence)
        time.sleep(delay)
        if verify_fn():
            return True
        print(f'  [CHECK] vip_btn click not confirmed — retrying ({attempt + 1}/{retries})')
    print('  [CHECK] vip_btn still not confirmed after retries.')
    return False


def open_remote_compose():
    _click_image(VIP_BTN_PATH)
    time.sleep(0.2)
    _click_image(REMOTE_COMPOSE_PATH)


def _click_first_cell(times=None, delay=0.1):
    with mss.MSS() as sct:
        mon = sct.monitors[0]
        raw = np.array(sct.grab(mon))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
    if not wh_panel:
        print('  [CLICK] warehouse panel not found')
        return False

    if times is None:
        item_count = _count_warehouse_items(gray, wh_panel)
        times = round(item_count * CLICK_MULTIPLIER)
        print(f'  [CLICK] {item_count} item(s) in warehouse — clicking {times} times (x{CLICK_MULTIPLIER})')
        if times == 0:
            return True

    px, py, _, _ = wh_panel
    _focus_game_window()
    for i in range(times):
        r, c = random.choice([(0, 0), (0, 1)])
        x = px + WH_OFFSET_X + c * WH_SLOT_W + WH_SLOT_W // 2
        y = py + WH_OFFSET_Y + r * WH_SLOT_H + WH_SLOT_H // 2
        pyautogui.moveTo(x, y, duration=0.15)
        time.sleep(delay)
        pyautogui.mouseDown()
        time.sleep(0.08)
        pyautogui.mouseUp()
        print(f'  [CLICK] cell(r{r},c{c}) @ ({x},{y})  {i + 1}/{times}')
    return True


def _click_at(x, y):
    _focus_game_window()
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.1)
    pyautogui.mouseDown()
    time.sleep(0.1)
    pyautogui.mouseUp()


def _mob_click():
    sw, sh = pyautogui.size()
    x = max(10, min(sw - 10, sw // 2 + random.randint(-500, 500)))
    y = max(10, min(sh - 10, sh // 2 + random.randint(-500, 500)))
    _focus_game_window()
    pyautogui.moveTo(x, y, duration=0.05)
    pyautogui.rightClick()
    print(f'  [MOB] right-click @ ({x},{y})')


def _convert_dragonballs():
    """While the inventory has at least DRAGONBALL_NEED dragonball.png items, right-clicks
    one to convert them into a scroll. Re-scans fresh each time — converting can reshuffle
    or resort the rest of the inventory, so cached positions can't be trusted."""
    while True:
        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[0]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
        if not panel:
            print('  [DRAGONBALL] Inventory not visible — skipping.')
            return

        ox, oy = panel[0] + INV_OFFSET_X, panel[1] + INV_OFFSET_Y
        count = _count_badge_cells(raw, ox, oy, DRAGONBALL)
        if count < DRAGONBALL_NEED:
            print(f'  [DRAGONBALL] {count}/{DRAGONBALL_NEED} — not enough to convert.')
            return

        item = _find_badge_cell(raw, ox, oy, DRAGONBALL)
        if not item:
            return
        _focus_game_window()
        pyautogui.moveTo(*item, duration=0.05)
        time.sleep(0.05)
        pyautogui.rightClick()
        print(f'  [DRAGONBALL] {count} found — right-clicked one @ {item} to convert to scroll')
        time.sleep(WAIT)


def _find_badge_cell_from_end(raw, ox, oy, badge, offset=0):
    """Returns center of the Nth-from-last matching cell (offset=0 -> last, 1 -> second-to-last), or None."""
    matches = []
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = ox + c * INV_SLOT_W
            y1 = oy + r * INV_SLOT_H
            crop = raw[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W, :3].copy()
            if badge['score'](crop) >= badge['threshold']:
                matches.append((x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2))
    if offset >= len(matches):
        return None
    return matches[-1 - offset]


def _find_badge_cell(raw, ox, oy, badge):
    """Returns center of the last inventory cell matching the badge, or None."""
    return _find_badge_cell_from_end(raw, ox, oy, badge, offset=0)


def _count_badge_cells(raw, ox, oy, badge):
    count = 0
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = ox + c * INV_SLOT_W
            y1 = oy + r * INV_SLOT_H
            crop = raw[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W, :3].copy()
            if badge['score'](crop) >= badge['threshold']:
                count += 1
    return count


def _locate_compose_ui():
    """Finds the compose dialog and derives main/minor/compose slot coords."""
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[0])), cv2.COLOR_BGRA2GRAY)
    dlg = _find(gray, _tmpl_dialog, threshold=CONF_DIALOG)
    if not dlg:
        print('  [UI] compose dialog not found')
        return False
    dx, dy, _, _ = dlg
    _ui['main']    = (dx + DIALOG_MAIN_OFF[0],    dy + DIALOG_MAIN_OFF[1])
    _ui['minor']   = (dx + DIALOG_MINOR_OFF[0],   dy + DIALOG_MINOR_OFF[1])
    _ui['compose'] = (dx + DIALOG_COMPOSE_OFF[0], dy + DIALOG_COMPOSE_OFF[1])
    _ui['cancel']  = (dx + DIALOG_CANCEL_OFF[0],  dy + DIALOG_CANCEL_OFF[1])
    print(f"  [UI] dialog@({dx},{dy})  main={_ui['main']}  minor={_ui['minor']}  "
          f"compose={_ui['compose']}  cancel={_ui['cancel']}")
    return True


def _ensure_compose_open():
    """Verifies the compose dialog is visible; reopens it via remote_compose if it got closed."""
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[0])), cv2.COLOR_BGRA2GRAY)
    if _find(gray, _tmpl_dialog, threshold=CONF_DIALOG):
        return True
    print('  [CHECK] compose dialog not visible — reopening via remote_compose')
    _click_image(REMOTE_COMPOSE_PATH)
    time.sleep(0.5)
    return _locate_compose_ui()


def _compose_step(badge, offset=0):
    """The 8 compose sub-steps (S1-S8) for one badge tier. Returns False if it can't proceed.
    `offset` picks which item S1 selects (0=last, 1=second-to-last, ...) — only meaningful
    for the first call in a tier's loop, since later calls just see whatever is left."""
    label = badge['label']
    with mss.MSS() as sct:
        mon = sct.monitors[0]

        panel = None
        for _ in range(10):
            raw = np.array(sct.grab(mon))
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
            if panel:
                break
            time.sleep(1.0)
        if not panel:
            print(f'[{label}] Inventory not visible.')
            return False

        def fresh_item():
            r = np.array(sct.grab(mon))
            g = cv2.cvtColor(r, cv2.COLOR_BGRA2GRAY)
            p = _find(g, _tmpl_inv, threshold=CONF_INV)
            if not p:
                return None
            return _find_badge_cell(r, p[0] + INV_OFFSET_X, p[1] + INV_OFFSET_Y, badge)

        px, py, _, _ = panel
        ox, oy = px + INV_OFFSET_X, py + INV_OFFSET_Y

        # S1 - select item
        item = _find_badge_cell_from_end(raw, ox, oy, badge, offset=offset)
        if not item:
            print(f'[{label}] No item at offset {offset}.')
            return False
        if _count_badge_cells(raw, ox, oy, badge) <= 2:
            print(f'[{label}] Only 1-2 items — skipping to next badge.')
            return False
        _click_at(*item)
        print(f'  S1: {label} item at {item} (offset={offset})'); time.sleep(WAIT)

        # S2 - main slot
        if not _ensure_compose_open():
            print(f'[{label}] Compose dialog gone and could not be reopened.'); return False
        _click_at(*_ui['main'])
        print(f'  S2: main at {_ui["main"]}'); time.sleep(WAIT)

        # Count after S2 — need at least 2 more items (S3 + S6)
        _r = np.array(sct.grab(mon))
        _p = _find(cv2.cvtColor(_r, cv2.COLOR_BGRA2GRAY), _tmpl_inv, threshold=CONF_INV)
        if _p:
            _rem = _count_badge_cells(_r, _p[0] + INV_OFFSET_X, _p[1] + INV_OFFSET_Y, badge)
            print(f'[{label}] Items: {_rem}')
            if _rem <= 1:
                print(f'[{label}] Not enough items after S2 — moving to next badge.')
                return False

        # S3 - select item again
        item = fresh_item()
        if not item:
            print(f'[{label}] No item for S3.'); return False
        _click_at(*item)
        print(f'  S3: {label} item at {item}'); time.sleep(WAIT)

        # S4 - minor slot
        _click_at(*_ui['minor'])
        print(f'  S4: minor at {_ui["minor"]}'); time.sleep(WAIT)

        # S5 - compose
        _click_at(*_ui['compose'])
        print('  S5: compose'); time.sleep(WAIT)

        # S6 - select item again
        item = fresh_item()
        if not item:
            print(f'[{label}] No item for S6.'); return False
        _click_at(*item)
        print(f'  S6: {label} item at {item}'); time.sleep(WAIT)

        # S7 - minor slot
        _click_at(*_ui['minor'])
        print(f'  S7: minor at {_ui["minor"]}'); time.sleep(WAIT)

        # S8 - compose
        _click_at(*_ui['compose'])
        print('  S8: compose')
        return True


def _run_badge_tier(badge, first_offset=0):
    """Repeats the 8-step cycle until 0 or 1-2 items of this badge remain.
    `first_offset` only affects the very first cycle's S1 pick; later cycles use offset=0.
    Returns True if at least one cycle actually composed items."""
    label = badge['label']
    composed = False
    offset = first_offset
    while True:
        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[0]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
        if not panel:
            print(f'[{label}] Panel not found — skipping.')
            return composed
        ox, oy = panel[0] + INV_OFFSET_X, panel[1] + INV_OFFSET_Y
        count = _count_badge_cells(raw, ox, oy, badge)
        if count == 0:
            print(f'[{label}] No more items — moving to next badge.')
            return composed
        if count <= 2:
            print(f'[{label}] Only {count} item(s) — moving to next badge.')
            return composed

        if not _compose_step(badge, offset=offset):
            return composed
        composed = True
        offset = 0
        time.sleep(WAIT)


def _warehouse_item_count():
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
    return _count_warehouse_items(gray, wh_panel) if wh_panel else 0


def _compose_setup():
    """MS1-MS3, once: open the compose dialog and withdraw items from the warehouse."""
    if not _click_vip_btn(lambda: _is_visible(REMOTE_COMPOSE_PATH)):   # MS1
        print('[SEQ] VIP menu never opened.')
        return False
    time.sleep(0.3)
    _mob_click()

    if not _click_verify(REMOTE_COMPOSE_PATH, _warehouse_visible):   # MS2
        print('[SEQ] Warehouse never opened.')
        return False
    time.sleep(0.3)
    _mob_click()

    _click_first_cell(delay=0.1)   # MS3 - withdraw items from warehouse to inventory
    _mob_click()

    for attempt in range(15):
        if _locate_compose_ui():
            return True
        print(f'  [UI] not ready ({attempt + 1}/15)')
        time.sleep(1.0)
    print('[SEQ] Could not find compose dialog.')
    return False


def _click_cancel():
    """Clicks Cancel at its stored position (fixed offset from the dialog) so a popup
    covering the on-screen Cancel image can't hide it from template matching."""
    if _ui['cancel']:
        _click_at(*_ui['cancel'])
        print(f"  [CLICK] cancel @ {_ui['cancel']}")
    else:
        _click_image(CANCEL_PATH)


def _ensure_dialog_closed(retries=5, delay=0.5):
    """Verifies the compose dialog is gone after clicking Cancel; re-clicks Cancel if it's still open."""
    for attempt in range(retries):
        with mss.MSS() as sct:
            gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[0])), cv2.COLOR_BGRA2GRAY)
        if not _find(gray, _tmpl_dialog, threshold=CONF_DIALOG):
            return True
        print(f'  [CHECK] compose dialog still open — retrying Cancel ({attempt + 1}/{retries})')
        _click_cancel()
        time.sleep(delay)
    print('  [CHECK] compose dialog still open after retries.')
    return False


def _run_compose_pass(offset):
    """MS4-MS7 for every badge tier. `offset` picks which item the tier's first S1 selects
    (0=last, 1=second-to-last, ...)."""
    for badge in BADGES:
        label = badge['label']
        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[0]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
        if not panel:
            print(f'[{label}] Inventory not visible — skipping.')
            continue

        ox, oy = panel[0] + INV_OFFSET_X, panel[1] + INV_OFFSET_Y
        count = _count_badge_cells(raw, ox, oy, badge)
        if count < 1:
            print(f'[{label}] No items — skipping to next badge.')
            continue

        _run_badge_tier(badge, first_offset=offset)         # MS4-MS6
        _mob_click()

    _click_cancel()                                         # MS7
    time.sleep(WAIT)
    _mob_click()
    _ensure_dialog_closed()
    _click_image(REMOTE_COMPOSE_PATH)                       # MS8 - reopen compose dialog
    time.sleep(0.5)
    _mob_click()


def run_compose(cycles=CYCLES, min_items=WAREHOUSE_MIN_ITEMS):
    item_count = _warehouse_item_count()
    print(f'  [CHECK] {item_count} item(s) in warehouse')
    if item_count < min_items:
        print(f'  [CHECK] Fewer than {min_items} — skipping compose entirely.')
        return

    if not _compose_setup():
        return

    _convert_dragonballs()

    for i in range(cycles):
        print(f'=== Compose cycle {i + 1}/{cycles} (offset={i}) ===')
        _run_compose_pass(offset=i)

    _click_cancel()
    time.sleep(WAIT)
    _click_image(VIP_EXIT_PATH)


def _find(gray, template, threshold=CONF_WH):
    if template is None:
        return None
    th, tw = template.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    if val >= threshold:
        return (loc[0], loc[1], tw, th)
    return None


def _scan_cells(gray, panel, offset_x, offset_y, slot_w, slot_h, cols, rows, classify):
    px, py, _, _ = panel
    ox = px + offset_x
    oy = py + offset_y
    cells = []
    hit_empty = False
    for r in range(rows):
        for c in range(cols):
            x1 = ox + c * slot_w
            y1 = oy + r * slot_h
            if hit_empty:
                color = '#444444'
            else:
                crop = gray[y1:y1 + slot_h, x1:x1 + slot_w]
                color = classify(crop)
                if color == '#444444':
                    hit_empty = True
            cells.append((x1, y1, slot_w, slot_h, color))
    return cells


def _make_overlay_items(canvas):
    wh_border = canvas.create_rectangle(0, 0, 0, 0, outline='', width=3)
    wh_label  = canvas.create_text(0, 0, text='', fill='#00aaff',
                                   font=('Arial', 9, 'bold'), anchor='nw')
    wh_tabs   = [(canvas.create_rectangle(0, 0, 0, 0, outline='', width=2),
                  canvas.create_text(0, 0, text='', fill='#ffaa00',
                                     font=('Arial', 7), anchor='nw'))
                 for _ in range(WH_TAB_COUNT)]
    wh_cells  = [canvas.create_rectangle(0, 0, 0, 0, outline='', fill='',
                                         stipple='gray25', width=2)
                 for _ in range(WH_COLS * WH_ROWS)]
    return wh_border, wh_label, wh_tabs, wh_cells


def _update_overlay(canvas, items, wh_panel, wh_cells):
    wh_border, wh_label, wh_tab_ids, wh_cell_ids = items

    if wh_panel:
        px, py, pw, ph = wh_panel
        canvas.coords(wh_border, px, py, px + pw, py + ph)
        canvas.itemconfig(wh_border, outline='#00aaff', state='normal')
        canvas.coords(wh_label, px, py - 14)
        canvas.itemconfig(wh_label, text=f'wh {pw}x{ph} @ ({px},{py})', state='normal')
        for t, (rect_id, text_id) in enumerate(wh_tab_ids):
            tx = px + WH_TAB_X
            ty = py + WH_TAB_Y + t * WH_TAB_H
            canvas.coords(rect_id, tx, ty, tx + WH_TAB_W, ty + WH_TAB_H)
            canvas.itemconfig(rect_id, outline='#ffaa00', state='normal')
            canvas.coords(text_id, tx + 4, ty + 4)
            canvas.itemconfig(text_id, text=str(t), state='normal')
        for i, (x1, y1, sw, sh, color) in enumerate(wh_cells):
            canvas.coords(wh_cell_ids[i], x1, y1, x1 + sw, y1 + sh)
            canvas.itemconfig(wh_cell_ids[i], outline=color, fill=color, state='normal')
        for i in range(len(wh_cells), len(wh_cell_ids)):
            canvas.itemconfig(wh_cell_ids[i], state='hidden')
    else:
        canvas.itemconfig(wh_border, state='hidden')
        canvas.itemconfig(wh_label, state='hidden')
        for rect_id, text_id in wh_tab_ids:
            canvas.itemconfig(rect_id, state='hidden')
            canvas.itemconfig(text_id, state='hidden')
        for cid in wh_cell_ids:
            canvas.itemconfig(cid, state='hidden')


def _scan_loop(canvas, root, overlay_items, run_once=False):
    prev_state = [None]
    with mss.MSS() as sct:
        mon = sct.monitors[0]
        while True:
            raw  = np.array(sct.grab(mon))
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
            wh_cells = _scan_cells(gray, wh_panel,
                                   WH_OFFSET_X, WH_OFFSET_Y,
                                   WH_SLOT_W, WH_SLOT_H,
                                   WH_COLS, WH_ROWS,
                                   lambda _: '#00aaff') if wh_panel else []

            if run_once:
                return

            state = (wh_panel, wh_cells)
            if state == prev_state[0]:
                time.sleep(0.3)
                continue
            prev_state[0] = state

            if canvas is not None:
                def draw(wh_panel=wh_panel, wh_cells=wh_cells):
                    _update_overlay(canvas, overlay_items, wh_panel, wh_cells)
                root.after(0, draw)
            time.sleep(0.3)


if __name__ == '__main__':
    run_compose()
    os._exit(0)

    if VIS:
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
        root.attributes('-topmost', True)
        root.attributes('-transparentcolor', 'black')
        root.configure(bg='black')

        canvas = tk.Canvas(root, bg='black', highlightthickness=0)
        canvas.pack(fill='both', expand=True)

        overlay_items = _make_overlay_items(canvas)

        def _set_clickthrough():
            hwnd = root.winfo_id()
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20)
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 0, 1)

        root.after(200, _set_clickthrough)

        threading.Thread(target=_scan_loop, args=(canvas, root, overlay_items), daemon=True).start()

        root.mainloop()
    else:
        _scan_loop(None, None, None, run_once=True)
        os._exit(0)
