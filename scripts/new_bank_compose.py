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
EMPTYCELL_PATH       = os.path.join(_DIR, 'emptycell.jpg')
ARROW_PATH           = os.path.join(_DIR, 'arrow.jpg')
VIP_BTN_PATH         = os.path.join(_DIR, 'vip_btn.png')
VIP_MENU_PATH        = os.path.join(_DIR, 'vip_menu.jpg')
COMPOSE_PATH         = os.path.join(_DIR, 'compose.jpg')
DEPOSIT_PATH         = os.path.join(_DIR, 'deposit.jpg')
CLOSE_VIP_PATH       = os.path.join(_DIR, 'close_vip.jpg')

from dragon_settings import get_settings
_S = get_settings('new_bank_compose', {
    'WH_OFFSET_X': 128, 'WH_OFFSET_Y': 46, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,
    'WH_COLS': 5, 'WH_ROWS': 4,
    'CONF_WH': 0.5, 'CONF_EMPTY': 0.8, 'CONF_ARROW': 0.5,
    'CONF_VIP_BTN': 0.5, 'CONF_COMPOSE': 0.8, 'CONF_DEPOSIT': 0.8, 'CONF_CLOSE_VIP': 0.8,
    'CLICK_MULTIPLIER': 2, 'WAREHOUSE_MIN_ITEMS': 10,
    'WAIT': 0.4,
})

WH_OFFSET_X = _S['WH_OFFSET_X']
WH_OFFSET_Y = _S['WH_OFFSET_Y']
WH_SLOT_W   = _S['WH_SLOT_W']
WH_SLOT_H   = _S['WH_SLOT_H']
WH_COLS     = _S['WH_COLS']
WH_ROWS     = _S['WH_ROWS']

CONF_WH    = _S['CONF_WH']
CONF_EMPTY = _S['CONF_EMPTY']
CONF_ARROW = _S['CONF_ARROW']
CONF_VIP_BTN   = _S['CONF_VIP_BTN']
CONF_COMPOSE   = _S['CONF_COMPOSE']
CONF_DEPOSIT   = _S['CONF_DEPOSIT']
CONF_CLOSE_VIP = _S['CONF_CLOSE_VIP']
CLICK_MULTIPLIER = _S['CLICK_MULTIPLIER']  # extra clicks over the detected item count, e.g. 1.5 = +50%
WAREHOUSE_MIN_ITEMS = _S['WAREHOUSE_MIN_ITEMS']  # only withdraw + run the vip flow if the warehouse has at least this many items

WAIT = _S['WAIT']  # seconds between steps

_tmpl_wh    = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_empty = cv2.imread(EMPTYCELL_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_arrow = cv2.imread(ARROW_PATH, cv2.IMREAD_GRAYSCALE)


def _focus_game_window():
    return True


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
    if _tmpl_arrow is None:
        return False
    th, tw = _tmpl_arrow.shape[:2]
    if cell_gray.shape[0] < th or cell_gray.shape[1] < tw:
        return False
    res = cv2.matchTemplate(cell_gray, _tmpl_arrow, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(res)[1] >= CONF_ARROW


def _count_warehouse_items(gray, wh_panel):
    """Counts non-empty cells in the warehouse grid, and separately the arrow cells among them.
    Arrow cells are ignored entirely — not counted as items, and not touched by the withdraw step."""
    px, py, _, _ = wh_panel
    ox, oy = px + WH_OFFSET_X, py + WH_OFFSET_Y
    count = 0
    arrow_count = 0
    for r in range(WH_ROWS):
        for c in range(WH_COLS):
            x1 = ox + c * WH_SLOT_W
            y1 = oy + r * WH_SLOT_H
            crop = gray[y1:y1 + WH_SLOT_H, x1:x1 + WH_SLOT_W]
            if _tmpl_empty is not None and crop.shape[0] >= _tmpl_empty.shape[0] and crop.shape[1] >= _tmpl_empty.shape[1]:
                res = cv2.matchTemplate(crop, _tmpl_empty, cv2.TM_CCOEFF_NORMED)
                if cv2.minMaxLoc(res)[1] >= CONF_EMPTY:
                    continue
            if _is_arrow(crop):
                arrow_count += 1
                continue
            count += 1
    return count, arrow_count


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


def _warehouse_state():
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[0])), cv2.COLOR_BGRA2GRAY)
    wh_panel = _find(gray, _tmpl_wh, threshold=CONF_WH)
    item_count, arrow_count = _count_warehouse_items(gray, wh_panel) if wh_panel else (0, 0)
    return wh_panel, item_count, arrow_count


