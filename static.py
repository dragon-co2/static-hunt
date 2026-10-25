"""Dragon CO2 - background hunting: every account runs in its own game, all on the one desktop,
nothing in front, no mouse, no keys. Everything goes through the games themselves (game_actions.py
injection, game_memory.py reads):

  - log in      bg_login.py: the launcher (it pulls updates, PLAY pressed for you) + the login
                screen's own auto-login; again whenever a game closes, crashes or gets disconnected
  - hunt        Scatter 4 times around the character, over and over (background_hunt.py)
  - + items     -> VIP compose warehouse (Deposit)
  - dragonballs 10 -> a dragonball scroll -> a regular remote bank tab
  - repair      VIP Repair All (1000 gold) every REPAIR_INTERVAL seconds
  - arrows      fewer than ARROWS_MIN equipped -> a pack from the bag or the remote bank, equipped
  - death       Revive Here after the revive wait (longer for a repeat killer, none for a monster,
                skipped from Telegram with rev / revall); never back to town
  - kills       the killer gets a whisper once we're revived (Kill reply), Telegram is told
  - Telegram    whispers to our characters are forwarded; a Reply there is whispered back
  - F10         with F10_CYCLONE on, the F10 hotkey is used every F10_EVERY seconds

One thread per account. Ctrl+Shift+C stops everything."""
import json
import logging
import logging.handlers
import os
import sys
import threading
import time

if getattr(sys, 'frozen', False):
    sys.path.insert(0, sys._MEIPASS)
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts'))

from pynput import keyboard as pynput_kb

import accounts
import background_hunt
import bg_login
import game_actions
import game_memory
import kill_reply
import kill_watch
import notify
import overlay
from dragon_settings import get_settings, override_dir

LOG_MAX_MB  = 5   # static.log rotates once it reaches this size...
LOG_BACKUPS = 3   # ...keeping this many old files

_S = get_settings('static', {'F10_CYCLONE': False, 'REPAIR_INTERVAL': 8000, 'FIRST_RUN_REPAIR': False})
F10_CYCLONE = bool(_S['F10_CYCLONE'])   # use the F10 hotkey every F10_EVERY seconds while hunting
REPAIR_INTERVAL  = _S['REPAIR_INTERVAL']          # seconds between repairs of each account's gear
FIRST_RUN_REPAIR = bool(_S['FIRST_RUN_REPAIR'])   # repair as soon as an account is in the game
F10_EVERY   = 10
F10_SLOT    = 9                         # F10

_R = get_settings('revive', {'REVIVE_DELAY': 360, 'REVIVE_TIME_MULTIPLIER': 2, 'MAX_REVIVE_WAIT': 1800,
                             'MONSTERS': 'Pheasant*\nBloodyBat*\nBullMonster*\nBullKing*\nHawKing*\nSandMonster*'})
REVIVE_DELAY = _R['REVIVE_DELAY']        # seconds dead before Revive Here
REVIVE_TIME_MULTIPLIER = max(1.0, float(_R['REVIVE_TIME_MULTIPLIER']))   # same killer again: x this
MAX_REVIVE_WAIT = _R['MAX_REVIVE_WAIT']  # ...up to this
MONSTERS = [m.strip() for m in (_R['MONSTERS'] or '').replace('\\n', '\n').splitlines() if m.strip()]

SERVICE_EVERY   = 10     # seconds between looks at the bag (+ items) and the equipped arrows
LAUNCH_COOLDOWN = 120    # seconds before a failed login is tried again
ARROWS_ALERT_EVERY = 3600   # seconds before the same account's "out of arrows" alert again
REPLY_TRIES     = 3      # failed whispers to one player before giving up
NAMES_PATH = os.path.join(override_dir(), 'account_characters.json')   # username -> character

_login_lock = threading.Lock()    # one login at a time: each new client takes the next auto-login file
_reply_lock = threading.Lock()
_reply_due  = {}    # slot -> [killer, ...] to whisper once revived
_custom_due = {}    # slot -> [(player, text, telegram msg id)] typed in Telegram
_rev_now    = set() # slots told from Telegram to revive without the wait
_status     = {}    # slot -> one line for the on-screen panel
_accts      = []


# ---------------------------------------------------------------- log, overlay, hotkey

