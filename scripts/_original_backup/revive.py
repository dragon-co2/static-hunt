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
REVIVE_PATH = os.path.join(_DIR, 'revive.jpg')

REVIVE_WAIT  = 1    # seconds to wait after death before the revive button becomes clickable
REVIVE_RETRY = 1.0   # seconds between re-checks once the wait is over


def _locate(path, confidence=0.9):
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


def handle_revive():
    """If revive.jpg is on screen, hovers over it and waits for the respawn timer before
    clicking — it's visible but not actually clickable right after death.
    Returns True if a revive was handled, False if there was nothing to do."""
    loc = _locate(REVIVE_PATH)
    if not loc:
        return False

    x, y = pyautogui.center(loc)
    print(f'  [REVIVE] Died — hovering at ({x},{y}), waiting {REVIVE_WAIT}s...')
    pyautogui.moveTo(x, y, duration=0.1)
    time.sleep(REVIVE_WAIT)

    print('  [REVIVE] Wait done — checking for revive button...')
    while True:
        loc = _locate(REVIVE_PATH)
        if loc:
            x, y = pyautogui.center(loc)
            _click_at(x, y)
            print(f'  [REVIVE] Clicked @ ({x},{y})')
            break
        print('  [REVIVE] Not found yet — checking again...')
        time.sleep(REVIVE_RETRY)

    print('  [REVIVE] Done.')
    return True


if __name__ == '__main__':
    print('Watching for revive.jpg... (Ctrl+C to stop)')
    while True:
        handle_revive()
        time.sleep(1.0)
