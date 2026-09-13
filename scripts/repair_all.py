import os
import time
import ctypes
import pyautogui
from pynput import keyboard as pynput_kb

from repaire import run_repair

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

ACCOUNT_NUMBERS = 4   # number of accounts/desktops to ping-pong between

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish
INTERVAL     = 60    # seconds between each switch

_shift_held = False


def _on_key_release(key):
    global _shift_held
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_r):
        _shift_held = False


def _on_key_press(key):
    global _shift_held
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_r):
        _shift_held = True
        return
    try:
        if key.char == '\x03' and _shift_held:   # Ctrl+Shift+C
            print('  [HOTKEY] Ctrl+Shift+C — exiting')
            os._exit(0)
    except AttributeError:
        pass


pynput_kb.Listener(on_press=_on_key_press, on_release=_on_key_release, daemon=True).start()


if __name__ == '__main__':
    current   = 0
    direction = 1

    while True:
        print(f'[DESKTOP] Repairing {current + 1}/{ACCOUNT_NUMBERS}')
        run_repair()

        time.sleep(INTERVAL)

        next_idx = current + direction
        if not (0 <= next_idx < ACCOUNT_NUMBERS):
            direction *= -1
            next_idx = current + direction

        pyautogui.hotkey('ctrl', 'win', 'right' if direction == 1 else 'left')
        current = next_idx
        time.sleep(SWITCH_DELAY)
