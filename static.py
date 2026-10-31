"""Dragon CO2 - background hunting: every account runs in its own game, all on the one desktop,
nothing in front, no mouse, no keys. Everything goes through the games themselves (game_actions.py
injection, game_memory.py reads):

  - log in      bg_login.py: the launcher (it pulls updates, PLAY pressed for you) + the login
                screen's own auto-login; again whenever a game closes, crashes or gets disconnected
  - hunt        Scatter 4 times around the character, over and over (background_hunt.py) - not
                while scripts/auto_hunt.py runs it (that one moves it and casts itself)
  - + items     -> VIP compose warehouse (Deposit); on Auto hunt, mailed every MAIL_EVERY seconds
                to the character picked in its panel (Mail items to), which claims them and deposits
                them in its own (mail_compose.py)
  - dragonballs 10 -> a dragonball scroll -> a regular remote bank tab
  - repair      VIP Repair All (1000 gold) every REPAIR_INTERVAL seconds
  - arrows      fewer than ARROWS_MIN equipped -> a pack from the bag or the remote bank, equipped
  - death       Revive Here after the revive wait (longer for a repeat killer, none for a monster,
                skipped from Telegram with rev / revall); never back to town
  - kills       the killer gets a whisper once we're revived (Kill reply), Telegram is told
  - Telegram    whispers to our characters are forwarded; a Reply there is whispered back
  - F10         ticked in its panel, the F10 hotkey is used every F10_EVERY seconds (a thread
                of its own: steady, whatever the rest of the visit is doing)

The control panel (panel.py) is the window: the characters - the games open now, and the ones
known from before (characters.py) greyed out. Nothing runs till you pick: each one's mode - off,
Static, Auto hunt (auto_hunt.py - Static's work too, on the move), Monk hunt (monk_hunt.py) or Auto heal (heal.py), one at
a time - its own options (Auto login, F10, Mail to) and the log (the console is hidden). One thread
per character. Closing the window or Ctrl+Shift+C stops everything."""
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

import access
import accounts
import auto_hunt
import background_hunt
import bg_login
import characters
import game_actions
import game_memory
import auto_reply
import heal
import hunt_zones
from hunt_zones import MAP_NAMES
import hunt_stats
import kill_reply
import kill_watch
import mail_compose
import monk_hunt
import notify
import overlay
import panel
import travel
from dragon_settings import get_settings, override_dir

LOG_MAX_MB  = 5   # static.log rotates once it reaches this size...
LOG_BACKUPS = 3   # ...keeping this many old files

_S = get_settings('static', {'REPAIR_INTERVAL': 8000, 'FIRST_RUN_REPAIR': False})
REPAIR_INTERVAL  = _S['REPAIR_INTERVAL']          # seconds between repairs of each account's gear
FIRST_RUN_REPAIR = bool(_S['FIRST_RUN_REPAIR'])   # repair as soon as an account is in the game
F10_EVERY   = 2
F10_SLOT    = 9                         # F10
F10_NOT_HUNTING = {8002}                # skills not used with F10 while the auto hunt moves the character
                                        # (an archer's Fly: its game crashed, jumping / to NPCs flying)
MAIL_EVERY  = 600                       # seconds between mails of the compose items (Mail to) - the
                                        # first right as the character is in the game
_mail_lock  = threading.Lock()          # one character mailing at a time, not all together
_mail_sent  = threading.Event()         # set once one of ours mailed: the receivers claim right away

_R = get_settings('revive', {'REVIVE_DELAY': 360, 'REVIVE_TIME_MULTIPLIER': 2, 'MAX_REVIVE_WAIT': 1800})
REVIVE_DELAY = _R['REVIVE_DELAY']        # seconds dead before Revive Here
REVIVE_TIME_MULTIPLIER = max(1.0, float(_R['REVIVE_TIME_MULTIPLIER']))   # same killer again: x this
MAX_REVIVE_WAIT = _R['MAX_REVIVE_WAIT']  # ...up to this
PLAYER_IDS = 1000000                     # role ids from here up are players, below are monsters / NPCs

SERVICE_EVERY   = 10     # seconds between looks at the bag (+ items) and the equipped arrows
LAUNCH_COOLDOWN = 10     # seconds before a failed login is tried again
LOGIN_ALERT     = 5      # failed login attempts in a row before Telegram is told (once)
ARROWS_ALERT_EVERY = 3600   # seconds before the same account's "out of arrows" alert again
REPLY_TRIES     = 3      # failed whispers to one player before giving up
AUTO_REPLY_PAUSE = 8     # seconds the auto hunt holds still at most while an auto reply goes out
NAMES_PATH = os.path.join(override_dir(), 'account_characters.json')   # username -> character
MODES = {'off': 'off', 'static': 'Static', 'auto': 'Auto hunt', 'monk': 'Monk hunt',
         'heal': 'Auto heal'}
LOCKED = {'auto', 'monk', 'heal'}       # modes for the users only (access.py) - Static is for all
MAIL_JOBS = {'send': 'Send', 'claim': 'Receive to bag', 'receive': 'Receive to bank',
             'withdraw': 'Export from bank'}

_logging_in = set()               # slots being logged in right now (one at a time - _login_loop)
_reply_lock = threading.Lock()
_reply_due  = {}    # slot -> [killer, ...] to whisper once revived
_custom_due = {}    # slot -> [(player, text, telegram msg id)] typed in Telegram
_auto_due   = {}    # slot -> [(player, text, when, channel)] auto replies (auto_reply) - sent from `when` on
_rev_now    = set() # slots told from Telegram to revive without the wait
_status     = {}    # slot -> one line for the on-screen panel
_rows       = []    # the panel's characters (_Account), in order
_hunts      = {}    # character -> (mode, thread) of its auto hunt / monk hunt
_heal_on    = [False]   # heal.py's threads started (the first account put on Auto heal)
_jobs       = {}    # character key -> (job, thread) of its Mail & Bank Compose button
_job_said   = {}    # character key -> how its last one went


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
                    panel.push(line)
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


