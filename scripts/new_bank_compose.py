import glob
import os
import time
import ctypes
import cv2
import pyautogui

import game_input as gi  # real-device input via the Interception driver

from _paths import app_root
import screen  # shared primary-monitor capture + panel matching
import grab_arrows as _ga  # inventory panel detection + grid layout (INV_* settings)
from repaire import open_warehouse  # Alt+P until the warehouse panel is visible

try:
    if not ctypes.windll.user32.IsProcessDPIAware():
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

pyautogui.FAILSAFE = False  # moving the mouse to a screen corner does NOT stop the script
pyautogui.PAUSE = 0

_DIR = os.path.join(app_root(), 'reference_images')
VIP_BTN_PATH         = os.path.join(_DIR, 'vip_btn.png')
VIP_MENU_PATH        = os.path.join(_DIR, 'vip_menu.jpg')
COMPOSE_PATH         = os.path.join(_DIR, 'compose.jpg')
DEPOSIT_PATH         = os.path.join(_DIR, 'deposit.jpg')


CONF_VIP_BTN = 0.5
CONF_COMPOSE = 0.8
CONF_DEPOSIT = 0.8

WAIT   = 0.4  # seconds between steps
TRIALS = 20   # attempts per step before giving up on validating it
DEPOSIT_PRESSES = 5      # max Deposit presses while +N items remain (stops earlier as soon as they're gone)
PLUS_MAX_DIFF   = 3000   # mean squared color difference on the badge's yellow pixels; real badge ~800, other digits ~14000+



def _load_plus_badges():
    """Every reference_images/plus*.png (+1, +2, ...) with a mask of just its yellow digits,
    so the item art behind a badge in the inventory doesn't spoil the match."""
    badges = []
    for path in sorted(glob.glob(os.path.join(_DIR, 'plus*.png'))):
        tmpl = cv2.imread(path)
        if tmpl is None:
            continue
        mask = cv2.inRange(cv2.cvtColor(tmpl, cv2.COLOR_BGR2HSV), (15, 80, 120), (40, 255, 255))
        if cv2.countNonZero(mask):
            badges.append((os.path.splitext(os.path.basename(path))[0], tmpl, mask))
    return badges


_plus_badges = _load_plus_badges()


def _focus_game_window():
    return True


def _best_match_score(path):
    tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tmpl is None:
        return 0.0
    gray = screen.grab_gray()
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
    gi.move(x, y)
    time.sleep(0.1)
    gi.down()
    time.sleep(0.08)
    gi.up()
    print(f'  [CLICK] {os.path.basename(path)} @ ({x},{y})')
    return True


def _is_visible(path, confidence=0.8):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True) is not None
    except pyautogui.ImageNotFoundException:
        return False


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


def run_new_bank_compose():
    """0. Make sure the warehouse is open -> Alt+P until warehouse_title is visible.
       1. Open the VIP menu  -> validated by vip_menu.jpg appearing.
       2. Open Compose tab   -> validated by deposit.jpg appearing.
    Skips a step whose result is already on screen (e.g. VIP menu left open from last run),
    and stops if a step never validates after TRIALS attempts."""
    print('[SEQ] warehouse...')
    if not open_warehouse():
        print('[SEQ] Aborted at "warehouse" — never opened.')
        return False
    time.sleep(WAIT)

    print('[SEQ] vip...')
    if _is_visible(DEPOSIT_PATH, CONF_DEPOSIT) or _is_visible(VIP_MENU_PATH):
        print('  [OK] VIP menu already open — skipping')
    elif not _run_step(lambda: _click_image(VIP_BTN_PATH, confidence=CONF_VIP_BTN),
                       lambda: _is_visible(VIP_MENU_PATH), 'vip_menu.jpg appeared'):
        print('[SEQ] Aborted at "vip".')
        return False
    time.sleep(WAIT)

    print('[SEQ] compose...')
    if _is_visible(DEPOSIT_PATH, CONF_DEPOSIT):
        print('  [OK] deposit.jpg already visible — skipping')
    elif not _run_step(lambda: _click_image(COMPOSE_PATH, confidence=CONF_COMPOSE),
                       lambda: _is_visible(DEPOSIT_PATH, CONF_DEPOSIT), 'deposit.jpg appeared'):
        print('[SEQ] Aborted at "compose".')
        return False
    time.sleep(WAIT)
    return True


def plus_items_in_inventory():
    """Names of the +N badges (plus1, plus2, ...) visible in the inventory panel, or []."""
    bgr = screen.grab_bgr()
    inv_panel = _ga._find(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), _ga._tmpl_inv, threshold=_ga.CONF_INV)
    if not inv_panel:
        return []
    x, y, w, h = inv_panel
    region = bgr[y:y + h, x:x + w]
    found = []
    for name, tmpl, mask in _plus_badges:
        if region.shape[0] < tmpl.shape[0] or region.shape[1] < tmpl.shape[1]:
            continue
        diff = cv2.minMaxLoc(cv2.matchTemplate(region, tmpl, cv2.TM_SQDIFF, mask=mask))[0]
        if diff / cv2.countNonZero(mask) <= PLUS_MAX_DIFF:
            found.append(name)
    return found


def deposit_plus_items(max_presses=DEPOSIT_PRESSES):
    """Presses Deposit only while a +N item is in the inventory, and keeps pressing until no
    +N badge is left (capped at `max_presses` so a missed detection can't loop forever)."""
    found = plus_items_in_inventory()
    if not found:
        print('  [OK] no +N items in inventory — skipping deposit')
        return True
    print(f'[SEQ] +N item(s) in inventory ({", ".join(found)}) — depositing')
    for press in range(1, max_presses + 1):
        deposit_click()
        time.sleep(0.5)
        found = plus_items_in_inventory()
        if not found:
            print(f'  [OK] all +N items deposited (after {press} press(es))')
            return True
    print(f'  [FAIL] +N item(s) still in inventory after {max_presses} deposit presses ({", ".join(found)})')
    return False


def deposit_click(trials=TRIALS):
    """Presses the deposit button once — no VIP or compose steps. Nothing on screen changes
    after a deposit, so it's validated by the button being found and clicked; retried up to
    `trials` times if it isn't."""
    print('[SEQ] deposit...')
    for trial in range(1, trials + 1):
        if _click_image(DEPOSIT_PATH, confidence=CONF_DEPOSIT):
            print('  [OK] deposit clicked')
            return True
        print(f'  [FAIL] deposit button not found (trial {trial}/{trials})')
        time.sleep(0.5)
    print(f'  [FAIL] deposit — gave up after {trials} trials.')
    return False


if __name__ == '__main__':
    if run_new_bank_compose():
        deposit_plus_items()
    os._exit(0)
