"""Whispers the killer a chat message: the channel button (Talk) -> Whisper, their name in the
chat bar's left box, a line from the 'kill_reply' MESSAGES setting in the right box, then Enter.
(When to send — once per killer, after the revive — is decided by static.py.)

Each line is sent once: the ones already sent are kept in used_kill_messages.json (all accounts,
across restarts) and a random unsent one is picked; once all went out, it starts over.

Who was already messaged is kept in messaged_killers.json next to the settings, shared by all
the accounts and kept across restarts: a killer gets one message, whichever account they kill,
until FORGET_AFTER has passed since it — then they're dropped from the file.

The chat bar is found by its right end (the smiley, Clear and Chat buttons — chat.jpg from
x=565), which never changes; the two boxes are at fixed offsets from there, since their
contents (old names / half-typed text) would spoil a match on the whole bar."""
import json
import os
import random
import threading
import time

import cv2

import game_input as gi
import screen
from _paths import app_root
from dragon_settings import get_settings, override_dir
from game_window import focus_game_window

_bar = cv2.imread(os.path.join(app_root(), 'reference_images', 'chat.jpg'), cv2.IMREAD_GRAYSCALE)
ANCHOR_X = 565                                    # where the fixed right end of chat.jpg starts
_anchor = _bar[:, ANCHOR_X:] if _bar is not None else None
CONF = 0.8

_whisper = cv2.imread(os.path.join(app_root(), 'reference_images', 'whisper.jpg'), cv2.IMREAD_GRAYSCALE)
CONF_WHISPER  = 0.8
WHISPER_WAIT  = 2     # seconds for the channel menu (whisper.jpg) to show after clicking Talk
WHISPER_TRIES = 3

# clicked near the right end of each box (caret after any old text), from the anchor's top-left
NAME_BOX = (120 - ANCHOR_X, 12)   # left box  (x 32..140 in chat.jpg): who the message goes to
TEXT_BOX = (520 - ANCHOR_X, 12)   # right box (x 225..565): the message
TALK_BTN = (195 - ANCHOR_X, 12)   # the channel button between them (x 173..217, says "Talk")
CLEAR_KEYS = 30

_MESSAGES = [   # the default MESSAGES: each reply picks one at random
    "plz stop",
    "enough plz",
    "wtf",
    "why killing me?!",
    "bro im just hunting",
    ":|",
    ":/",
    "plz let me hunt in peace",
    "stop that!",
    "dude chill",
    "????",
    "leave me alone plz",
    "stop",
    "not cool man",
    "really?",
    "stop wasting ur time",
    "leave me alone :D",
    "leave me alone",
    "you only good at killing archer?",
    "you are so good, play at wars!",
    "hey stop killing me",
]
_DEFAULTS = {'ENABLED': False, 'SEND_ONCE_EVERYDAY': True, 'MESSAGES': '\n'.join(_MESSAGES)}


LOG_PATH = os.path.join(override_dir(), 'messaged_killers.json')
FORGET_AFTER = 24 * 3600   # seconds a messaged killer stays in the log
_log_lock = threading.Lock()


def _read_log():
    """{killer lower: {'name', 'character', 'at' (time.time()), 'when' (readable)}}, without the
    entries older than FORGET_AFTER."""
    try:
        with open(LOG_PATH, encoding='utf-8') as f:
            log = json.load(f)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in log.items()
            if isinstance(v, dict) and isinstance(v.get('at'), (int, float)) and now - v['at'] < FORGET_AFTER}


def already_messaged(killer):
    """True if `killer` got a message (from any account) within the last FORGET_AFTER — always
    False with SEND_ONCE_EVERYDAY off (then they get one after every revive)."""
    if not get_settings('kill_reply', _DEFAULTS)['SEND_ONCE_EVERYDAY']:
        return False
    with _log_lock:
        return killer.lower() in _read_log()


def remember(killer, character):
    """Logs that `killer` was messaged (for killing `character`); drops the expired entries."""
    with _log_lock:
        log = _read_log()
        log[killer.lower()] = {'name': killer, 'character': character, 'at': time.time(),
                               'when': time.strftime('%Y-%m-%d %H:%M:%S')}
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, 'w', encoding='utf-8') as f:
                json.dump(log, f, indent=1)
        except OSError as e:
            print(f'[KILL] could not save {LOG_PATH} ({e})')


