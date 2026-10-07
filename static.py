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

from new_bank_compose import run_new_bank_compose, deposit_plus_items, plus_items_in_inventory
from revive import handle_revive, is_dead, REVIVE_DELAY
from grab_arrows import ensure_arrows
from repaire import run_repair
from db_scroll import run_db_scroll
import panels
import screen
import game_input
import accounts
import login
import auto_hunt
import overlay
from game_window import find_game_window, focus_game_window
import repaire as _rep
import json
from dragon_settings import get_settings, override_dir


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = False  # moving the mouse to a screen corner does NOT stop the script
pyautogui.PAUSE    = 0

SWITCH_DELAY = 0.5   # seconds to let the desktop-switch animation finish

LOG_MAX_MB  = 5   # static.log rotates once it reaches this size...
LOG_BACKUPS = 3   # ...keeping this many old files (static.log.1 .. .3) — ~20 MB on disk at most

_S = get_settings('static', {
    'FIRST_RUN_REPAIR': True,
    'REPAIR_INTERVAL':  8000,
    'VISIT_WAIT':       15,
    'SERVICE_INTERVAL': 180,
})

FIRST_RUN_REPAIR = bool(_S['FIRST_RUN_REPAIR'])  # run repair on each desktop's first visit
REPAIR_INTERVAL  = _S['REPAIR_INTERVAL']         # seconds between repair passes over all desktops
VISIT_WAIT       = _S['VISIT_WAIT']              # seconds to pause at the end of a desktop visit before switching
SERVICE_INTERVAL = _S['SERVICE_INTERVAL']        # per desktop: seconds between service rounds (bank, arrows, deposit)

_timer_start = {}   # 'repair' -> time.time() the current interval started
_dead_since = {}    # desktop index -> time.time() its account was first seen dead
_last_service = {}  # desktop index -> time.time() of its last service round
_last_launch = {}   # desktop index -> time.time() its game was last started (accounts mode)
LAUNCH_COOLDOWN = 120  # seconds before a desktop's game may be started again after a failed login
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


def _open_bank_quick():
    """Alt+P once and up to 3s for the bank (+ inventory) to show. Doubles as the "is this account
    actually in the game?" check: a disconnected client never opens it."""
    if _rep._warehouse_open():
        return True
    _rep._press_alt_p()
    for _ in range(6):
        time.sleep(0.5)
        if _rep._warehouse_open():
            return True
    return False


def _service(idx):
    """Every SERVICE_INTERVAL seconds per desktop (the bank + inventory are open by now):
    dragonballs -> scroll -> bank, arrows -> grab + equip if none, +N items -> VIP > Compose >
    Deposit. Each step only clicks when there's something to do."""
    _last_service[idx] = time.time()
    run_db_scroll()      # no-op unless dragonballs >= MIN_COUNT or a scroll is waiting
    ensure_arrows()      # no-op unless the inventory has no arrows
    plus = plus_items_in_inventory()
    print(f'  [SERVICE] +N items: {", ".join(plus) if plus else "none"}')
    if plus and run_new_bank_compose():
        deposit_plus_items()


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
    for key, interval in (('repair', REPAIR_INTERVAL),):
        left = saved.get(key)
        if not isinstance(left, (int, float)) or not 0 <= left <= interval:
            left = interval
        _timer_start[key] = now - (interval - left)
    if 'repair' in saved:
        print(f'[TIMER] resumed from last session — repair in {_fmt_left(saved["repair"])}')


def _save_timer_state(repair_left):
    try:
        _os.makedirs(override_dir(), exist_ok=True)
        tmp = TIMER_STATE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'repair': max(0, round(repair_left))}, f)
        _os.replace(tmp, TIMER_STATE_PATH)  # atomic: a crash mid-write never leaves a broken file
    except Exception:
        pass