def _others(username):
    """The characters of the other accounts (as read from their games) - never this one's."""
    names = {u: n for u, n in _load_names().items() if n}
    return {n for u, n in names.items() if u != username} - {names.get(username)}


def _find_game(character):
    """pid of the running game whose logged-in character is `character`, or None."""
    if not character:
        return None
    for pid in game_memory.game_pids():
        if game_memory._name_pid(pid) == character:
            return pid
    return None


# ---------------------------------------------------------------- Telegram / chat listeners

def _option(character, option):
    """The character's own option (characters.py) - e.g. kill_reply, auto_reply."""
    return bool(characters.get(characters.key_of(character))[option])


def _on_kill(character, slot, killer, streak):
    """kill_watch: a player killed one of ours - who replies (its Kill reply on), and Telegram."""
    if _option(character, 'kill_reply'):
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
    if auto_reply.sent_by_us(sender, character, text):  # one of ours answering another of ours:
        return                                          # what we sent ourselves - no answer to it
    staff = auto_reply.is_staff(sender)                 # the game's staff: you answer, never the bot
    notify.send(('\U0001F6A8 STAFF ' if staff else '\U0001F4E8 ')
                + f'{sender} → {character} (account {slot}){via}: {text}',   # Talk too
                ref={'desktop': slot, 'player': sender, 'character': character})
    auto_reply.note(character, sender, 'them', text)
    if staff:
        print(f'[AUTO REPLY] {sender} is staff - no auto reply, told on Telegram')
        return
    if not _option(character, 'auto_reply'):            # its Auto reply off: Telegram only
        return
    threading.Thread(target=_auto_answer, args=(sender, character, slot, channel, via),
                     daemon=True).start()               # the AI takes a second or two


def _auto_answer(sender, character, slot, channel, via):
    reply = auto_reply.reply_for(sender, _last_said(character, sender), channel, character)
    if reply:
        with _reply_lock:
            _auto_due.setdefault(slot, []).append((sender, reply, auto_reply.when(), channel))
        print(f'[AUTO REPLY] {character} will answer {sender}{via}: {reply}')


def _last_said(character, player):
    with auto_reply._lock:
        talk = auto_reply._history.get((character.lower(), player.lower()), ())
        return next((said for who, said in reversed(talk) if who == 'them'), '')


def _on_tg_reply(ref, text, msg_id):
    auto_reply.note(ref['character'], ref['player'], 'me', text)   # the AI knows what you said
    with _reply_lock:
        _custom_due.setdefault(ref['desktop'], []).append((ref['player'], text, msg_id))
    print(f'[TELEGRAM] queued for account {ref["desktop"]}: whisper {ref["player"]}: {text}')
    notify.send(f'⏳ {ref["character"]} will whisper {ref["player"]} (when alive)', reply_to=msg_id)


def _on_tg_command(command, ref, msg_id):
    if command == 'revall':
        _rev_now.update(_status.keys())             # every account / game being run
        what = 'every dead account revives now (no revive wait)'
    else:
        _rev_now.add(ref['desktop'])
        what = f'{ref["character"]} revives now (no revive wait)'
    print(f'[TELEGRAM] {command}: {what}')
    notify.send(f'♻️ {what}', reply_to=msg_id)


# ---------------------------------------------------------------- one account

