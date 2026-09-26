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
REVIVE_RETRY = 1.0  # seconds between re-checks once the wait is over


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
    """If revive.jpg is on screen, hovers over it and waits for the respawn timer (it's visible
    but not clickable right after death), then keeps clicking it every REVIVE_RETRY seconds
    until it's gone. Returns True if a revive was handled, False if there was nothing to do."""
    loc = _locate(REVIVE_PATH)
    if not loc:
        return False

    x, y = pyautogui.center(loc)
    print(f'  [REVIVE] Died — hovering at ({x},{y}), waiting {REVIVE_WAIT}s...')
    pyautogui.moveTo(x, y, duration=0.1)
    time.sleep(REVIVE_WAIT)

    clicks = 0
    while True:
        loc = _locate(REVIVE_PATH)
        if not loc:
            break
        x, y = pyautogui.center(loc)
        _click_at(x, y)
        clicks += 1
        print(f'  [REVIVE] Clicked @ ({x},{y})  (#{clicks})')
        time.sleep(REVIVE_RETRY)

    print(f'  [REVIVE] Revive button gone — done after {clicks} click(s).')
    return True


if __name__ == '__main__':
    print('Watching for revive.jpg... (Ctrl+C to stop)')
    while True:
        handle_revive()
        time.sleep(1.0)
