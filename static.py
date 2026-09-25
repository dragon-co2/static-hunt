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
from pyvda import VirtualDesktop, get_virtual_desktops

from new_bank_compose import run_new_bank_compose
from revive import handle_revive
from grab_arrows import ensure_arrows
from repaire import run_repair, open_warehouse


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish
INTERVAL     = 3    # seconds between each switch

ARROWS_INTERVAL = 100   # seconds between grab_arrows passes over all desktops
REPAIR_INTERVAL = 200   # seconds between repair passes over all desktops

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


def _first_run(idx):
    """Full setup, once per desktop on the first visit."""
    print(f'  [INIT] First visit to desktop {idx + 1} — full setup')
    handle_revive()
    run_repair()
    open_warehouse()
    run_new_bank_compose()
    ensure_arrows()


def _main():
    total = len(get_virtual_desktops())         # accounts/desktops to ping-pong between
    current = VirtualDesktop.current().number - 1  # 0-based; start from wherever we are now
    print(f'[DESKTOP] {total} desktop(s) found, starting on {current + 1}')

    print('Starting in 3s...')
    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)

    direction = 1 if current < total - 1 else -1
    initialized = set()          # desktops that already had their first-run setup
    pending_arrows = set()       # desktops still owed a grab_arrows run in the current pass
    pending_repair = set()       # desktops still owed a repair run in the current pass
    last_arrows = last_repair = time.time()

    while True:
        now = time.time()
        if now - last_arrows >= ARROWS_INTERVAL:
            print(f'[TIMER] {ARROWS_INTERVAL}s — grab_arrows queued for all desktops')
            pending_arrows = set(range(total))
            last_arrows = now
        if now - last_repair >= REPAIR_INTERVAL:
            print(f'[TIMER] {REPAIR_INTERVAL}s — repair queued for all desktops')
            pending_repair = set(range(total))
            last_repair = now

        print(f'[DESKTOP] Processing {current + 1}/{total}')
        if current not in initialized:
            _first_run(current)
            initialized.add(current)
            pending_arrows.discard(current)   # just did both as part of the setup
            pending_repair.discard(current)
        else:
            handle_revive()
            if current in pending_repair:
                run_repair()
                pending_repair.discard(current)
            run_new_bank_compose()
            if current in pending_arrows:
                ensure_arrows()
                pending_arrows.discard(current)

        time.sleep(INTERVAL)

        if total < 2:
            continue
        next_idx = current + direction
        if not (0 <= next_idx < total):
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