class _Account:
    """One character (its profile `key` in characters.py): found in its game by its name whenever
    that game is open; logged in by _login_loop only when told to (keep_in: Log in in the panel,
    or its game was open with Auto login ticked) - then again after a crash / disconnect."""

    def __init__(self, slot, key):
        self.slot, self.key = slot, key
        self.name = characters.get(key)['name']
        self.acc = None               # accounts.Account while it has a login (_login_loop)
        self.pid = None
        self.mode = 'off'             # the panel's: off / static / auto / monk / heal
        self.thread = None
        self.keep_in = False          # logged in (again) by the script
        self.gone = False             # forgotten in the panel: its thread ends
        self.moving = False           # a teleport from the panel: its auto hunt not started again yet
        self.in_game_once = False
        self.down = None              # [what happened, failure already reported] while it's out
        self.login_fails = 0          # failed login attempts in a row
        self.last_launch = 0.0
        self.dead_since = None
        self.revive_wait = None
        self.kill_streak = 0
        self.here_refused_told = False
        self.last_service = 0.0
        self.last_repair = 0.0 if FIRST_RUN_REPAIR else time.time()
        self.last_f10 = 0.0
        self.last_mail = 0.0
        self.last_received = 0.0
        self.arrows_alerted = 0.0
        self.reply_tries = {}

    def profile(self):
        return characters.get(self.key)

    def who(self):
        return self.name or self.profile()['user'] or f'game {self.pid}'

    def say(self, text):
        _status[self.slot] = f'{self.slot}({self.who()}) {text}'

    # -- in the game?
    def in_game(self):
        """True while this character is in a game (finds it again in a new game). A game that
        crashed but stays open (its "stopped working" report up, or frozen) is closed here - so
        it's logged in again (Auto login)."""
        if self.pid and self.name and game_memory._name_pid(self.pid) == self.name:
            why = game_memory.crashed(self.pid)
            if not why:
                return True
            print(f'[ACCOUNTS] {self.who()}: {why} - its game closed')
            notify.send(f'💥 {self.who()}: {why} - game closed'
                        + (', logging in again' if self.keep_in and self.profile()['auto_login'] else ''))
            game_memory.close_game(self.pid)
            time.sleep(1)
        pid = _find_game(self.name)
        if pid:
            return self._got(pid)
        if self.in_game_once:
            self.in_game_once = False
            if self.keep_in and self.profile()['auto_login']:
                self.down = self.down or ['the game closed / crashed / was disconnected', False]
                print(f'[ACCOUNTS] {self.who()}: not in the game any more — it will be logged in again')
            else:                                       # not coming back by itself: its mode off
                self.mode = 'off'
                print(f'[ACCOUNTS] {self.who()}: its game closed / logged out')
        return False

    def login_due(self):
        """True if it can be logged in now, False not yet (LAUNCH_COOLDOWN), None never (no
        Auto login / no username / no password that decrypts)."""
        p = self.profile()
        if not (p['auto_login'] and p['user'] and p['pass']):
            return None
        return time.time() - self.last_launch >= LAUNCH_COOLDOWN

    def login(self):
        """Logs this character in - called by _login_loop, one at a time."""
        p = self.profile()
        password = characters.password(self.key)
        if not password:
            print(f'[ACCOUNTS] {self.who()}: the saved password can not be decrypted (saved on another '
                  'PC or Windows user?) - type it again in its Auto login')
            self.keep_in = False
            return False
        self.acc = accounts.Account(self.slot, self.slot, p['user'], password)
        self.say('logging in...')
        self.last_launch = time.time()
        pid = bg_login.login_retry(self.acc, others=_others(self.acc.username),
                                   failed=self._login_failed)
        return bool(pid) and self._got(pid)

    def _login_failed(self):
        """One login attempt failed - a crash and reopen is not reported, only LOGIN_ALERT in a row."""
        self.login_fails += 1
        if self.login_fails == LOGIN_ALERT:
            what = f'{self.down[0]} — ' if self.down else ''
            notify.send(f'⚠️ {self.who()}: {what}could not get into the game {LOGIN_ALERT} times '
                        f'in a row — still retrying every {LAUNCH_COOLDOWN}s')
            if self.down:
                self.down[1] = True

    def _got(self, pid):
        name = game_memory._name_pid(pid)
        if self.acc and name and name in _others(self.acc.username):
            print(f"[ACCOUNTS] {self.who()}: game {pid} is {name} - another account's, not taken")
            return False
        self.pid = pid
        if name and name != self.name:
            print(f'[ACCOUNTS] {self.who()}: character {name}')
            self.name = name
            self.key = characters.named(self.key, name)  # a profile made from its username
        if self.acc:
            _save_name(self.acc.username, self.name)
        if self.profile()['auto_login']:
            self.keep_in = True                         # in the game: logged in again if it drops
        kill_watch.set_character(self.name, self.slot)
        self.in_game_once = True
        if self.login_fails >= LOGIN_ALERT:     # told it could not get in: tell it's back
            notify.send(f'♻️ {self.who()}: back in game after {self.login_fails} failed attempts')
        self.login_fails = 0
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
        if travel.claimed(self.name):                  # auto hunt: no revive wait - Revive (in town)
            return self._revive_in_town()               # at once, then on to another city
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

    def _revive_in_town(self):
        """Auto hunt: Revive (back in town, away from whoever killed it) - tried again each visit
        till the game takes it (it refuses one too soon after the death)."""
        self.say('dead - Revive (auto hunt, no wait)')
        if time.time() - getattr(self, 'revive_tried', 0) < 2:
            return False
        self.revive_tried = time.time()
        print(f'  [REVIVE] {self.name}: Revive (auto hunt - in town at once, then on to another city)')
        game_actions.revive(self.pid, here=False)
        time.sleep(2)
        return not game_actions.i_am_dead(self.pid)

    # -- whispers
    def send_whispers(self):
        whisper = lambda player, text: game_actions.whisper(self.pid, player, text)
        with _reply_lock:
            due = list(_reply_due.get(self.slot) or [])
            custom = list(_custom_due.get(self.slot) or [])
            auto = [a for a in _auto_due.get(self.slot) or [] if a[2] <= time.time()]
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
        for item in auto:
            player, text, _, channel = item
            hunting = travel.claimed(self.name)
            if hunting:                                 # the auto hunt holds still a moment,
                travel.pause(self.name, AUTO_REPLY_PAUSE)   # turns to them, answers, goes on
                time.sleep(1.2)                         # (the jump in the air lands)
                self._face(player)
                time.sleep(0.6)
            if channel.lower() == 'talk':                   # answered where they said it: the Talk
                ok = kill_reply.send_with(lambda to, t: game_actions.whisper(   # to them, in the Talk
                    self.pid, to, t, game_actions.CHAT_TALK), player, message=text)
            else:
                ok = kill_reply.send_with(whisper, player, message=text)
            if ok:
                auto_reply.note(self.name, player, 'me', text)
                via = ' (Talk)' if channel.lower() == 'talk' else ''
                notify.send(f'\U0001F916 {self.name} auto-replied to {player}{via}: "{text}"',
                            ref={'desktop': self.slot, 'player': player, 'character': self.name})
            if hunting:
                time.sleep(1.5)
                travel.pause(self.name, 0)
            if self._tried(('auto', player.lower(), text), bool(ok)):
                with _reply_lock:
                    if item in _auto_due.get(self.slot, []):
                        _auto_due[self.slot].remove(item)

    def _face(self, player):
        """Turns to `player` (if around): a step their way - as a player who's been spoken to."""
        try:
            role = game_actions.players(self.pid).get(player)
            if role is None:
                return
            them = game_actions.positions(self.pid, [game_actions.role_id(self.pid, role)])
            me = game_actions.my_pos(self.pid)
            at = next(iter(them.values()), None)
            if me and at:
                sign = lambda v: (v > 0) - (v < 0)
                game_actions.run_to(self.pid, me[0] + sign(at[0] - me[0]), me[1] + sign(at[1] - me[1]))
        except OSError as e:
            print(f'[AUTO REPLY] {self.name}: could not turn to {player} ({e})')

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
    def settled(self):
        """False while its game is still loading (travel.game_ready): nothing is sent into it
        (deposits, mail, F10, Scatter...) - one still loading crashes."""
        return bool(self.pid) and travel.game_ready(self.pid)

    def service(self):
        if time.time() - self.last_service < SERVICE_EVERY or not self.settled():
            return
        if travel.in_market_trip(self.name):            # auto hunt's Market trip: its dragonballs
            return                                      # are for sale, not for scrolls and the bank
        busy = _jobs.get(self.key)
        if busy and busy[1].is_alive():                 # a Mail & Bank Compose button at work
            return
        self.last_service = time.time()
        if time.time() - self.last_repair >= REPAIR_INTERVAL:
            self.last_repair = time.time()
            background_hunt.repair(self.pid, self.name)
        background_hunt.dragonball_scrolls(self.pid, self.name)
        background_hunt.deposit_plus(self.pid, self.name)
        me = self.profile()
        if self.name.lower() in _receivers():
            # a receiver: as soon as one of ours mailed, or a mail shows unread, else every MAIL_EVERY
            if _mail_sent.is_set() or mail_compose.unread(self.pid) or \
                    time.time() - self.last_received >= MAIL_EVERY:
                _mail_sent.clear()
                self.last_received = time.time()
                mail_compose.receive(self.pid, self.name)      # Claim All, Deposit, mark read
        to = (me['mail_to'] or '').strip() if self.mode == 'auto' else ''   # Auto hunt only
        if to and to.lower() != self.name.lower() and time.time() - self.last_mail >= MAIL_EVERY:
            self.last_mail = time.time()
            with _mail_lock:                            # one character mailing at a time
                sent = mail_compose.transfer(self.pid, self.name, to,   # its CPs in the first mail
                                             cps_keep=int(me['cps_keep']) if me['mail_cps'] else None)
            if sent:
                _mail_sent.set()
        armed = background_hunt.ensure_arrows(self.pid, self.name)   # None: no pack in any tab
        if not armed:
            left = game_memory._read_equipped_pid(self.pid)[0]
            if (left == 0 or armed is None) and \
                    time.time() - self.arrows_alerted >= ARROWS_ALERT_EVERY:
                self.arrows_alerted = time.time()
                notify.send(f'\U0001F3F9 {self.name}: out of arrows and no pack to take — {left} equipped')
        else:
            self.arrows_alerted = 0.0

    def f10_loop(self):
        """F10 every F10_EVERY seconds, steady - on its own thread, not held up by the deposits,
        the bank or the mail; not while the game is gone, the account is off or it's dead."""
        while True:
            time.sleep(F10_EVERY)
            try:
                if (self.mode in ('static', 'auto') and self.profile()['f10']
                        and self.pid and self.name and self.settled()
                        and game_memory._name_pid(self.pid) == self.name
                        and not game_actions.i_am_dead(self.pid)
                        and not (travel.claimed(self.name)
                                 and game_actions.hotkey_id(self.pid, F10_SLOT) in F10_NOT_HUNTING)):
                    game_actions.press_hotkey(self.pid, F10_SLOT)
            except OSError as e:
                print(f'[F10] {self.name}: {e}')

    # -- the loop
    def run(self):
        while True:
            try:
                if self.gone:                   # forgotten in the panel
                    return
                if not self.in_game():
                    if self.slot in _logging_in:
                        pass
                    elif self.login_due() is not None and self.keep_in:
                        self.say('waiting for its turn to log in')
                    else:
                        self.say('not in game')
                    time.sleep(2)
                    continue
                if self.mode == 'off':
                    self.say('in game - nothing picked')
                    time.sleep(1)
                    continue
                hunt_stats.watch(self.pid, self.name)   # the daily counts (hunt_stats.py)
                self.handle_kills()
                if self.mode in ('monk', 'heal'):       # its own thread does it - none of static's
                    self.say(MODES[self.mode])          # (no Scatter, bank, mail, revive...)
                    time.sleep(1)
                    continue
                if not self.alive_or_revived():
                    time.sleep(2)
                    continue
                if not self.settled():          # just logged in: no Scatter / whispers / service yet
                    self.say('logged in - waiting for the game to finish loading')
                    time.sleep(1)
                    continue
                self.send_whispers()
                self.service()
                arrows = game_memory._read_equipped_pid(self.pid)[0]
                if travel.claimed(self.name):           # auto_hunt.py moves it and casts itself
                    self.say(f'auto hunt - arrows {arrows}')
                    time.sleep(1)
                    continue
                if travel.map_id(self.pid) in travel.NO_SCATTER_MAPS:   # the Market: no monsters
                    self.say(f'in the Market - no Scatter, arrows {arrows}')
                    time.sleep(1)
                    continue
                self.say(f'hunting - arrows {arrows}')
                background_hunt.scatter_around(self.pid)
            except Exception as e:      # one account's trouble never stops the others
                print(f'[ACCOUNTS] {self.who()}: {type(e).__name__}: {e}')
                time.sleep(3)


