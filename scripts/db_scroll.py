"""Dragonball -> scroll: when the inventory holds MIN_COUNT or more dragonballs, right-clicks one
(the game turns 10 of them into a dragonball scroll), clicks a random regular warehouse tab,
then Alt+left-clicks the new scroll.
Turned on/off with ENABLED under 'db_scroll' in settings_schema.json."""
import os
import random
import time

import cv2
import pyautogui

from _paths import app_root
from dragon_settings import get_settings
import grab_arrows as _ga  # inventory panel detection + grid layout (INV_* settings)

_DIR = os.path.join(app_root(), 'reference_images')
DRAGONBALL_PATH = os.path.join(_DIR, 'dragonball.png')
SCROLL_PATH     = os.path.join(_DIR, 'dragonball_scroll.jpg')

_DEFAULTS = {
    'ENABLED':     True,
    'MIN_COUNT':   10,
    'CONF_DB':     0.85,
    'CONF_SCROLL': 0.8,
}

TRIALS = 5  # attempts per step before giving up on validating it

_tmpl_db     = cv2.imread(DRAGONBALL_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_scroll = cv2.imread(SCROLL_PATH, cv2.IMREAD_GRAYSCALE)


def _inventory_cells(template, confidence):
    """Centers of every inventory cell matching `template`, in grid order ([] if the
    inventory isn't visible)."""
    if template is None:
        return []
    gray = _ga._grab_gray()
    inv_panel = _ga._find(gray, _ga._tmpl_inv, threshold=_ga.CONF_INV)
    if not inv_panel:
        return []
    ox, oy = inv_panel[0] + _ga.INV_OFFSET_X, inv_panel[1] + _ga.INV_OFFSET_Y
    th, tw = template.shape[:2]
    hits = []
    for r in range(_ga.INV_ROWS):
        for c in range(_ga.INV_COLS):
            x1, y1 = ox + c * _ga.INV_SLOT_W, oy + r * _ga.INV_SLOT_H
            crop = gray[y1:y1 + _ga.INV_SLOT_H, x1:x1 + _ga.INV_SLOT_W]
            if crop.shape[0] < th or crop.shape[1] < tw:
                continue
            if cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1] >= confidence:
                hits.append((x1 + _ga.INV_SLOT_W // 2, y1 + _ga.INV_SLOT_H // 2))
    return hits


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


def run_db_scroll():
    """If enabled: converts dragonballs to a scroll when there are at least MIN_COUNT, then —
    whether it just made one or one was already sitting in the inventory — clicks a random
    regular warehouse tab and Alt+clicks the scroll.
    Settings are re-read on every call, so the toggle takes effect without a restart."""
    s = get_settings('db_scroll', _DEFAULTS)
    if not s['ENABLED']:
        return False

    def _scroll_count():
        return len(_inventory_cells(_tmpl_scroll, s['CONF_SCROLL']))

    balls = _inventory_cells(_tmpl_db, s['CONF_DB'])
    scrolls_before = _scroll_count()
    if len(balls) >= s['MIN_COUNT']:
        print(f'[DB] {len(balls)} dragonball(s) in inventory (>= {s["MIN_COUNT"]}) — converting to scroll')
    elif scrolls_before:
        print(f'[DB] {scrolls_before} dragonball scroll(s) already in inventory — stashing')
        return _stash_scrolls(s)
    else:
        return False

    def _right_click_ball():
        cells = _inventory_cells(_tmpl_db, s['CONF_DB'])
        if not cells:
            return
        x, y = cells[0]
        pyautogui.moveTo(x, y, duration=0.1)
        time.sleep(0.05)
        pyautogui.rightClick()
        print(f'  [CLICK] right-clicked dragonball @ ({x},{y})')

    # validated by the scroll count going up, so a leftover scroll can't fake success
    if not _run_step(_right_click_ball, lambda: _scroll_count() > scrolls_before,
                     'dragonball_scroll.jpg appeared'):
        print('[DB] Aborted — scroll never appeared.')
        return False
    return _stash_scrolls(s)


def _stash_scrolls(s):
    """Clicks a random regular warehouse tab, then Alt+clicks every dragonball scroll in the
    inventory until none are left."""
    tab = random.randrange(_ga.BANK_TAB_COUNT)  # regular bank tabs only — never the arrows tab
    if not _ga._click_warehouse_tab(tab):
        print('[DB] Aborted — warehouse not visible, cannot pick a tab for the scroll.')
        return False
    time.sleep(0.4)

    def _alt_click_scrolls():
        for x, y in _inventory_cells(_tmpl_scroll, s['CONF_SCROLL']):
            pyautogui.moveTo(x, y, duration=0.1)
            pyautogui.keyDown('alt')
            try:
                time.sleep(0.1)
                pyautogui.mouseDown()
                time.sleep(0.08)
                pyautogui.mouseUp()
                time.sleep(0.1)
            finally:
                pyautogui.keyUp('alt')
            print(f'  [CLICK] Alt+clicked dragonball scroll @ ({x},{y})')
            time.sleep(0.3)

    if not _run_step(_alt_click_scrolls, lambda: not _inventory_cells(_tmpl_scroll, s['CONF_SCROLL']),
                     'dragonball_scroll.jpg left the inventory'):
        print('[DB] Aborted — scroll still in inventory after Alt+click.')
        return False
    return True


if __name__ == '__main__':
    run_db_scroll()
    os._exit(0)