def _timer_status_loop():
    """Once a second, shows the repair countdown, each desktop's next service round and any
    revive countdowns in the on-screen timers panel, and saves the repair timer so the next run
    can resume from here."""
    while True:
        now = time.time()
        repair_left = REPAIR_INTERVAL - (now - _timer_start['repair'])
        lines = [f'Repair {_fmt_left(repair_left)}']
        for idx, last in sorted(_last_service.items()):
            left = SERVICE_INTERVAL - (now - last)
            lines.append(f'Service D{idx + 1} {_fmt_left(left) if left > 0 else "due"}')
        for idx, since in sorted(_dead_since.items()):
            lines.append(f'Revive D{idx + 1} {_fmt_left(REVIVE_DELAY - (now - since))}')
        overlay.set_status('\n'.join(lines))
        _save_timer_state(repair_left)
        time.sleep(1)


def _revive_gate(idx, total):
    """Per-desktop death handling. Returns True if the character is alive (carry on with this
    desktop), False to skip it for now. The first time a desktop is seen dead its clock starts;
    revive is only attempted once REVIVE_DELAY seconds have passed, and meanwhile the loop moves
    on so the other desktops keep running."""
    if not is_dead():
        if _dead_since.pop(idx, None) is not None:
            print(f'  [REVIVE] desktop {idx + 1} is alive again')
        return True
    now = time.time()
    since = _dead_since.setdefault(idx, now)
    left = REVIVE_DELAY - (now - since)
    if left > 0:
        print(f'[DESKTOP] {idx + 1}/{total} — dead, revive in {_fmt_left(left)}, skipping')
        return False
    if handle_revive():
        _dead_since.pop(idx, None)
        return True
    print(f'[DESKTOP] {idx + 1}/{total} — still dead after clicking Revive, moving on (retry next visit)')
    return False