def _login_loop(workers):
    """Auto-login mode: the accounts are logged in one after another, in their order - the next
    only once the one before is in the game (or its login gave up). Runs for good: an account that
    drops out is logged in again on the next pass."""
    while True:
        for w in list(workers):
            if not w.keep_in or w.gone or w.in_game() or not w.login_due():
                continue
            _logging_in.add(w.slot)
            try:
                w.login()
            except Exception as e:
                print(f'[ACCOUNTS] {w.who()}: login failed - {type(e).__name__}: {e}')
            finally:
                _logging_in.discard(w.slot)
        time.sleep(2)


def _watch_chat(workers):
    kill_watch.ensure_chat_log(accounts.game_path())
    kill_watch.on_kill = _on_kill
    kill_watch.on_whisper = _on_whisper
    kill_watch.on_drop = hunt_stats.on_drop
    kill_watch.monster_lookup = _monster_named
    notify.on_reply = _on_tg_reply
    notify.on_command = _on_tg_command
    notify.listen()
    kill_watch.start({w.name: w.slot for w in workers if w.name}, accounts.game_path())


def _monster_named(name):
    """True if the role called `name` around one of our characters is a monster (its id is below
    the players'), False if a player, None if no game sees it."""
    for pid in game_memory.game_pids():
        for role_name, role in game_actions.roles(pid):
            if role_name.lower() == name.lower():
                try:
                    return game_actions.role_id(pid, role) < PLAYER_IDS
                except OSError:                 # freed while we were reading it
                    continue
    return None


