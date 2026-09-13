import os
import time
import numpy as np
import cv2
import mss
import pyautogui

from _paths import app_root

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

_DIR = os.path.join(app_root(), 'reference_images')
OPTIONS_BTN_PATH   = os.path.join(_DIR, 'options_btn.png')
OPTIONS_CLOSE_PATH = os.path.join(_DIR, 'options_close.png')
AUTO_HUNT_OFF_PATH = os.path.join(_DIR, 'auto_rightckick_off.png')
AUTO_HUNT_ON_PATH  = os.path.join(_DIR, 'auto_rightckick_on.png')

CONFIDENCE = 0.8


def _best_match_score(path):
    tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tmpl is None:
        print(f'  [WARN] could not load image: {path}')
        return 0.0
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[0]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    if gray.shape[0] < tmpl.shape[0] or gray.shape[1] < tmpl.shape[1]:
        return 0.0
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(res)[1])


def locate(path, confidence=CONFIDENCE):
    name = os.path.basename(path)
    try:
        loc = pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except pyautogui.ImageNotFoundException:
        loc = None
    score = _best_match_score(path)
    if loc:
        print(f'  [FOUND] {name} @ {loc}  (best score={score:.2f})')
    else:
        print(f'  [MISS]  {name}  (best score={score:.2f}, need {confidence})')
    return loc


def click(path, confidence=CONFIDENCE):
    """Clicks directly on the located option image itself (the on/off state graphic),
    not some separate checkbox glyph."""
    loc = locate(path, confidence=confidence)
    if not loc:
        return False
    x, y = pyautogui.center(loc)
    pyautogui.moveTo(x, y, duration=0.15)
    time.sleep(0.1)
    pyautogui.mouseDown()
    time.sleep(0.08)
    pyautogui.mouseUp()
    print(f'  [CLICK] {os.path.basename(path)} @ ({x},{y})')
    return True


def toggle_auto_hunt():
    """Opens options, flips auto hunt to whatever it currently isn't, then closes options."""
    print('[STEP] Opening options...')
    click(OPTIONS_BTN_PATH)
    time.sleep(1)

    print('[STEP] Checking auto hunt state...')
    if locate(AUTO_HUNT_ON_PATH):
        print('  [ACTION] Auto hunt is ON — clicking to turn it off...')
        click(AUTO_HUNT_ON_PATH)
    elif locate(AUTO_HUNT_OFF_PATH):
        print('  [ACTION] Auto hunt is OFF — clicking to turn it on...')
        click(AUTO_HUNT_OFF_PATH)
    else:
        print('  [WARN] Could not find auto hunt in either state — skipping toggle.')

    print('[STEP] Closing options...')
    click(OPTIONS_CLOSE_PATH)


def _set_auto_hunt(want_on):
    label       = 'on' if want_on else 'off'
    target_path = AUTO_HUNT_ON_PATH if want_on else AUTO_HUNT_OFF_PATH
    other_path  = AUTO_HUNT_OFF_PATH if want_on else AUTO_HUNT_ON_PATH

    print('[STEP] Opening options...')
    click(OPTIONS_BTN_PATH)
    time.sleep(1)

    print(f'[STEP] Ensuring auto hunt is {label}...')
    if locate(target_path):
        print(f'  [SKIP] Auto hunt already {label}.')
    elif locate(other_path):
        print(f'  [ACTION] Auto hunt is {"off" if want_on else "on"} — clicking to turn it {label}...')
        click(other_path)
    else:
        print('  [WARN] Could not find auto hunt in either state — skipping.')

    print('[STEP] Closing options...')
    click(OPTIONS_CLOSE_PATH)


def ingame_autohun_on():
    _set_auto_hunt(want_on=True)


def ingame_autohun_off():
    _set_auto_hunt(want_on=False)


if __name__ == '__main__':
    print('Starting in 3s — switch to the game window...')
    time.sleep(3)
    toggle_auto_hunt()
    print('=== Done ===')