class _Tee:
    """Mirrors a console stream into the rotating log, timestamping each complete line."""

    def __init__(self, stream, logger):
        self._stream, self._logger, self._buf = stream, logger, ''
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
    base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
    log_dir = os.path.join(base, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'static.log'), maxBytes=LOG_MAX_MB * 1024 * 1024,
        backupCount=LOG_BACKUPS, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger = logging.getLogger('static')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    sys.stdout = _Tee(sys.stdout, logger)
    sys.stderr = _Tee(sys.stderr, logger)
    logger.info('=' * 20 + ' static.py (background) started ' + '=' * 20)


_shift_held = False


def _on_key(key, down):
    global _shift_held
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_r):
        _shift_held = down
        return
    if down and getattr(key, 'char', None) == '\x03' and _shift_held:   # Ctrl+Shift+C
        print('  [HOTKEY] Ctrl+Shift+C — exiting')
        os._exit(0)


def _status_loop():
    while True:
        overlay.set_status('\n'.join(_status[s] for s in sorted(_status)))
        time.sleep(1)


def _fmt(seconds):
    m, s = divmod(max(0, int(seconds)), 60)
    return f'{m:02d}:{s:02d}'


def revive_wait_for(streak):
    """Revive wait for the `streak`-th death in a row by the same player."""
    if streak <= 1 or REVIVE_TIME_MULTIPLIER <= 1:
        return REVIVE_DELAY
    return int(min(REVIVE_DELAY * REVIVE_TIME_MULTIPLIER ** (streak - 1), max(MAX_REVIVE_WAIT, REVIVE_DELAY)))


# ---------------------------------------------------------------- which game is which account