def _start(worker):
    worker.thread = threading.Thread(target=worker.run, daemon=True, name=f'account-{worker.slot}')
    worker.thread.start()
    threading.Thread(target=worker.f10_loop, daemon=True, name=f'f10-{worker.slot}').start()


# ---------------------------------------------------------------- the control panel (panel.py)

def _receivers():
    """The characters some of ours mail their compose items to (lower case)."""
    return {characters.get(k)['mail_to'].strip().lower() for k in characters.all_keys()} - {''}


def _add_row(key):
    w = _Account(len(_rows) + 1, key)
    _rows.append(w)
    if w.name:
        kill_watch.set_character(w.name, w.slot)
    _start(w)
    return w


def _discover():
    """A game logged in that isn't a row yet (a character never seen here) becomes one."""
    for pid in game_memory.game_pids():
        name = game_memory._name_pid(pid)
        if name and not any(r.name == name for r in _rows):
            print(f'[ACCOUNTS] {name}: in a game - added to the panel')
            _add_row(characters.ensure(name))


def _heal_start():
    """heal.py's threads (its potions loop, the players list, the Ctrl+F shortcut) - once."""
    if _heal_on[0]:
        return
    _heal_on[0] = True
    heal.ONLY_TICKED = True                     # its buffs too: only the Auto heal characters
    heal._load_checked()
    heal._load_enemies()
    threading.Thread(target=heal._heal_loop, daemon=True, name='heal').start()
    threading.Thread(target=heal._kills_loop, daemon=True, name='heal-kills').start()
    heal._hotkeys()
    print(f'[HEAL] on - {heal.HOTKEY["label"]} in a game jumps to the target')


def _steer(w):
    """Starts / stops the character's auto hunt or monk hunt thread to match its mode."""
    name = w.name
    kind, thread = _hunts.get(name, (None, None))
    if thread and thread.is_alive():
        if kind != w.mode:                          # mode changed: it stops at its next look
            with monk_hunt._lock:
                monk_hunt._checked.discard(name)
        return
    if w.mode not in ('auto', 'monk') or w.moving or not (w.pid and game_memory._name_pid(w.pid) == name):
        return
    if not w.settled():
        return
    with monk_hunt._lock:
        monk_hunt._checked.add(name)
    hunt = auto_hunt._hunt if w.mode == 'auto' else monk_hunt._hunt
    thread = threading.Thread(target=hunt, args=(w.pid, name), daemon=True, name=f'{w.mode}-{name}')
    _hunts[name] = (w.mode, thread)
    thread.start()


def _control():
    """Every second: new games, each character's hunt thread, who Auto heal heals."""
    while True:
        try:
            _discover()
            if not access.active():                     # no access (any more): its modes off
                for w in _rows:
                    if w.mode in LOCKED:
                        print(f'[ACCESS] {w.who()}: {MODES[w.mode]} off - Auto features not active')
                        w.mode = 'off'
            for w in list(_rows):
                if w.name and not w.gone:
                    _steer(w)
            if _heal_on[0]:
                with heal._lock:
                    heal._checked.clear()
                    heal._checked.update(w.name for w in _rows if w.mode == 'heal' and w.name)
        except Exception as e:
            print(f'[PANEL] {type(e).__name__}: {e}')
        time.sleep(1)


def _row(key):
    w = next((r for r in _rows if r.key == key and not r.gone), None)
    if w is None:
        raise LookupError(f'no character {key}')
    return w


def _running(w):
    return bool(w.pid and w.name and game_memory._name_pid(w.pid) == w.name)


