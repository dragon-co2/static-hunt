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
    'ARROW_TAB_INDEX': 6, 'BANK_TAB_COUNT': 6,
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

ARROW_TAB_INDEX = _S['ARROW_TAB_INDEX']  # warehouse tab #7 (0-indexed)
BANK_TAB_COUNT  = _S['BANK_TAB_COUNT']   # tabs 0-5 are regular banks; tab 6 is reserved for arrows

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


def _click_arrow_in_warehouse():
    """Scans the warehouse grid itself (not the whole screen) for a cell matching
    arrow.jpg and clicks it — avoids accidentally matching an arrow icon elsewhere,
    e.g. still visible in the inventory panel."""
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)

    wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
    if not wh_panel:
        print('  [ARROWS] Warehouse not visible.')
        return False

    wpx, wpy, _, _ = wh_panel
    wox = wpx + WH_OFFSET_X
    woy = wpy + WH_OFFSET_Y

    for r in range(WH_ROWS):
        for c in range(WH_COLS):
            x1 = wox + c * WH_SLOT_W
            y1 = woy + r * WH_SLOT_H
            crop = gray[y1:y1 + WH_SLOT_H, x1:x1 + WH_SLOT_W]
            if _is_arrow(crop):
                x, y = x1 + WH_SLOT_W // 2, y1 + WH_SLOT_H // 2
                _click_at(x, y)
                print(f'  [ARROWS] Clicked arrow in warehouse @ ({x},{y})')
                return True

    print('  [ARROWS] arrow.jpg not found in warehouse.')
    return False


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


def _right_click_arrow_in_inventory(retries=8, delay=0.5):
    """Right-clicks the arrow stack in the inventory — used to equip it when the
    inventory had none at all before this grab. Retries for a bit since the item
    can take a moment to actually land in the inventory after the warehouse click."""
    for attempt in range(retries):
        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[0]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)

        inv_panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
        if inv_panel:
            item = _find_arrow_in_inventory(gray, inv_panel)
            if item:
                x, y = item
                pyautogui.moveTo(x, y, duration=0.05)
                time.sleep(0.05)
                pyautogui.rightClick()
                print(f'  [ARROWS] Right-clicked arrow @ ({x},{y})')

                time.sleep(1)
                pyautogui.keyDown('alt')
                time.sleep(0.1)
                pyautogui.moveTo(x, y, duration=0.05)
                # pyautogui.click()
                time.sleep(0.1)
                pyautogui.keyUp('alt')
                print(f'  [ARROWS] Alt+clicked arrow @ ({x},{y})')
                return True

        time.sleep(delay)

    print('  [ARROWS] No arrow found in inventory to right-click.')
    return False


def init_bank_tab(tab_idx=None):
    """Clicks one of the 6 regular bank tabs (0-5) — used once per account on the first
    loop to initialize the warehouse view, in case it was left on the arrows tab (tab 7)
    from a previous run."""
    if tab_idx is None:
        tab_idx = random.randrange(BANK_TAB_COUNT)
    return _click_warehouse_tab(tab_idx)


def ensure_arrows():
    """If the inventory has no arrows at all, opens warehouse tab 7, clicks the arrow
    stack to grab one, then right-clicks it in the inventory to equip it."""
    count = count_arrows()
    if count is None or count > 0:
        return

    print(f'[ARROWS] Empty — grabbing from warehouse tab {ARROW_TAB_INDEX + 1}...')
    if not _click_warehouse_tab(ARROW_TAB_INDEX):
        return
    time.sleep(0.3)
    if not _click_arrow_in_warehouse():
        return

    time.sleep(0.3)
    _right_click_arrow_in_inventory()

    time.sleep(0.3)
    _click_warehouse_tab(random.randrange(BANK_TAB_COUNT))


if __name__ == '__main__':
    ensure_arrows()
