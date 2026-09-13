import os
import re
import ctypes
import pyautogui
import pytesseract
from pynput import keyboard as pynput_kb

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

# Path to tesseract.exe — adjust if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

_DIR = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(_DIR, 'coords.txt')

# Pixel region (left, top, width, height) of the coordinate text in-game — same as navigation_static.py.
COORD_REGION = (110, 0, 177, 17)

_f1_down = False


def get_current_coords():
    img = pyautogui.screenshot(region=COORD_REGION)
    img = img.resize((img.width * 3, img.height * 3))
    text = pytesseract.image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789(),[]| ')
    m = re.search(r'\((\d+),\s*(\d+)\)', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _append_coords():
    coords = get_current_coords()
    if coords is None:
        print('  [STORE] Could not read coords — skipping.')
        return
    x, y = coords
    with open(COORDS_FILE, 'a', encoding='utf-8') as f:
        f.write(f'({x},{y}),')
    print(f'  [STORE] appended ({x},{y}) -> {COORDS_FILE}')


def _on_key_press(key):
    global _f1_down
    if key != pynput_kb.Key.f1:
        return
    if _f1_down:
        return  # ignore OS key-repeat while held
    _f1_down = True
    _append_coords()


def _on_key_release(key):
    global _f1_down
    if key == pynput_kb.Key.f1:
        _f1_down = False


if __name__ == '__main__':
    print('=== Store Coords ===')
    print(f'Press F1 to append the current coords to {COORDS_FILE}')
    listener = pynput_kb.Listener(on_press=_on_key_press, on_release=_on_key_release)
    listener.start()
    listener.join()