def _state():
    """What the page shows."""
    rows = []
    for w in _rows:
        if w.gone:
            continue
        name, p = w.name, w.profile()
        kind, thread = _hunts.get(name, (None, None))
        row = {'key': w.key, 'slot': w.slot, 'name': name or p['user'], 'running': _running(w),
               'logging_in': w.slot in _logging_in or (w.keep_in and not _running(w)
                                                        and w.login_due() is not None),
               'mode': w.mode, 'status': _status.get(w.slot, '').split(') ', 1)[-1],
               'hunt': monk_hunt._status.get(name, '') if thread and thread.is_alive() else '',
               'profile': {'user': p['user'], 'has_pass': bool(p['pass']), 'auto_login': p['auto_login'],
                           'f10': p['f10'], 'mail_to': p['mail_to'], 'mail_cps': p['mail_cps'],
                           'cps_keep': p['cps_keep'], 'kill_reply': p['kill_reply'],
                           'auto_reply': p['auto_reply']}}
        busy = _jobs.get(w.key)
        row['job'] = {'busy': MAIL_JOBS[busy[0]] if busy and busy[1].is_alive() else '',
                      'said': _job_said.get(w.key, '')}
        row['profile']['send_to'] = p['send_to']
        if w.mode in ('static', 'auto') and row['running']:
            row['auto'] = _auto_info(w)
        if w.mode == 'heal' and _heal_on[0]:
            with heal._lock:
                v = heal._seen.get(name)
                ticked = set(heal._buffs.get(name, ()))
            has = heal._skills.get(name, (0, set()))[1]
            row['heal'] = {
                'vitals': v and {'hp': v['hp'], 'max_hp': v['max_hp'], 'mp': v['mp'],
                                 'potions': v['potions']},
                'buffs': [{'skill': s, 'name': label, 'on': s in ticked, 'has': s in has,
                           'active': bool(v and v['status'] & bit)}
                          for s, (label, bit) in heal.BUFFS.items()]}
        rows.append(row)
    out = {'accounts': rows, 'modes': MODES, 'access': access.state(), 'locked': sorted(LOCKED),
           'admin': os.path.exists(os.path.join(override_dir(), 'users_admin.json')),
           'cities': [[code, title] for code, title in travel.TELEPORT_NAMES.items()]
           + [[place, way[2]] for place, way in travel.PLACES.items()]}
    if _heal_on[0]:
        target = ''
        try:
            pid = heal._foreground_game()
            if pid:
                around = game_actions.players(pid)
                target = (heal._list_target(pid, around)[0] if heal._enemies['on']
                          else heal._label_name(pid, around)) or ''
        except Exception:
            pass
        out['heal'] = {'list_on': heal._enemies['on'], 'lock': heal._enemies['lock'],
                       'players': list(heal._enemies['players']), 'target': target,
                       'hotkey': heal.HOTKEY['label'], 'capturing': heal._capture[0] is not None,
                       'skip_near': heal.SKIP_NEAR[0], 'near_cells': heal.NEAR_CELLS[0],
                       'path_find': heal.PATH_FIND[0]}
    return out


def _act(body):
    """A click in the page."""
    action = body.get('action') or ''
    if action == 'mode':
        w, mode = _row(body.get('key')), body.get('mode')
        if mode not in MODES:
            return {'ok': False, 'message': f'unknown mode {mode}'}
        if mode != 'off' and not _running(w):
            return {'ok': False, 'message': f'{w.who()} is not in the game'}
        if mode in LOCKED and not access.active():
            return {'ok': False, 'message': 'Activate Auto Features first'}
        if mode == 'heal':
            _heal_start()
        w.mode = mode
        print(f'[PANEL] {w.who()}: {MODES[mode]}')
    elif action == 'profile':                       # its own options: f10, mail_to, mail_cps, cps_keep
        w, field, value = _row(body.get('key')), body.get('field'), body.get('value')
        if field == 'cps_keep':
            value = max(0, int(value or 0))
        elif field == 'mail_to':
            value = (value or '').strip()
        elif field == 'send_to':
            value = (value or '').strip()
        elif field in ('f10', 'mail_cps', 'auto_login', 'kill_reply', 'auto_reply'):
            value = bool(value)
        else:
            return {'ok': False, 'message': f'unknown option {field}'}
        characters.update(w.key, **{field: value})
        if field == 'auto_login':
            w.keep_in = bool(value) and _running(w)     # in the game now: logged in again if it drops
        print(f'[PANEL] {w.who()}: {field} = {value}')
    elif action == 'login_save':                    # the Auto login's username / password
        w = _row(body.get('key'))
        user = (body.get('user') or '').strip()
        if not user:
            return {'ok': False, 'message': 'Type the username'}
        characters.set_login(w.key, user, body.get('password') or '')
        if not characters.get(w.key)['pass']:
            return {'ok': False, 'message': 'Type the password'}
        characters.update(w.key, auto_login=True)
        if _running(w):
            w.keep_in = True
        print(f'[PANEL] {w.who()}: Auto login saved ({user})')
    elif action == 'login_now':
        w = _row(body.get('key'))
        if w.login_due() is None:
            return {'ok': False, 'message': 'Tick Auto login and save its username and password first'}
        w.keep_in, w.last_launch = True, 0.0
        print(f'[PANEL] {w.who()}: logging in')
    elif action == 'login_stop':
        w = _row(body.get('key'))
        w.keep_in = False
        print(f'[PANEL] {w.who()}: not logged in again')
    elif action == 'forget':
        w = _row(body.get('key'))
        if _running(w):
            return {'ok': False, 'message': 'Close its game first'}
        w.gone, w.keep_in, w.mode = True, False, 'off'
        characters.forget(w.key)
        print(f'[PANEL] {w.who()}: forgotten')
    elif action == 'activate':
        ok, message = access.activate(body.get('user') or '', body.get('password') or '')
        return {'ok': ok, 'message': message}
    elif action == 'sign_out':
        access.sign_out()
    elif action == 'users':                         # the Users window (users.py) - with the ADMIN_KEY
        import users
        if not users.saved_key():
            return {'ok': False, 'message': 'No ADMIN_KEY on this PC'}
        try:
            return users.admin(body.get('op'), body.get('user') or '', body.get('days') or 30)
        except OSError as e:
            return {'ok': False, 'message': str(e)}
    elif action in ('mail_job', 'teleport', 'zone_save') and not access.active():
        return {'ok': False, 'message': 'Activate Auto Features first'}
    elif action == 'mail_job':
        return _mail_job(_row(body.get('key')), body.get('job'), (body.get('to') or '').strip())
    elif action == 'teleport':
        code = body.get('code')
        return _teleport(_row(body.get('key')), code if code in travel.PLACES else int(code or 0))
    elif action == 'zone_save':                     # the page's drawing: its own zone of the map
        w, the_map = _row(body.get('key')), int(body.get('map'))
        squares = None if body.get('default') else {tuple(sq) for sq in body.get('squares') or []}
        travel.save_zone(the_map, squares, w.name)
        print(f'[ZONES] {w.name}: map {the_map} - ' + ('back to the default zone' if squares is None
                                                     else f'its own zone saved ({len(squares)} squares)'))
    elif action == 'settings':
        _open_settings()
    elif action == 'restart':
        _restart()
    elif action.startswith('heal_'):
        _heal_act(action[5:], body)
    else:
        return {'ok': False, 'message': f'unknown action {action}'}
    return {'ok': True}


