import os
import time
import ctypes
import pyautogui

from _paths import app_root
from game_window import focus_game_window

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
REVIVE_MAX_CLICKS = 30  # give up after this many clicks (the loop moves on to the next desktop)


def _locate(path, confidence=0.9):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except pyautogui.ImageNotFoundException:
        return None


def _click_at(x, y):
    """Focuses the game first (it ignores the mouse while not the active window), moves there,
    reports if the cursor didn't actually arrive, then clicks with a 0.1s hold like repair."""
    if not focus_game_window():
        print('  [REVIVE] could not bring the game window to the front')
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    cx, cy = pyautogui.position()
    if abs(cx - x) > 3 or abs(cy - y) > 3:
        print(f'  [REVIVE] mouse is at ({cx},{cy}), not ({x},{y}) — move was blocked')
    pyautogui.mouseDown()
    time.sleep(0.1)
    pyautogui.mouseUp()


def handle_revive():
    """If revive.jpg is on screen, hovers over it and waits for the respawn timer (it's visible
    but not clickable right after death), then clicks it every REVIVE_RETRY seconds until it's
    gone — up to REVIVE_MAX_CLICKS times.
    Returns True if the character is alive afterwards (not dead, or revived), False if the
    button was still there after REVIVE_MAX_CLICKS clicks."""
    loc = _locate(REVIVE_PATH)
    if not loc:
        return True

    x, y = pyautogui.center(loc)
    print(f'  [REVIVE] Died — hovering at ({x},{y}), waiting {REVIVE_WAIT}s...')
    focus_game_window()
    pyautogui.moveTo(x, y, duration=0.1)
    time.sleep(REVIVE_WAIT)

    for clicks in range(1, REVIVE_MAX_CLICKS + 1):
        loc = _locate(REVIVE_PATH)
        if not loc:
            print(f'  [REVIVE] Revive button gone — done after {clicks - 1} click(s).')
            return True
        x, y = pyautogui.center(loc)
        _click_at(x, y)
        print(f'  [REVIVE] Clicked @ ({x},{y})  ({clicks}/{REVIVE_MAX_CLICKS})')
        time.sleep(REVIVE_RETRY)

    if not _locate(REVIVE_PATH):
        print(f'  [REVIVE] Revive button gone — done after {REVIVE_MAX_CLICKS} click(s).')
        return True
    print(f'  [REVIVE] Still dead after {REVIVE_MAX_CLICKS} clicks — giving up for now.')
    return False


if __name__ == '__main__':
    print('Watching for revive.jpg... (Ctrl+C to stop)')
    while True:
        handle_revive()
        time.sleep(1.0)
