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

pyautogui.FAILSAFE = False  # moving the mouse to a screen corner does NOT stop the script
pyautogui.PAUSE    = 0

_DIR = os.path.join(app_root(), 'reference_images')
REVIVE_PATH = os.path.join(_DIR, 'revive.jpg')


from dragon_settings import get_settings
_S = get_settings('revive', {'REVIVE_DELAY': 360})

REVIVE_DELAY = _S['REVIVE_DELAY']  # per desktop: seconds after death is first seen before Revive is clicked
REVIVE_CHECK = 3                   # seconds to watch for the button to disappear after a click


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


def is_dead():
    """True if the revive button is on screen."""
    return _locate(REVIVE_PATH) is not None


def handle_revive():
    """Clicks Revive once (bringing the game to the front first) and watches up to REVIVE_CHECK
    seconds for the button to disappear. Returns True if the character is alive afterwards (not
    dead, or revived), False if the button is still there — the caller retries on its next visit.
    The wait before reviving is REVIVE_DELAY, handled per desktop by static.py."""
    loc = _locate(REVIVE_PATH)
    if not loc:
        return True
    x, y = pyautogui.center(loc)
    print(f'  [REVIVE] clicking Revive @ ({x},{y})')
    _click_at(x, y)
    for _ in range(REVIVE_CHECK * 2):
        time.sleep(0.5)
        if not _locate(REVIVE_PATH):
            print('  [REVIVE] revived')
            return True
    print('  [REVIVE] button still there — will try again next visit')
    return False


if __name__ == '__main__':
    print('Watching for revive.jpg... (Ctrl+C to stop)')
    while True:
        handle_revive()
        time.sleep(1.0)
