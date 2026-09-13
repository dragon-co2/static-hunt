import os
import time
import ctypes
import pyautogui

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
FLY_PATH = os.path.join(_DIR, 'fly.jpg')

CONFIDENCE = 0.9


def _locate(path, confidence=CONFIDENCE):
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


def handle_fly():
    """If fly.jpg is on screen, clicks it. Returns True if clicked, False if not found."""
    loc = _locate(FLY_PATH)
    if not loc:
        return False

    x, y = pyautogui.center(loc)
    _click_at(x, y)
    print(f'  [FLY] Clicked @ ({x},{y})')
    return True


if __name__ == '__main__':
    print('Watching for fly.jpg... (Ctrl+C to stop)')
    while True:
        handle_fly()
        time.sleep(1.0)