def _ensure_account(idx, total, accts):
    """Accounts mode: makes sure desktop idx runs its account, logged in and hunting.
      - no game window (first run / crash): start the game, log in, turn on auto hunt
      - game open but on the login form (kicked / disconnected): log in again, auto hunt
    Returns True when the account is in the game, False to skip this desktop for now."""
    acc = accts[idx] if idx < len(accts) else None
    if acc is None:  # more desktops than accounts — nothing to open here
        return find_game_window() is not None

    if not find_game_window():
        since = time.time() - _last_launch.get(idx, 0)
        if since < LAUNCH_COOLDOWN:
            print(f'[ACCOUNTS] {idx + 1}/{total} — game still not up after a start {int(since)}s ago, '
                  f'waiting before trying again')
            return False
        print(f'[ACCOUNTS] {idx + 1}/{total} — no game running: opening {acc.username}')
        _last_launch[idx] = time.time()
        if not login.login(acc, launch_first=True):
            return False
        auto_hunt.start_hunting()
        return True

    if login.form_showing():
        print(f'[ACCOUNTS] {idx + 1}/{total} — {acc.username} is on the login screen: logging in')
        if not login.login(acc, launch_first=False):
            return False
        auto_hunt.start_hunting()
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
    accts = []
    if accounts.enabled():
        ok, sw, sh = accounts.screen_ok()
        if not ok:
            need_w, need_h = accounts.REQUIRED_SCREEN
            print(f'[ACCOUNTS] ERROR: the screen is {sw}x{sh}, but opening accounts needs {need_w}x{need_h} '
                  '(the login screen can only be found at that size) — accounts turned OFF for this run')
            game_input._message_box(
                f'Accounts are ON in the settings, but this screen is {sw}x{sh}.\n\n'
                f'Logging in only works at {need_w}x{need_h}. Accounts are turned off for this run; '
                'the script carries on without opening or logging in accounts.\n\n'
                f'Set the screen to {need_w}x{need_h} and start the script again.',
                'Dragon CO2 \u2014 accounts', 0x30)   # MB_ICONWARNING
    if accounts.enabled() and accounts.screen_ok()[0]:
        accts = accounts.load()
        print(f'[ACCOUNTS] {len(accts)} account(s): ' + ', '.join(f'{a.username} -> desktop {a.desktop}' for a in accts))
        if accts:
            accounts.sync_desktops(len(accts))   # one desktop per account: create / remove as needed
        if not accounts.game_path():
            print('[ACCOUNTS] GAME_PATH is not set (or the file is missing) — games cannot be opened automatically')
    total = len(get_virtual_desktops())         # accounts/desktops to loop over: 1 -> 2 -> ... -> total -> 1
    current = 0                                  # 0-based; always start on desktop 1
    print(f'[DESKTOP] {total} desktop(s) found — jumping to desktop 1')
    _go_to_desktop(current)

    print('Starting in 3s...')
    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)

    visited = set()              # desktops seen at least once (first visit may repair)
    pending_repair = set()       # desktops still owed a repair run in the current pass
    _load_timer_state()
    threading.Thread(target=_timer_status_loop, daemon=True, name='timer-status').start()

    if accts:
        # bring-up pass: get every account into the game first, back to back (no service, no
        # waiting between desktops), then start the normal rounds from desktop 1
        print(f'[ACCOUNTS] starting up {len(accts)} account(s)')
        for idx in range(total):
            if idx:
                _go_to_desktop(idx)
            try:
                _ensure_account(idx, total, accts)   # open / log in / auto hunt — services come in the rounds
            except OSError as e:
                print(f'[ACCOUNTS] {idx + 1}/{total} — screen unavailable ({e}); waiting for it')
                screen.wait_for_screen()
        if total > 1:
            _go_to_desktop(current)
        print('[ACCOUNTS] start-up done — starting the rounds')

    while True:
        now = time.time()
        if now - _timer_start['repair'] >= REPAIR_INTERVAL:
            print(f'[TIMER] {REPAIR_INTERVAL}s — repair queued for all desktops')
            pending_repair = set(range(total))
            _timer_start['repair'] = now

        if current not in visited:
            visited.add(current)
            if FIRST_RUN_REPAIR:
                pending_repair.add(current)

        try:
            if accts and not _ensure_account(current, total, accts):
                # accounts mode: couldn't get this desktop's account into the game — try next visit
                print(f'[DESKTOP] {current + 1}/{total} — account not in game yet, skipping')
            elif not find_game_window():
                # crashed / closed / frozen: don't click blindly here — move on; anything due stays due
                print(f'[DESKTOP] {current + 1}/{total} — game not running, skipping')
            else:
                if not focus_game_window():   # the game ignores the mouse until it's the active window
                    print('  [DESKTOP] could not bring the game window to the front')
                repair_due = current in pending_repair
                service_due = time.time() - _last_service.get(current, 0) >= SERVICE_INTERVAL
                alive = _revive_gate(current, total)
                if alive and not (repair_due or service_due):
                    # nothing to do this visit — say so, so a quiet log doesn't look like a hang
                    left = SERVICE_INTERVAL - (time.time() - _last_service.get(current, 0))
                    print(f'[DESKTOP] {current + 1}/{total} — idle, next service in {_fmt_left(left)}')
                elif alive:
                    print(f'[DESKTOP] Processing {current + 1}/{total}'
                          + (' — repair' if repair_due else '') + (' — service' if service_due else ''))
                    if not _open_bank_quick():
                        # no response to Alt+P: likely disconnected — skip, retry next visit
                        print(f'[DESKTOP] {current + 1}/{total} — bank did not open (disconnected?), skipping')
                    else:
                        if repair_due:
                            run_repair()
                            pending_repair.discard(current)
                        if service_due:
                            _service(current)
                        panels.close_all()   # inventory, bank and VIP menu
                time.sleep(VISIT_WAIT)
        except OSError as e:
            # a screen grab refused mid-visit (session locked, or switching screens — e.g. the RDP
            # session handed back to the console): wait for the screen, then carry on
            print(f'[DESKTOP] {current + 1}/{total} — screen unavailable ({e}); waiting for it')
            screen.wait_for_screen()

        if total < 2:
            continue
        current = (current + 1) % total   # after the last desktop, jump straight back to 1
        _go_to_desktop(current)


if __name__ == '__main__':
    # When frozen, an unhandled exception would otherwise close the console
    # window instantly and the error is never seen - print it and wait.
    game_input.ensure_driver()   # offers to install the driver if missing; never stops the script
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