def _mail_job(w, job, to):
    """A Mail & Bank Compose button (mail_compose.py), on a thread of its own - one at a time."""
    if job not in MAIL_JOBS:
        return {'ok': False, 'message': f'unknown job {job}'}
    if not _running(w):
        return {'ok': False, 'message': f'{w.who()} is not in the game'}
    busy = _jobs.get(w.key)
    if busy and busy[1].is_alive():
        return {'ok': False, 'message': f'{w.who()} is still on {MAIL_JOBS[busy[0]]}'}
    if job == 'send':
        if not to:
            return {'ok': False, 'message': 'Type who to send to'}
        if to.lower() == w.name.lower():
            return {'ok': False, 'message': 'It can not mail itself'}
        characters.update(w.key, send_to=to)
    pid, name, p = w.pid, w.name, w.profile()

    def run():
        _job_said[w.key] = f'{MAIL_JOBS[job]}...'
        try:
            if job == 'send':
                with _mail_lock:
                    n = mail_compose.transfer(pid, name, to,
                                              cps_keep=int(p['cps_keep']) if p['mail_cps'] else None)
                said = f'{n} item(s) mailed to {to}'
            elif job == 'withdraw':
                said = f'{mail_compose.withdraw_all(pid, name)} item(s) taken out to the bag'
            else:
                n = mail_compose.receive(pid, name, deposit=job == 'receive')
                said = f'{n} + item(s) claimed' + (' and deposited' if job == 'receive' and n else '')
        except Exception as e:
            said = f'{MAIL_JOBS[job]} failed - {type(e).__name__}: {e}'
        _job_said[w.key] = said
        print(f'[MAIL] {name}: {said}')

    thread = threading.Thread(target=run, daemon=True, name=f'mail-{name}')
    _jobs[w.key] = (job, thread)
    thread.start()
    return {'ok': True}


def _auto_info(w):
    """The Auto hunt card's numbers: the map, the kills (today / all days) and the arrows."""
    try:
        the_map = travel.map_id(w.pid)
        arrows = game_memory._read_equipped_pid(w.pid)[0]
    except OSError:
        the_map, arrows = None, None
    today, total = 0, 0
    with hunt_stats._lock:
        days = hunt_stats._load()
        for day, names in days.items():
            kills = sum(m.get('kills', 0) for m in names.get(w.name, {}).values())
            total += kills
            if day == time.strftime('%Y-%m-%d'):
                today = kills
    return {'map': MAP_NAMES.get(the_map, f'map {the_map}' if the_map else '-'),
            'kills_today': today, 'kills_total': total, 'arrows': arrows}


_map_png = {}       # map -> (width, height, PNG of its walkable cells)


def _get(path, q):
    """The page's other GETs: the zone editor's map picture and drawing."""
    import io
    if path == '/api/map.png':
        the_map = int(q['map'])
        if the_map not in _map_png:
            from PIL import Image
            root = hunt_zones._root()
            width, height, walkable = travel.load_map(travel.map_file(root, the_map))
            ground = Image.frombytes('L', (width, height), bytes(walkable))
            picture = Image.merge('RGB', [ground.point(lambda v, c=c: hunt_zones.GROUND[c] if v
                                                       else hunt_zones.WALL[c]) for c in range(3)])
            out = io.BytesIO()
            picture.save(out, 'PNG')
            _map_png[the_map] = (width, height, out.getvalue())
        return 'image/png', _map_png[the_map][2]
    if path == '/api/zone':
        w = _row(q['key'])
        here = travel.map_id(w.pid) if _running(w) else None
        the_map = int(q.get('map') or here or travel.TWIN_CITY)
        _get('/api/map.png', {'map': the_map})                  # its size
        width, height, _ = _map_png[the_map]
        chars = [[n, pos[0], pos[1]] for n, (m, pos) in hunt_zones._characters().items() if m == the_map]
        if q.get('chars'):
            return 'application/json', json.dumps({'chars': chars})
        maps = sorted(set(MAP_NAMES) | ({here} if here else set()))
        try:
            areas = [[t, x, y] for t, x, y, _ in travel.hunting_areas(hunt_zones._root(), the_map)]
        except OSError:
            areas = []
        return 'application/json', json.dumps({
            'map': the_map, 'here': here, 'width': width, 'height': height, 'cells': travel.ZONE_CELLS,
            'squares': sorted(travel.zone(the_map, w.name)), 'own': travel.own_zone(the_map, w.name),
            'areas': areas, 'chars': chars,
            'maps': [[m, MAP_NAMES.get(m, f'map {m}')] for m in maps]}, ensure_ascii=False)
    return None


