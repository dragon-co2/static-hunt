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

from new_bank_compose import run_new_bank_compose, deposit_plus_items, hover_last_empty_cell
from revive import handle_revive
from grab_arrows import ensure_arrows
from repaire import run_repair, open_warehouse
from db_scroll import run_db_scroll
import overlay
from game_window import find_game_window, focus_game_window
import new_bank_compose as _nbc
import repaire as _rep
import revive as _rev
import json
from dragon_settings import get_settings, override_dir


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = False  # moving the mouse to a screen corner does NOT stop the script
pyautogui.PAUSE    = 0

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish
INTERVAL     = 3    # seconds between each switch

LOG_MAX_MB  = 5   # static.log rotates once it reaches this size...
LOG_BACKUPS = 3   # ...keeping this many old files (static.log.1 .. .3) — ~20 MB on disk at most

_S = get_settings('static', {
    'FIRST_RUN_REPAIR': True,
    'ARROWS_INTERVAL':  1000,
    'REPAIR_INTERVAL':  8000,
})

FIRST_RUN_REPAIR = bool(_S['FIRST_RUN_REPAIR'])  # run repair during each desktop's first-visit setup
ARROWS_INTERVAL  = _S['ARROWS_INTERVAL']         # seconds between grab_arrows passes over all desktops
REPAIR_INTERVAL  = _S['REPAIR_INTERVAL']         # seconds between repair passes over all desktops

_timer_start = {}   # 'arrows' / 'repair' -> time.time() the current interval started
TIMER_STATE_PATH = _os.path.join(override_dir(), 'timer_state.json')  # outside the repo: survives git reset / updates

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
    """VIP -> Compose tab, then Deposit while +N items are in the inventory, then move the mouse
    onto the last empty inventory cell. Skips both if the Compose tab never opened."""
    if not run_new_bank_compose():
        return
    deposit_plus_items()
    hover_last_empty_cell()


def _fmt_left(seconds):
    m, s = divmod(max(0, int(seconds)), 60)
    return f'{m:02d}:{s:02d}'


def _load_timer_state():
    """Restores the countdowns where the previous session left them (closed or crashed):
    the saved seconds-left pick up again now, as if the script had been paused. Missing or
    unreadable state, or a saved value longer than the current interval, starts fresh."""
    try:
        with open(TIMER_STATE_PATH, encoding='utf-8') as f:
            saved = json.load(f)
    except Exception:
        saved = {}
    now = time.time()
    for key, interval in (('arrows', ARROWS_INTERVAL), ('repair', REPAIR_INTERVAL)):
        left = saved.get(key)
        if not isinstance(left, (int, float)) or not 0 <= left <= interval:
            left = interval
        _timer_start[key] = now - (interval - left)
    if saved:
        print(f'[TIMER] resumed from last session — arrows in {_fmt_left(saved.get("arrows", 0))}, '
              f'repair in {_fmt_left(saved.get("repair", 0))}')


def _save_timer_state(arrows_left, repair_left):
    try:
        _os.makedirs(override_dir(), exist_ok=True)
        tmp = TIMER_STATE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'arrows': max(0, round(arrows_left)), 'repair': max(0, round(repair_left))}, f)
        _os.replace(tmp, TIMER_STATE_PATH)  # atomic: a crash mid-write never leaves a broken file
    except Exception:
        pass


def _timer_status_loop():
    """Once a second, shows how long until the next grab_arrows and repair passes are queued
    in the on-screen timers panel (overlay_timers settings), and saves it so the next run can
    resume from here."""
    while True:
        now = time.time()
        arrows_left = ARROWS_INTERVAL - (now - _timer_start['arrows'])
        repair_left = REPAIR_INTERVAL - (now - _timer_start['repair'])
        overlay.set_status(f'Arrows {_fmt_left(arrows_left)}\nRepair {_fmt_left(repair_left)}')
        _save_timer_state(arrows_left, repair_left)
        time.sleep(1)


