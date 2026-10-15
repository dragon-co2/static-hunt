import os
import time
import random
import ctypes
import cv2
import pyautogui

import game_input as gi  # real-device input via the Interception driver
import game_memory  # equipped arrow count, read from the game (read-only)

from _paths import app_root
from dragon_settings import get_settings
import screen  # shared primary-monitor capture + panel matching

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = False  # moving the mouse to a screen corner does NOT stop the script
pyautogui.PAUSE    = 0

_DIR = os.path.join(app_root(), 'reference_images')
# an arrow pack's icon WITHOUT its number, so a pack matches whatever amount it holds (9383 as
# well as 10000). Searched over a whole panel at once, not cell by cell.
ARROW_ICON_PATH = os.path.join(_DIR, 'arrow_icon.png')


INV_OFFSET_X = 18
INV_OFFSET_Y = 10
INV_SLOT_W   = 43
INV_SLOT_H   = 43
INV_COLS     = 5
INV_ROWS     = 8

WH_OFFSET_X = 120
WH_OFFSET_Y = 40
WH_SLOT_W   = 43
WH_SLOT_H   = 43
WH_COLS     = 5
WH_ROWS     = 4

WH_TAB_X     = 5
WH_TAB_Y     = 26
WH_TAB_W     = 110
WH_TAB_H     = 31
WH_TAB_COUNT = 7

CONF_ICON  = 0.85  # arrow packs score ~0.97-1.00, other items and empty cells < 0.5
ARROWS_MIN = 100   # default for the General setting: below this many equipped, take a new pack
CONF_INV   = screen.CONF_INV  # shared with every script (see screen.py)
CONF_WH    = screen.CONF_WH

BANK_TAB_COUNT = 6  # tabs 0-5 are regular banks (used by init_bank_tab)
TRIALS         = 5  # attempts per step before giving up on validating it
TAB_SCAN_WAIT  = 2.0  # seconds to wait after switching warehouse tab before scanning it for arrows
PARK_OFF       = (60, 17)  # from the warehouse panel's top-left: empty title bar, left of "Warehouse"

_tmpl_inv = screen.tmpl_inv
_tmpl_wh  = screen.tmpl_wh
_tmpl_icon = cv2.imread(ARROW_ICON_PATH, cv2.IMREAD_GRAYSCALE)


_find = screen.find