def _load_names():
    try:
        with open(NAMES_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_name(username, character):
    names = _load_names()
    if names.get(username) == character:
        return
    names[username] = character
    try:
        tmp = NAMES_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(names, f, indent=1)
        os.replace(tmp, NAMES_PATH)
    except OSError:
        pass


def _find_game(character):
    """pid of the running game whose logged-in character is `character`, or None."""
    if not character:
        return None
    for pid in game_memory.game_pids():
        if game_memory._name_pid(pid) == character:
            return pid
    return None


# ---------------------------------------------------------------- Telegram / chat listeners

def _on_kill(character, slot, killer, streak):
    """kill_watch: a player killed one of ours - who replies, and Telegram."""
    if kill_reply.enabled():
        with _reply_lock:
            first = next((s for s, due in _reply_due.items() if killer.lower() in (k.lower() for k in due)), None)
            if kill_reply.already_messaged(killer):
                print(f'  [KILL] {killer} was already messaged (in the last 24h) — no new message')
            elif first is None:
                _reply_due.setdefault(slot, []).append(killer)
                print(f'  [KILL] {character} will whisper {killer} once revived')
    notify.kill(character, slot, killer, streak, revive_wait_for(streak))


def _on_whisper(sender, character, slot, text, channel='Whisper'):
    via = '' if channel.lower() == 'whisper' else f' [{channel}]'
    notify.send(f'\U0001F4E8 {sender} → {character} (account {slot}){via}: {text}',
                ref={'desktop': slot, 'player': sender, 'character': character})


def _on_tg_reply(ref, text, msg_id):
    with _reply_lock:
        _custom_due.setdefault(ref['desktop'], []).append((ref['player'], text, msg_id))
    print(f'[TELEGRAM] queued for account {ref["desktop"]}: whisper {ref["player"]}: {text}')
    notify.send(f'⏳ {ref["character"]} will whisper {ref["player"]} (when alive)', reply_to=msg_id)


def _on_tg_command(command, ref, msg_id):
    if command == 'revall':
        _rev_now.update(a.slot for a in _accts)
        what = 'every dead account revives now (no revive wait)'
    else:
        _rev_now.add(ref['desktop'])
        what = f'{ref["character"]} revives now (no revive wait)'
    print(f'[TELEGRAM] {command}: {what}')
    notify.send(f'♻️ {what}', reply_to=msg_id)


# ---------------------------------------------------------------- one account

class _Account:
    def __init__(self, acc):
        self.acc, self.slot = acc, acc.slot
        self.name = acc.name or _load_names().get(acc.username, '')
        self.pid = None
        self.in_game_once = False
        self.down = None              # [what happened, failure already reported] while it's out
        self.last_launch = 0.0
        self.dead_since = None
        self.revive_wait = None
        self.kill_streak = 0
        self.here_refused_told = False
        self.last_service = 0.0
        self.last_repair = 0.0 if FIRST_RUN_REPAIR else time.time()
        self.last_f10 = 0.0
        self.arrows_alerted = 0.0
        self.reply_tries = {}

    def who(self):
        return self.name or self.acc.username

    def say(self, text):
        _status[self.slot] = f'{self.slot}({self.who()}) {text}'

    # -- in the game?
    def ensure_in_game(self):
        """True once this account's character is in a game (logs in when it isn't)."""
        if self.pid and game_memory._name_pid(self.pid) == self.name and self.name:
            return True
        pid = _find_game(self.name)
        if pid:
            return self._got(pid)
        if self.in_game_once:
            self.in_game_once = False
            self.down = self.down or ['the game closed / crashed / was disconnected', False]
            print(f'[ACCOUNTS] {self.who()}: not in the game any more — logging in again')
        if time.time() - self.last_launch < LAUNCH_COOLDOWN:
            self.say(f'login retry in {_fmt(LAUNCH_COOLDOWN - (time.time() - self.last_launch))}')
            return False
        self.say('logging in...')
        with _login_lock:
            self.last_launch = time.time()
            pid = bg_login.login_retry(self.acc)
        if not pid:
            if self.down and not self.down[1]:
                self.down[1] = True
                notify.send(f'⚠️ {self.who()}: {self.down[0]} — could not get back in yet, '
                            f'retrying every {LAUNCH_COOLDOWN // 60} min')
            return False
        return self._got(pid)

    def _got(self, pid):
        self.pid = pid
        name = game_memory._name_pid(pid)
        if name and name != self.name:
            print(f'[ACCOUNTS] {self.acc.username}: character {name}')
            self.name = name
        _save_name(self.acc.username, self.name)
        kill_watch.set_character(self.name, self.slot)
        self.in_game_once = True
        if self.down:
            notify.send(f'♻️ {self.who()}: {self.down[0]} — reopened and back in game')
            self.down = None
        return True

    # -- death
    def handle_kills(self):
        for killer, repeat, monster in kill_watch.take(self.name):
            if monster:
                self.revive_wait = 0
                print(f'  [KILL] {self.name} was killed by the monster {killer} — reviving now')
                continue
            self.kill_streak = self.kill_streak + 1 if repeat else 1
            wait = revive_wait_for(self.kill_streak)
            self.revive_wait = wait
            if wait != REVIVE_DELAY:
                print(f'  [KILL] {killer} killed {self.name} {self.kill_streak} times in a row — '
                      f'revive wait {_fmt(wait)}')

    def alive_or_revived(self):
        """True if alive (or just revived); False while waiting to revive."""
        if not game_actions.i_am_dead(self.pid):
            if self.dead_since is not None:
                print(f'  [REVIVE] {self.name} is alive again')
            self.dead_since, self.revive_wait, self.here_refused_told = None, None, False
            _rev_now.discard(self.slot)
            return True
        now = time.time()
        if self.dead_since is None:
            self.dead_since = now
            print(f'  [REVIVE] {self.name} died')
        wait = REVIVE_DELAY if self.revive_wait is None else self.revive_wait
        left = wait - (now - self.dead_since)
        if left > 0 and self.slot not in _rev_now:
            self.say(f'dead - Revive Here in {_fmt(left)}')
            return False
        if not game_actions.revive_here_allowed(self.pid):
            if not self.here_refused_told:
                self.here_refused_told = True
                print(f'  [REVIVE] {self.name}: this map does not allow Revive Here — waiting (not reviving in town)')
                notify.send(f'⚠️ {self.name}: dead on a map without Revive Here — not revived')
            self.say('dead - no Revive Here on this map')
            return False
        print(f'  [REVIVE] {self.name}: Revive Here')
        game_actions.revive(self.pid, here=True)
        time.sleep(2)
        return not game_actions.i_am_dead(self.pid)

    # -- whispers
    def send_whispers(self):
        whisper = lambda player, text: game_actions.whisper(self.pid, player, text)
        with _reply_lock:
            due = list(_reply_due.get(self.slot) or [])
            custom = list(_custom_due.get(self.slot) or [])
        for killer in due:
            done = kill_reply.already_messaged(killer)
            message = None if done else kill_reply.send_with(whisper, killer)
            if message:
                kill_reply.remember(killer, self.name)
                notify.send(f'\U0001F4AC {self.name} whispered {killer}: "{message}"',
                            ref={'desktop': self.slot, 'player': killer, 'character': self.name})
                done = True
            if self._tried(('kill', killer.lower()), done):
                with _reply_lock:
                    if killer in _reply_due.get(self.slot, []):
                        _reply_due[self.slot].remove(killer)
        for item in custom:
            player, text, msg_id = item
            ok = kill_reply.send_with(whisper, player, message=text)
            if ok:
                notify.send(f'✅ {self.name} whispered {player}: "{text}"', reply_to=msg_id,
                            ref={'desktop': self.slot, 'player': player, 'character': self.name})
            elif self.reply_tries.get(('tg', msg_id), 0) + 1 >= REPLY_TRIES:
                notify.send(f'❌ {self.name} could not whisper {player}', reply_to=msg_id)
            if self._tried(('tg', msg_id), bool(ok)):
                with _reply_lock:
                    if item in _custom_due.get(self.slot, []):
                        _custom_due[self.slot].remove(item)

    def _tried(self, key, done):
        """Counts a failed send; True when the item is finished (sent, or given up)."""
        if done:
            self.reply_tries.pop(key, None)
            return True
        self.reply_tries[key] = self.reply_tries.get(key, 0) + 1
        if self.reply_tries[key] >= REPLY_TRIES:
            self.reply_tries.pop(key, None)
            return True
        return False

    # -- the bag, arrows, F10
    def service(self):
        if time.time() - self.last_service < SERVICE_EVERY:
            return
        self.last_service = time.time()
        if time.time() - self.last_repair >= REPAIR_INTERVAL:
            self.last_repair = time.time()
            background_hunt.repair(self.pid, self.name)
        background_hunt.dragonball_scrolls(self.pid, self.name)
        background_hunt.deposit_plus(self.pid, self.name)
        if not background_hunt.ensure_arrows(self.pid, self.name):
            left = game_memory._read_equipped_pid(self.pid)[0]
            if (left == 0 or not game_memory.bank_items(self.pid)) and \
                    time.time() - self.arrows_alerted >= ARROWS_ALERT_EVERY:
                self.arrows_alerted = time.time()
                notify.send(f'\U0001F3F9 {self.name}: out of arrows and no pack to take — {left} equipped')
        else:
            self.arrows_alerted = 0.0

    def f10(self):
        if F10_CYCLONE and time.time() - self.last_f10 >= F10_EVERY:
            self.last_f10 = time.time()
            game_actions.press_hotkey(self.pid, F10_SLOT)

    # -- the loop
    def run(self):
        while True:
            try:
                if not self.ensure_in_game():
                    time.sleep(5)
                    continue
                self.handle_kills()
                if not self.alive_or_revived():
                    time.sleep(2)
                    continue
                self.send_whispers()
                self.service()
                self.f10()
                arrows = game_memory._read_equipped_pid(self.pid)[0]
                self.say(f'hunting - arrows {arrows}')
                background_hunt.scatter_around(self.pid)
            except Exception as e:      # one account's trouble never stops the others
                print(f'[ACCOUNTS] {self.who()}: {type(e).__name__}: {e}')
                time.sleep(3)


def _main():
    global _accts
    if not accounts.enabled():
        print('[ACCOUNTS] turn on Accounts in the settings and fill the accounts in')
        return
    _accts = accounts.load()
    if not _accts:
        print('[ACCOUNTS] no account filled in')
        return
    print(f'[ACCOUNTS] {len(_accts)} account(s): ' + ', '.join(a.username for a in _accts))
    workers = [_Account(a) for a in _accts]
    if kill_reply.enabled() or REVIVE_TIME_MULTIPLIER > 1 or notify.enabled() or MONSTERS:
        kill_watch.ensure_chat_log(accounts.game_path())
        kill_watch.on_kill = _on_kill
        kill_watch.on_whisper = _on_whisper
        kill_watch.monsters = MONSTERS
        notify.on_reply = _on_tg_reply
        notify.on_command = _on_tg_command
        notify.listen()
        kill_watch.start({w.name: w.slot for w in workers if w.name}, accounts.game_path())
    threading.Thread(target=_status_loop, daemon=True, name='status').start()
    for w in workers:
        threading.Thread(target=w.run, daemon=True, name=f'account-{w.slot}').start()
    while True:
        time.sleep(1)


if __name__ == '__main__':
    pynput_kb.Listener(on_press=lambda k: _on_key(k, True), on_release=lambda k: _on_key(k, False),
                       daemon=True).start()
    overlay.start()
    _setup_log()
    try:
        _main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        notify.send_and_wait(f'❌ Dragon CO2 stopped on {os.environ.get("COMPUTERNAME", "this PC")}: '
                             f'{type(e).__name__}: {e}'[:900])
        if getattr(sys, 'frozen', False):
            input('\nCrashed - press Enter to close...')
        raise