def _withdraw_items(wh_panel, item_count):
    """Clicks warehouse cells to pull items into the bag — same CLICK_MULTIPLIER logic as bank_compose.py.
    Before each click it checks the target cell for arrow.jpg and skips it if found — arrows are
    never withdrawn, they're just left in place."""
    times = round(item_count * CLICK_MULTIPLIER)
    print(f'  [WITHDRAW] {item_count} item(s) in warehouse — clicking {times} times (x{CLICK_MULTIPLIER})')
    if times == 0:
        return True

    px, py, _, _ = wh_panel
    _focus_game_window()
    clicked = 0
    attempts = 0
    max_attempts = times * 4  # bail out if a cell keeps landing on an arrow
    while clicked < times and attempts < max_attempts:
        attempts += 1
        r, c = random.choice([(0, 0), (0, 1)])
        x1 = px + WH_OFFSET_X + c * WH_SLOT_W
        y1 = py + WH_OFFSET_Y + r * WH_SLOT_H
        x, y = x1 + WH_SLOT_W // 2, y1 + WH_SLOT_H // 2

        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[0]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        crop = gray[y1:y1 + WH_SLOT_H, x1:x1 + WH_SLOT_W]
        if _is_arrow(crop):
            print(f'  [WITHDRAW] arrow at cell(r{r},c{c}) — skipping')
            continue

        pyautogui.moveTo(x, y, duration=0.15)
        time.sleep(0.1)
        pyautogui.mouseDown()
        time.sleep(0.08)
        pyautogui.mouseUp()
        clicked += 1
        print(f'  [WITHDRAW] cell(r{r},c{c}) @ ({x},{y})  {clicked}/{times}')
    return True


def run_new_bank_compose(min_items=WAREHOUSE_MIN_ITEMS):
    wh_panel, item_count, arrow_count = _warehouse_state()
    print(f'  [CHECK] {item_count} item(s) in warehouse ({arrow_count} arrow(s) ignored)')
    if wh_panel and item_count == 0 and arrow_count > 0:
        print('  [CHECK] Only arrows in warehouse — skipping.')
        return
    if not wh_panel or item_count < min_items:
        print(f'  [CHECK] Fewer than {min_items} — skipping.')
        return

    _withdraw_items(wh_panel, item_count)
    time.sleep(WAIT)

    if not _click_verify(VIP_BTN_PATH, lambda: _is_visible(VIP_MENU_PATH), confidence=CONF_VIP_BTN):
        print('[SEQ] VIP menu never opened.')
        return
    time.sleep(WAIT)


    _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE)
    time.sleep(0.1)
    _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE)
    time.sleep(0.1)
    _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE)
    time.sleep(0.1)
    _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE)
    time.sleep(0.1)
    _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE)
    time.sleep(0.3)


    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1) 
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.1)
    _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT)
    time.sleep(0.3)

    if not _click_verify(CLOSE_VIP_PATH, lambda: not _is_visible(VIP_MENU_PATH), confidence=CONF_CLOSE_VIP):
        print('[SEQ] VIP menu never closed.')
    time.sleep(0.5)




if __name__ == '__main__':
    run_new_bank_compose()
    os._exit(0)