def _icons_in(gray, x0, y0, w, h):
    """Screen centers of every arrow pack icon inside the box (x0, y0, w, h), top-left first."""
    x0, y0 = max(x0, 0), max(y0, 0)
    area = gray[y0:y0 + h, x0:x0 + w]
    th, tw = _tmpl_icon.shape[:2]
    if area.shape[0] < th or area.shape[1] < tw:
        return []
    res = cv2.matchTemplate(area, _tmpl_icon, cv2.TM_CCOEFF_NORMED)
    hits = []
    while True:
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        if score < CONF_ICON:
            break
        hits.append((x0 + x + tw // 2, y0 + y + th // 2))
        res[max(0, y - 15):y + 15, max(0, x - 15):x + 15] = -1   # don't count this icon again
    return sorted(hits, key=lambda p: (p[1] // 20, p[0]))


def _wh_arrows(gray, wh_panel):
    """Arrow packs (any amount) in the warehouse grid that's showing."""
    return _icons_in(gray, wh_panel[0] + WH_OFFSET_X - 5, wh_panel[1] + WH_OFFSET_Y - 5,
                     WH_COLS * WH_SLOT_W + 25, WH_ROWS * WH_SLOT_H + 25)


def _inv_arrows(gray=None):
    """Arrow packs (any amount) in the inventory; [] if it isn't visible."""
    gray = _grab_gray() if gray is None else gray
    inv_panel = _find(gray, _tmpl_inv, threshold=CONF_INV)
    if not inv_panel:
        return []
    return _icons_in(gray, inv_panel[0] + INV_OFFSET_X - 5, inv_panel[1] + INV_OFFSET_Y - 5,
                     INV_COLS * INV_SLOT_W + 25, INV_ROWS * INV_SLOT_H + 25)


def count_arrows():
    """How many arrow packs the inventory holds. None if the inventory isn't visible."""
    gray = _grab_gray()
    if not _find(gray, _tmpl_inv, threshold=CONF_INV):
        print('[ARROWS] Inventory not visible.')
        return None
    count = len(_inv_arrows(gray))
    print(f'[ARROWS] {count} arrow pack(s) in inventory')
    return count


def _click_at(x, y):
    gi.move(x, y)
    time.sleep(0.05)
    gi.down()
    time.sleep(0.08)
    gi.up()


def _click_warehouse_tab(tab_idx):
    gray = _grab_gray()
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


_grab_gray = screen.grab_gray  # primary monitor only (see screen.py)


def _park_mouse():
    """Moves the cursor onto the warehouse's empty title bar — off every tab and cell — so no
    hover tooltip (e.g. "Click to open the warehouse in ...") covers the grid while it's scanned."""
    wh_panel = _find(_grab_gray(), _tmpl_wh, threshold=CONF_WH)
    if wh_panel:
        gi.move(wh_panel[0] + PARK_OFF[0], wh_panel[1] + PARK_OFF[1])


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
    """Center of the first arrow pack (any amount) in the warehouse grid, or None."""
    packs = _wh_arrows(gray, wh_panel)
    return packs[0] if packs else None


def _search_tabs_for_arrow():
    """Cycles through every warehouse tab and returns the center of the first arrow found
    in the grid (leaving that tab open), or None if no tab has one."""
    for tab in range(WH_TAB_COUNT):
        if not _click_warehouse_tab(tab):
            return None
        _park_mouse()              # the tab's tooltip would cover the first cells
        time.sleep(TAB_SCAN_WAIT)  # let the tab's grid finish loading before scanning it
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


def init_bank_tab(tab_idx=None):
    """Clicks one of the 6 regular bank tabs (0-5) — used once per account on the first
    loop to initialize the warehouse view, in case it was left on the arrows tab (tab 7)
    from a previous run."""
    if tab_idx is None:
        tab_idx = random.randrange(BANK_TAB_COUNT)
    return _click_warehouse_tab(tab_idx)


def ensure_arrows():
    """Keeps arrows equipped. How many are left in the equipped pack is read from the game's
    memory (game_memory); below ARROWS_MIN (General settings) it takes a pack - any amount -
    from the bank, going through the tabs one by one, and equips it:
    1. Validate that the warehouse and inventory panels are both visible.
    2. Cycle through the warehouse tabs; click the first arrow pack found in the grid.
       -> validated by a NEW pack appearing in the inventory (one that wasn't there before).
    3. Right-click that new pack to equip it -> validated by the memory count going back up.
    If the memory can't be read, falls back to the old rule: only when the inventory holds no
    arrows at all. Each step is retried up to TRIALS times; stops if one never validates."""
    need = int(get_settings('static', {'ARROWS_MIN': ARROWS_MIN})['ARROWS_MIN'])
    equipped = game_memory.equipped_arrows()
    if equipped is not None and equipped >= need:
        print(f'[ARROWS] {equipped} arrow(s) equipped')
        return True

    print('[ARROWS] panels...')
    if not _run_step(lambda: None, _panels_visible, 'warehouse_title and inventory_title visible'):
        print('[ARROWS] Aborted — panels not visible.')
        return False

    if equipped is None:
        print('[ARROWS] could not read the equipped arrows from the game — checking the inventory instead')
        count = count_arrows()
        if count is None or count > 0:
            return True
    else:
        print(f'[ARROWS] only {equipped} arrow(s) equipped (< {need}) — taking a pack from the bank')

    before = _inv_arrows()   # packs already in the bag (e.g. an almost empty one): not the new one

    def _new_packs():
        return [p for p in _inv_arrows()
                if all(abs(p[0] - q[0]) > 10 or abs(p[1] - q[1]) > 10 for q in before)]

    pos = _search_tabs_for_arrow()
    if not pos:
        return False

    def _click_found_arrow():
        _click_at(*pos)
        print(f'  [CLICK] arrow in warehouse @ {pos}')
        _park_mouse()              # don't leave a cell's tooltip over the panels while checking

    if not _run_step(_click_found_arrow, lambda: bool(_new_packs()), 'the arrow pack reached the inventory'):
        print('[ARROWS] Aborted — arrow never reached the inventory.')
        return False

    x, y = _new_packs()[0]
    gi.move(x, y)
    time.sleep(0.05)
    gi.click_here('right')
    print(f'  [CLICK] right-clicked the new arrow pack in inventory @ ({x},{y})')
    _park_mouse()
    if equipped is not None:
        for _ in range(6):
            time.sleep(0.5)
            now = game_memory.equipped_arrows()
            if now is not None and now >= need:
                print(f'  [OK] {now} arrow(s) equipped now')
                return True
        print(f'  [FAIL] still {game_memory.equipped_arrows()} arrow(s) equipped after equipping the new pack')
        return False
    return True


if __name__ == '__main__':
    ensure_arrows()