USED_PATH = os.path.join(override_dir(), 'used_kill_messages.json')   # MESSAGES lines already sent


def _read_used():
    try:
        with open(USED_PATH, encoding='utf-8') as f:
            used = json.load(f)
        return used if isinstance(used, list) else []
    except (OSError, ValueError):
        return []


def _save_used(used):
    try:
        os.makedirs(os.path.dirname(USED_PATH), exist_ok=True)
        with open(USED_PATH, 'w', encoding='utf-8') as f:
            json.dump(used, f, indent=1)
    except OSError as e:
        print(f'[KILL] could not save {USED_PATH} ({e})')


def _pick(lines):
    """A random MESSAGES line not sent yet (all accounts, kept across restarts). Once every line
    went out, the round starts over - never with the line that was sent last."""
    with _log_lock:
        used = _read_used()
    fresh = [line for line in lines if line not in used]
    if not fresh:
        fresh = [line for line in lines if not used or line != used[-1]] or lines
    return random.choice(fresh)


def _mark_used(message, lines):
    """Logs that `message` was sent; when that was the last unsent line, the next round starts."""
    with _log_lock:
        used = [line for line in _read_used() if line in lines and line != message]
        if not [line for line in lines if line not in used and line != message]:
            used = []                       # every line went out: start over (keep only the last)
        _save_used(used + [message])


def enabled():
    return bool(get_settings('kill_reply', _DEFAULTS)['ENABLED'])


def _messages():
    text = get_settings('kill_reply', _DEFAULTS)['MESSAGES'] or ''
    return [line.strip() for line in text.replace('\\n', '\n').splitlines() if line.strip()]


def _pick_whisper(ax, ay):
    """Clicks the channel button (Talk), then Whisper in the menu it opens. False if the menu
    never showed."""
    for attempt in range(1, WHISPER_TRIES + 1):
        gi.click(ax + TALK_BTN[0], ay + TALK_BTN[1])
        end = time.time() + WHISPER_WAIT
        while time.time() < end:
            hit = screen.find(screen.grab_gray(), _whisper, CONF_WHISPER)
            if hit:
                gi.click(hit[0] + hit[2] // 2, hit[1] + hit[3] // 2)
                time.sleep(0.3)
                print('  [CLICK] Talk -> Whisper')
                return True
            time.sleep(0.2)
        print(f'  [KILL] whisper.jpg did not show after clicking Talk ({attempt}/{WHISPER_TRIES})')
    return False


def _fill(x, y, text):
    gi.click(x, y)
    time.sleep(0.2)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    return gi.type_text(text)


def untypeable(text):
    """The characters of `text` a US keyboard can't type (they'd be skipped), in order."""
    import ctypes
    hkl = gi._us_layout()
    return [c for c in text if ctypes.windll.user32.VkKeyScanExW(ord(c), hkl) in (-1, 0xFFFF)]


def send(killer, message=None):
    """Whispers `killer` a MESSAGES line not sent before (or `message`). Returns the text sent,
    or False."""
    custom = bool(message)
    lines = [message] if message else _messages()
    if not lines:
        print('[KILL] no reply messages set — nothing sent')
        return False
    focus_game_window()
    hit = screen.find(screen.grab_gray(), _anchor, CONF)
    if not hit:
        print('[KILL] chat bar not found on screen — reply skipped')
        return False
    ax, ay = hit[0], hit[1]
    message = lines[0] if custom else _pick(lines)
    with gi.KEYS:   # nothing else (the F10 presser) may type into the chat meanwhile
        if not _pick_whisper(ax, ay):
            print('[KILL] could not switch the chat to Whisper — not sent (never sent on Talk)')
            return False
        _fill(ax + NAME_BOX[0], ay + NAME_BOX[1], killer)
        _fill(ax + TEXT_BOX[0], ay + TEXT_BOX[1], message)
        gi.press('enter')
    print(f'[KILL] sent to {killer}: {message!r}')
    if not custom:
        _mark_used(message, lines)
    return message
