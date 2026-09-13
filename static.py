import os
import time
import ctypes

import sys as _sys, os as _os
if getattr(_sys, 'frozen', False):
    _sys.path.insert(0, _sys._MEIPASS)
else:
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scripts'))

# pyvda pulls in comtypes, which by default caches generated COM wrapper code
# on disk next to the comtypes package. Inside a frozen exe that folder is
# read-only/extracted-per-run, which makes comtypes throw on startup. Telling
# it to keep the generated code in memory instead avoids that.
import comtypes.client
comtypes.client.gen_dir = None

import pyautogui
from pynput import keyboard as pynput_kb
from pyvda import get_virtual_desktops

from stash2 import stash_items
from bank_compose import run_compose
from ingame_autohunt import ingame_autohun_off, ingame_autohun_on
from revive import handle_revive
from grab_arrows import ensure_arrows


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

ACCOUNT_NUMBERS = len(get_virtual_desktops())   # number of accounts/desktops to ping-pong between

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish
INTERVAL     = 3    # seconds between each switch

ARROWS_INTERVAL = 1400   # seconds between grab_arrows runs, per account

_last_arrows_check = {}

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


def _main():
    print('Starting in 3s...')

    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)

    current   = 0
    direction = 1
    start_time = time.time()

    while True:
        print(f'[DESKTOP] Processing {current + 1}/{ACCOUNT_NUMBERS}')
        if not handle_revive():
            stash_items()
            handle_revive()

            now = time.time()
            if now - _last_arrows_check.get(current, start_time) >= ARROWS_INTERVAL:
                ensure_arrows()
                _last_arrows_check[current] = now

        time.sleep(INTERVAL)

        next_idx = current + direction
        if not (0 <= next_idx < ACCOUNT_NUMBERS):
            direction *= -1
            next_idx = current + direction

        pyautogui.hotkey('ctrl', 'win', 'right' if direction == 1 else 'left')
        current = next_idx
        time.sleep(SWITCH_DELAY)


if __name__ == '__main__':
    # When frozen, an unhandled exception would otherwise close the console
    # window instantly and the error is never seen - print it and wait.
    try:
        _main()
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(_sys, 'frozen', False):
            input('\nCrashed - press Enter to close...')
        raise
