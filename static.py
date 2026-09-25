import os
import time
import ctypes
import logging
import logging.handlers
import threading

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

from new_bank_compose import run_new_bank_compose, deposit_plus_items, click_empty_inventory_cell
from revive import handle_revive
from grab_arrows import ensure_arrows
from repaire import run_repair, open_warehouse
from db_scroll import run_db_scroll
import overlay
from dragon_settings import get_settings


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish
INTERVAL     = 3    # seconds between each switch

LOG_MAX_MB  = 5   # static.log rotates once it reaches this size...
LOG_BACKUPS = 3   # ...keeping this many old files (static.log.1 .. .3) — ~20 MB on disk at most

_S = get_settings('static', {
    'FIRST_RUN_REPAIR': True,
    'ARROWS_INTERVAL':  1000,
    'REPAIR_INTERVAL':  2000,
})

FIRST_RUN_REPAIR = bool(_S['FIRST_RUN_REPAIR'])  # run repair during each desktop's first-visit setup
ARROWS_INTERVAL  = _S['ARROWS_INTERVAL']         # seconds between grab_arrows passes over all desktops
REPAIR_INTERVAL  = _S['REPAIR_INTERVAL']         # seconds between repair passes over all desktops
TIMER_STATUS_INTERVAL = 5   # seconds between "[TIMER] ... left" countdown prints

_timer_start = {}   # 'arrows' / 'repair' -> time.time() the current interval started

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


class _Tee:
    """Mirrors a console stream into the rotating log, timestamping each complete line."""

    def __init__(self, stream, logger):
        self._stream = stream
        self._logger = logger
        self._buf = ''
        self._lock = threading.Lock()

    def write(self, text):
        self._stream.write(text)
        with self._lock:
            self._buf += text
            *lines, self._buf = self._buf.split('\n')
            for line in lines:
                if line.strip():
                    self._logger.info(line)
                    overlay.push(line)
        return len(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _setup_log():
    """Copies everything printed (stdout + stderr, including crashes) into logs/static.log,
    rotating it at LOG_MAX_MB so it never grows without bound, and mirrors each line to the
    on-screen overlay."""
    base = _os.path.dirname(_sys.executable if getattr(_sys, 'frozen', False) else _os.path.abspath(__file__))
    log_dir = _os.path.join(base, 'logs')
    _os.makedirs(log_dir, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        _os.path.join(log_dir, 'static.log'), maxBytes=LOG_MAX_MB * 1024 * 1024,
        backupCount=LOG_BACKUPS, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger = logging.getLogger('static')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    _sys.stdout = _Tee(_sys.stdout, logger)
    _sys.stderr = _Tee(_sys.stderr, logger)
    logger.info('=' * 20 + ' static.py started ' + '=' * 20)


def _compose():
    """VIP -> Compose tab, then Deposit while +N items are in the inventory, then click an
    empty inventory cell. Skips the deposit/empty-cell clicks if the Compose tab never opened."""
    if not run_new_bank_compose():
        return
    deposit_plus_items()
    click_empty_inventory_cell()


def _timer_status_loop():
    """Every TIMER_STATUS_INTERVAL seconds, prints how long until the next grab_arrows
    and repair passes are queued."""
    while True:
        time.sleep(TIMER_STATUS_INTERVAL)
        now = time.time()
        arrows_left = max(0, int(ARROWS_INTERVAL - (now - _timer_start['arrows'])))
        repair_left = max(0, int(REPAIR_INTERVAL - (now - _timer_start['repair'])))
        print(f'[TIMER] grab_arrows in {arrows_left}s | repair in {repair_left}s')


def _first_run(idx):
    """Full setup, once per desktop on the first visit."""
    print(f'  [INIT] First visit to desktop {idx + 1} — full setup')
    handle_revive()
    if FIRST_RUN_REPAIR:
        run_repair()
    else:
        print('  [INIT] first-run repair turned off in settings — skipping')
    open_warehouse()
    _compose()
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
    _timer_start['arrows'] = _timer_start['repair'] = time.time()
    threading.Thread(target=_timer_status_loop, daemon=True, name='timer-status').start()

    while True:
        now = time.time()
        if now - _timer_start['arrows'] >= ARROWS_INTERVAL:
            print(f'[TIMER] {ARROWS_INTERVAL}s — grab_arrows queued for all desktops')
            pending_arrows = set(range(total))
            _timer_start['arrows'] = now
        if now - _timer_start['repair'] >= REPAIR_INTERVAL:
            print(f'[TIMER] {REPAIR_INTERVAL}s — repair queued for all desktops')
            pending_repair = set(range(total))
            _timer_start['repair'] = now

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
            _compose()
            if current in pending_arrows:
                ensure_arrows()
                pending_arrows.discard(current)

        run_db_scroll()   # every loop, on every desktop (no-op if disabled or < MIN_COUNT)

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
    overlay.start()
    _setup_log()
    try:
        _main()
    except Exception:
        import traceback
        traceback.print_exc()
        if getattr(_sys, 'frozen', False):
            input('\nCrashed - press Enter to close...')
        raise
