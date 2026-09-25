import os
import time
import random
import ctypes
import numpy as np
import cv2
import mss
import pyautogui

from _paths import app_root

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

_DIR = os.path.join(app_root(), 'reference_images')
INVENTORY_TITLE_PATH = os.path.join(_DIR, 'inventory_title.jpg')
WAREHOUSE_TITLE_PATH = os.path.join(_DIR, 'warehouse_title.jpg')
ARROW_PATH           = os.path.join(_DIR, 'arrow.jpg')

from dragon_settings import get_settings
_S = get_settings('grab_arrows', {
    'INV_OFFSET_X': 18, 'INV_OFFSET_Y': 10, 'INV_SLOT_W': 43, 'INV_SLOT_H': 43,
    'INV_COLS': 5, 'INV_ROWS': 8,
    'WH_OFFSET_X': 120, 'WH_OFFSET_Y': 40, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,
    'WH_COLS': 5, 'WH_ROWS': 4,
    'WH_TAB_X': 5, 'WH_TAB_Y': 26, 'WH_TAB_W': 110, 'WH_TAB_H': 31, 'WH_TAB_COUNT': 7,
    'CONF_INV': 0.3, 'CONF_WH': 0.35, 'CONF_ARROW': 0.5,
    'BANK_TAB_COUNT': 6,
})

INV_OFFSET_X = _S['INV_OFFSET_X']
INV_OFFSET_Y = _S['INV_OFFSET_Y']
INV_SLOT_W   = _S['INV_SLOT_W']
INV_SLOT_H   = _S['INV_SLOT_H']
INV_COLS     = _S['INV_COLS']
INV_ROWS     = _S['INV_ROWS']

WH_OFFSET_X  = _S['WH_OFFSET_X']
WH_OFFSET_Y  = _S['WH_OFFSET_Y']
WH_SLOT_W    = _S['WH_SLOT_W']
WH_SLOT_H    = _S['WH_SLOT_H']
WH_COLS      = _S['WH_COLS']
WH_ROWS      = _S['WH_ROWS']

WH_TAB_X     = _S['WH_TAB_X']
WH_TAB_Y     = _S['WH_TAB_Y']
WH_TAB_W     = _S['WH_TAB_W']
WH_TAB_H     = _S['WH_TAB_H']
WH_TAB_COUNT = _S['WH_TAB_COUNT']

CONF_INV   = _S['CONF_INV']
CONF_WH    = _S['CONF_WH']
CONF_ARROW = _S['CONF_ARROW']

BANK_TAB_COUNT  = _S['BANK_TAB_COUNT']   # tabs 0-5 are regular banks (used by init_bank_tab)
TRIALS          = 5                      # attempts per step before giving up on validating it

_tmpl_inv = cv2.imread(INVENTORY_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_wh  = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_arrow = cv2.imread(ARROW_PATH,         cv2.IMREAD_GRAYSCALE)


def _find(gray, template, threshold):
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


def _is_arrow(cell_gray):
    th, tw = _tmpl_arrow.shape[:2]
    if cell_gray.shape[0] < th or cell_gray.shape[1] < tw:
        return False
    res = cv2.matchTemplate(cell_gray, _tmpl_arrow, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(res)[1] >= CONF_ARROW


def count_arrows():
    """Counts inventory cells matching arrow.jpg. Returns None if the inventory isn't visible."""
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)

    inv_panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
    if not inv_panel:
        print('[ARROWS] Inventory not visible.')
        return None

    ipx, ipy, _, _ = inv_panel
    iox = ipx + INV_OFFSET_X
    ioy = ipy + INV_OFFSET_Y

    count = 0
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = iox + c * INV_SLOT_W
            y1 = ioy + r * INV_SLOT_H
            crop = gray[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W]
            if _is_arrow(crop):
                count += 1

    print(f'[ARROWS] {count} arrow(s) in inventory')
    return count


def _click_at(x, y):
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    pyautogui.mouseDown()
    time.sleep(0.08)
    pyautogui.mouseUp()


def _click_warehouse_tab(tab_idx):
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
    if not wh_panel:
        print('[ARROWS] Warehouse not visible — cannot switch tab.')
        return False

    wpx, wpy, _, _ = wh_panel
    tab_x = wpx + WH_TAB_X + WH_TAB_W // 2
    tab_y = wpy + WH_TAB_Y + tab_idx * WH_TAB_H + WH_TAB_H // 2
    _click_at(tab_x, tab_y)
    print(f'  [ARROWS] Clicked warehouse tab {tab_idx + 1} @ ({tab_x},{tab_y})')
    return True


def _grab_gray():
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    return cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)


