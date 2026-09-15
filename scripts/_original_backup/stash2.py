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
EMPTY_PATH           = os.path.join(_DIR, 'emptycell.jpg')

INV_OFFSET_X = 18
INV_OFFSET_Y = 10
INV_SLOT_W   = 43
INV_SLOT_H   = 43
INV_COLS     = 5
INV_ROWS     = 8

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
WH_TAB_COUNT = 6

CONF        = 0.3
CONF_WH     = 0.3
CONF_EMPTY  = 0.95

ALT_HOLD     = 0.1   # seconds Alt is held before/after the click
TAB_DELAY    = 0.2   # seconds after clicking a tab, before the alt+click
STASH_DELAY  = 0.5   # seconds between each item moved

_tmpl_inv   = cv2.imread(INVENTORY_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_wh    = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_arrow = cv2.imread(ARROW_PATH,           cv2.IMREAD_GRAYSCALE)
_tmpl_empty = cv2.imread(EMPTY_PATH,           cv2.IMREAD_GRAYSCALE)


def _find(gray, template, threshold=CONF):
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


def _classify_cell(cell_gray):
    """Classifies an inventory cell as 'empty', 'arrow' (junk marker), or 'item'."""
    th, tw = _tmpl_empty.shape[:2]
    if cell_gray.shape[0] >= th and cell_gray.shape[1] >= tw:
        res = cv2.matchTemplate(cell_gray, _tmpl_empty, cv2.TM_CCOEFF_NORMED)
        if cv2.minMaxLoc(res)[1] >= CONF_EMPTY:
            return 'empty'
    th, tw = _tmpl_arrow.shape[:2]
    if cell_gray.shape[0] >= th and cell_gray.shape[1] >= tw:
        res = cv2.matchTemplate(cell_gray, _tmpl_arrow, cv2.TM_CCOEFF_NORMED)
        if cv2.minMaxLoc(res)[1] >= CONF:
            return 'arrow'
    return 'item'


def _find_panels():
    """Screenshots and locates both the inventory and warehouse panels."""
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    inv_panel = _find(gray, _tmpl_inv)
    wh_panel  = _find(gray, _tmpl_wh, threshold=CONF_WH)
    return gray, inv_panel, wh_panel


def _next_inv_item(gray, inv_panel):
    """Returns the center of the next stashable inventory cell, or None if there aren't any."""
    ipx, ipy, _, _ = inv_panel
    iox = ipx + INV_OFFSET_X
    ioy = ipy + INV_OFFSET_Y
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = int(iox + c * INV_SLOT_W)
            y1 = int(ioy + r * INV_SLOT_H)
            crop = gray[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W]
            if _classify_cell(crop) == 'item':
                return (x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2)
    return None


def _click_random_tab(wh_panel):
    """Clicks a random warehouse tab so items get spread across tabs instead of piling into one."""
    wpx, wpy, _, _ = wh_panel
    tab_idx = random.randrange(WH_TAB_COUNT)
    tab_x = int(wpx + WH_TAB_X + WH_TAB_W // 2)
    tab_y = int(wpy + WH_TAB_Y + tab_idx * WH_TAB_H + WH_TAB_H // 2)
    pyautogui.moveTo(tab_x, tab_y, duration=0.05)
    pyautogui.click()
    time.sleep(TAB_DELAY)
    return tab_idx


def _click(x, y):
    """Click while Alt is already held down (see stash_items) — the game's shortcut to
    instantly send an inventory item to the active warehouse tab."""
    pyautogui.moveTo(x, y, duration=0.05)
    pyautogui.click()


def stash_items(wait_if_dead=None):
    """Drains the inventory into the warehouse via Alt+click, shuffling to a random
    warehouse tab before every single item so they spread out instead of piling up.
    Alt is held down for the entire run rather than toggled per click."""
    moved = 0
    pyautogui.keyDown('alt')
    time.sleep(ALT_HOLD)
    try:
        while True:
            if wait_if_dead:
                wait_if_dead()

            gray, inv_panel, wh_panel = _find_panels()
            if not inv_panel or not wh_panel:
                print('[STASH2] Panel(s) not visible — aborting')
                return

            item = _next_inv_item(gray, inv_panel)
            if not item:
                print(f'[STASH2] Done — {moved} item(s) moved')
                return

            tab_idx = _click_random_tab(wh_panel)
            _click(*item)
            moved += 1
            print(f'  [STASH2] Moved {moved}: inv{item} -> tab{tab_idx}')
            time.sleep(STASH_DELAY)
    finally:
        time.sleep(ALT_HOLD)
        pyautogui.keyUp('alt')


if __name__ == '__main__':
    print('Starting in 3s...')
    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)
    stash_items()
