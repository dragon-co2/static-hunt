import os
import time
import ctypes
import pyautogui

from _paths import app_root

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

_DIR = os.path.join(app_root(), 'reference_images')
DEPOSIT_PATH = os.path.join(_DIR, 'deposit.jpg')

from dragon_settings import get_settings
_S = get_settings('deposit_click', {'CONF_DEPOSIT': 0.8})

CONF_DEPOSIT = _S['CONF_DEPOSIT']


def _locate(path, confidence=CONF_DEPOSIT):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except pyautogui.ImageNotFoundException:
        return None


def _click_at(x, y):
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    pyautogui.mouseDown()
    time.sleep(0.08)
    pyautogui.mouseUp()


def handle_deposit():
    """If deposit.jpg is on screen, clicks it. Returns True if a click was made, False otherwise."""
    loc = _locate(DEPOSIT_PATH)
    if not loc:
        return False

    x, y = pyautogui.center(loc)
    _click_at(x, y)
    print(f'  [DEPOSIT] Clicked @ ({x},{y})')
    return True


if __name__ == '__main__':
    print('Watching for deposit.jpg... (Ctrl+C to stop)')
    while True:
        handle_deposit()
        time.sleep(1.0)