def _teleport(w, code):
    """Teleport (VIP Remote Travel) to a city - or to a PLACES map (a teleport, then its way
    there): its auto hunt stops, the trip, then the auto hunt starts again there."""
    place = travel.PLACES.get(code)
    if code not in travel.TELEPORT_NAMES and not place:
        return {'ok': False, 'message': f'unknown city {code}'}
    if not _running(w) or w.moving:
        return {'ok': False, 'message': f'{w.who()} can not teleport now'}
    city = place[2] if place else travel.TELEPORT_NAMES[code]

    def run():
        w.moving = True
        try:
            _job_said[w.key] = f'to {city}: stopping the hunt...'
            with monk_hunt._lock:
                monk_hunt._checked.discard(w.name)
            thread = _hunts.get(w.name, (None, None))[1]
            end = time.time() + 40
            while thread and thread.is_alive() and time.time() < end:
                time.sleep(0.3)
            _job_said[w.key] = f'teleporting to {city}...'
            if place:
                steps, the_map, _ = place
                done = travel.map_id(w.pid) == the_map or travel.side_trip(w.pid, w.name, steps,
                                                                           the_map, city)
            else:
                done = travel.teleport(w.pid, w.name, code)
            said = f'in {city}' if done else f'the way to {city} did not work'
        except Exception as e:
            said = f'teleport failed - {type(e).__name__}: {e}'
        finally:
            w.moving = False                        # _control starts its auto hunt again
        _job_said[w.key] = said
        print(f'[PANEL] {w.name}: {said}')

    threading.Thread(target=run, daemon=True, name=f'teleport-{w.name}').start()
    return {'ok': True}


def _heal_act(what, body):
    """The Auto heal options (heal.py's window, here)."""
    e = heal._enemies
    if what == 'buff':
        w = _row(body.get('key'))
        with heal._lock:
            types = heal._buffs.setdefault(w.name, set())
            (types.add if body.get('on') else types.discard)(int(body.get('skill')))
    elif what == 'list_on':
        e['on'] = bool(body.get('on'))
        heal._attack_seen.clear()
        print('[ENEMIES] the list ' + ('on: attacked players are added, the shortcut goes for them'
                                      if e['on'] else 'off: the shortcut uses the chat name box'))
    elif what == 'lock':
        name = body.get('name') or ''
        e['lock'] = name if name in e['players'] else ''
        print('[ENEMIES] ' + (f'{name} locked - the shortcut goes only for them' if e['lock'] else 'unlocked'))
    elif what == 'pick':                            # lock off: to the top - the target from now on
        name = body.get('name')
        if not e['lock'] and name in e['players']:
            e['players'].remove(name)
            e['players'].insert(0, name)
            print(f'[ENEMIES] {name} picked - the shortcut target')
    elif what == 'remove':
        name = body.get('name')
        if name in e['players']:
            e['players'].remove(name)
            if e['lock'] == name:
                e['lock'] = ''
    elif what == 'clear':
        e['players'], e['lock'] = [], ''
    elif what == 'hotkey':
        heal._capture[0] = (lambda label: None) if not heal._capture[0] else None
    elif what == 'skip_near':
        heal.SKIP_NEAR[0] = bool(body.get('on'))
    elif what == 'near_cells':
        heal.NEAR_CELLS[0] = max(1, min(16, int(body.get('cells') or 1)))
    elif what == 'path_find':
        heal.PATH_FIND[0] = bool(body.get('on'))
    heal._save_enemies()
    heal._save_checked()


def _open_script(name, *args):
    """scripts/<name> in its own window - no console."""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts', name)
    python = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    subprocess.Popen([python if os.path.exists(python) else sys.executable, script, *args],
                     cwd=os.path.dirname(script), creationflags=0x08000000)   # CREATE_NO_WINDOW


def _open_settings():
    """The global settings (settings_server.py)."""
    _open_script('settings_server.py')
    print('[PANEL] global settings opened - most changes take effect after Restart')


def _restart():
    """A new static.py (settings read again), then this one ends."""
    import subprocess
    args = [sys.executable] + (sys.argv[1:] if getattr(sys, 'frozen', False)
                               else [os.path.abspath(__file__)] + sys.argv[1:])
    print('[PANEL] restarting...')
    subprocess.Popen(args, cwd=os.path.dirname(os.path.abspath(sys.argv[0])),
                     creationflags=0x00000010)              # CREATE_NEW_CONSOLE (hidden by its panel)
    os._exit(0)


def _main():
    threading.Thread(target=_status_loop, daemon=True, name='status').start()
    accounts.move_game_path()
    for key in characters.all_keys():               # the characters known: greyed out till in a game
        _add_row(key)
    _discover()                                     # the games open now
    print(f'[ACCOUNTS] {len(_rows)} character(s): '
          + ', '.join(f'{w.who()} ({"in game" if _find_game(w.name) else "closed"})' for w in _rows)
          + ' - nothing runs till you pick it in the panel')
    _watch_chat(_rows)
    access.keep_checking()
    threading.Thread(target=_login_loop, args=(_rows,), daemon=True, name='logins').start()
    threading.Thread(target=_control, daemon=True, name='control').start()
    panel.run(_state, _act, _get)
    print('[PANEL] window closed - stopped')
    os._exit(0)


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
