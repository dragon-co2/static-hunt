import os
import time
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
VIP_BTN_PATH         = os.path.join(_DIR, 'vip_btn.png')
VIP_MENU_PATH        = os.path.join(_DIR, 'vip_menu.jpg')
COMPOSE_PATH         = os.path.join(_DIR, 'compose.jpg')
DEPOSIT_PATH         = os.path.join(_DIR, 'deposit.jpg')

from dragon_settings import get_settings
_S = get_settings('new_bank_compose', {
    'CONF_VIP_BTN': 0.5, 'CONF_COMPOSE': 0.8, 'CONF_DEPOSIT': 0.8,
    'WAIT': 0.4,
})

CONF_VIP_BTN   = _S['CONF_VIP_BTN']
CONF_COMPOSE   = _S['CONF_COMPOSE']
CONF_DEPOSIT   = _S['CONF_DEPOSIT']

WAIT = _S['WAIT']  # seconds between steps
TRIALS = 5         # attempts per step before giving up on validating it


def _focus_game_window():
    return True


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
    """1. Open the VIP menu  -> validated by vip_menu.jpg appearing.
       2. Open Compose tab   -> validated by deposit.jpg appearing.
    Skips a step whose result is already on screen (e.g. VIP menu left open from last run),
    and stops if a step never validates after TRIALS attempts."""
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
        deposit_click()
    os._exit(0)