def _in_game_ui_visible():
    """True if something that only shows while logged in is on screen: the bank panel, the
    VIP menu, the Compose tab's Deposit button, or the revive button (dead but still online)."""
    return (_rep._warehouse_open()
            or _nbc._is_visible(_nbc.VIP_MENU_PATH)
            or _nbc._is_visible(_nbc.DEPOSIT_PATH, _nbc.CONF_DEPOSIT)
            or _rev._locate(_rev.REVIVE_PATH) is not None)


def _account_online():
    """Second check after the game window: the window stays open when the account gets
    disconnected, so also require in-game UI. If none is showing (e.g. a fresh login with every
    panel closed), press Alt+P once and give the bank 3s to appear before calling it offline."""
    if _in_game_ui_visible():
        return True
    _rep._press_alt_p()
    for _ in range(6):
        time.sleep(0.5)
        if _rep._warehouse_open():
            return True
    return False


def _first_run(idx):
    """Full setup, once per desktop on the first visit. Returns False (setup not done — retried
    next visit) if the character couldn't be revived."""
    print(f'  [INIT] First visit to desktop {idx + 1} — full setup')
    if not handle_revive():
        return False
    if FIRST_RUN_REPAIR:
        run_repair()
    else:
        print('  [INIT] first-run repair turned off in settings — skipping')
    open_warehouse()
    _compose()
    ensure_arrows()
    return True


def _go_to_desktop(idx, retries=3):
    """Jumps directly to desktop idx + 1 (no Ctrl+Win+arrow stepping), then confirms that's
    really the current desktop; retries if the switch didn't land."""
    for attempt in range(1, retries + 1):
        VirtualDesktop(idx + 1).go()
        time.sleep(SWITCH_DELAY)
        actual = VirtualDesktop.current().number
        if actual == idx + 1:
            return True
        print(f'  [DESKTOP] switch to {idx + 1} landed on {actual} — retrying ({attempt}/{retries})')
    print(f'  [DESKTOP] could not switch to desktop {idx + 1} — continuing anyway')
    return False


def _main():
    total = len(get_virtual_desktops())         # accounts/desktops to loop over: 1 -> 2 -> ... -> total -> 1
    current = 0                                  # 0-based; always start on desktop 1
    print(f'[DESKTOP] {total} desktop(s) found — jumping to desktop 1')
    _go_to_desktop(current)

    print('Starting in 3s...')
    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)

    initialized = set()          # desktops that already had their first-run setup
    pending_arrows = set()       # desktops still owed a grab_arrows run in the current pass
    pending_repair = set()       # desktops still owed a repair run in the current pass
    _load_timer_state()
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

        if not find_game_window():
            # crashed / closed / frozen: don't click blindly here — move on. Its first-run setup
            # and any queued repair/arrows stay pending until the game is back.
            print(f'[DESKTOP] {current + 1}/{total} — game not running, skipping')
        elif not _account_online():
            # window still open but no in-game UI even after Alt+P — likely disconnected
            print(f'[DESKTOP] {current + 1}/{total} — game open but no in-game UI (disconnected?), skipping')
        else:
            print(f'[DESKTOP] Processing {current + 1}/{total}')
            if not focus_game_window():   # the game ignores the mouse until it's the active window
                print('  [DESKTOP] could not bring the game window to the front')
            if current not in initialized:
                done = _first_run(current)
                if done:
                    initialized.add(current)
                    pending_arrows.discard(current)   # just did both as part of the setup
                    pending_repair.discard(current)
            elif handle_revive():
                if current in pending_repair:
                    run_repair()
                    pending_repair.discard(current)
                _compose()
                if current in pending_arrows:
                    ensure_arrows()
                    pending_arrows.discard(current)
                done = True
            else:
                done = False

            if done:
                run_db_scroll()   # every loop, on every desktop (no-op if disabled or < MIN_COUNT)
                time.sleep(INTERVAL)
            else:
                print(f'[DESKTOP] {current + 1}/{total} — revive failed, moving on to the next desktop')

        if total < 2:
            continue
        current = (current + 1) % total   # after the last desktop, jump straight back to 1
        _go_to_desktop(current)


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