def _run_step(action, check, expect, trials=TRIALS, verify_tries=6, verify_delay=0.5):
    """Runs `action`, then validates it by polling `check` (up to verify_tries * verify_delay
    seconds). Prints [OK]/[FAIL]; re-runs the action up to `trials` times until it validates."""
    for trial in range(1, trials + 1):
        action()
        for _ in range(verify_tries):
            time.sleep(verify_delay)
            if check():
                print(f'  [OK] {expect}')
                return True
        print(f'  [FAIL] {expect} — not validated (trial {trial}/{trials})')
    print(f'  [FAIL] {expect} — gave up after {trials} trials.')
    return False


def _panels_visible():
    gray = _grab_gray()
    return (_find(gray, _tmpl_wh, threshold=CONF_WH) is not None
            and _find(gray, _tmpl_inv, threshold=CONF_INV) is not None)


def _find_arrow_in_warehouse(gray, wh_panel):
    """Returns the center of the first warehouse grid cell matching arrow.jpg, or None.
    Scans only the grid (not the whole screen) so arrows in the inventory never match."""
    wpx, wpy, _, _ = wh_panel
    wox = wpx + WH_OFFSET_X
    woy = wpy + WH_OFFSET_Y
    for r in range(WH_ROWS):
        for c in range(WH_COLS):
            x1 = wox + c * WH_SLOT_W
            y1 = woy + r * WH_SLOT_H
            crop = gray[y1:y1 + WH_SLOT_H, x1:x1 + WH_SLOT_W]
            if _is_arrow(crop):
                return (x1 + WH_SLOT_W // 2, y1 + WH_SLOT_H // 2)
    return None


def _arrow_in_inventory():
    """Returns the center of the first inventory cell holding an arrow, or None."""
    gray = _grab_gray()
    inv_panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
    return _find_arrow_in_inventory(gray, inv_panel) if inv_panel else None


def _search_tabs_for_arrow():
    """Cycles through every warehouse tab and returns the center of the first arrow found
    in the grid (leaving that tab open), or None if no tab has one."""
    for tab in range(WH_TAB_COUNT):
        if not _click_warehouse_tab(tab):
            return None
        time.sleep(0.4)
        gray = _grab_gray()
        wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
        if not wh_panel:
            return None
        pos = _find_arrow_in_warehouse(gray, wh_panel)
        if pos:
            print(f'  [OK] arrow.jpg found in tab {tab + 1} @ {pos}')
            return pos
        print(f'  [ARROWS] no arrow in tab {tab + 1}')
    print(f'  [FAIL] arrow.jpg not found in any of the {WH_TAB_COUNT} tabs.')
    return None


def _find_arrow_in_inventory(gray, inv_panel):
    ipx, ipy, _, _ = inv_panel
    iox = ipx + INV_OFFSET_X
    ioy = ipy + INV_OFFSET_Y
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = iox + c * INV_SLOT_W
            y1 = ioy + r * INV_SLOT_H
            crop = gray[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W]
            if _is_arrow(crop):
                return (x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2)
    return None


def init_bank_tab(tab_idx=None):
    """Clicks one of the 6 regular bank tabs (0-5) — used once per account on the first
    loop to initialize the warehouse view, in case it was left on the arrows tab (tab 7)
    from a previous run."""
    if tab_idx is None:
        tab_idx = random.randrange(BANK_TAB_COUNT)
    return _click_warehouse_tab(tab_idx)


def ensure_arrows():
    """If the inventory has no arrows at all:
    1. Validate that the warehouse and inventory panels are both visible.
    2. Cycle through the warehouse tabs; click the first arrow.jpg found in the grid.
       -> validated by an arrow appearing in the inventory.
    3. Right-click that arrow in the inventory to equip it.
    Each step is retried up to TRIALS times; stops if one never validates."""
    print('[ARROWS] panels...')
    if not _run_step(lambda: None, _panels_visible, 'warehouse_title and inventory_title visible'):
        print('[ARROWS] Aborted — panels not visible.')
        return False

    count = count_arrows()
    if count is None or count > 0:
        return True

    print('[ARROWS] Empty — searching warehouse tabs...')
    pos = _search_tabs_for_arrow()
    if not pos:
        return False

    def _click_found_arrow():
        _click_at(*pos)
        print(f'  [CLICK] arrow in warehouse @ {pos}')

    if not _run_step(_click_found_arrow, lambda: _arrow_in_inventory() is not None,
                     'arrow appeared in inventory'):
        print('[ARROWS] Aborted — arrow never reached the inventory.')
        return False

    x, y = _arrow_in_inventory()
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    pyautogui.rightClick()
    print(f'  [CLICK] right-clicked arrow in inventory @ ({x},{y})')
    return True


if __name__ == '__main__':
    ensure_arrows()
