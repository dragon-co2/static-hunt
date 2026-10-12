"""Whispers the killer a chat message: the channel button (Talk) -> Whisper, their name in the
chat bar's left box, a random line from the 'kill_reply' MESSAGES setting in the right box,
then Enter. With ONCE_PER_KILLER on, each killer gets one message per character until
FORGET_HOURS have passed since it was sent; then they may get another (the list is kept in
kill_reply_sent.json next to the settings, so it survives restarts).

The chat bar is found by its right end (the smiley, Clear and Chat buttons — chat.jpg from
x=565), which never changes; the boxes and the Talk button are at fixed offsets from there,
since their contents (old names / half-typed text / channel name) would spoil a match on the
whole bar."""
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

# positions from the anchor's top-left; the boxes are clicked near their right end (caret after any old text)
NAME_BOX = (120 - ANCHOR_X, 12)   # left box  (x 32..140 in chat.jpg): who the message goes to
TALK_BTN = (195 - ANCHOR_X, 12)   # the channel button (x 173..217, says "Talk")
TEXT_BOX = (520 - ANCHOR_X, 12)   # right box (x 225..565): the message
CLEAR_KEYS = 30

_DEFAULTS = {'ENABLED': False, 'ONCE_PER_KILLER': True, 'FORGET_HOURS': 5,
             'MESSAGES': 'plz stop\nenough plz\nwtf\nwhy killing me?!'}
SENT_PATH = os.path.join(override_dir(), 'kill_reply_sent.json')   # {character: {killer: sent at}}
_sent_lock = threading.Lock()


def enabled():
    return bool(get_settings('kill_reply', _DEFAULTS)['ENABLED'])


def _messages():
    text = get_settings('kill_reply', _DEFAULTS)['MESSAGES'] or ''
    return [line.strip() for line in text.replace('\\n', '\n').splitlines() if line.strip()]


def _load_sent():
    """{character: {killer: time.time() the message went}}, without the ones older than
    FORGET_HOURS."""
    try:
        with open(SENT_PATH, encoding='utf-8') as f:
            sent = json.load(f)
    except (OSError, ValueError):
        return {}
    keep = float(get_settings('kill_reply', _DEFAULTS)['FORGET_HOURS']) * 3600
    now = time.time()
    out = {}
    for victim, killers in sent.items():
        if not isinstance(killers, dict):
            continue
        fresh = {k: at for k, at in killers.items() if isinstance(at, (int, float)) and now - at < keep}
        if fresh:
            out[victim] = fresh
    return out


def already_sent(victim, killer):
    """True if ONCE_PER_KILLER is on and `killer` already got a message for `victim`."""
    if not get_settings('kill_reply', _DEFAULTS)['ONCE_PER_KILLER']:
        return False
    with _sent_lock:
        return killer.lower() in _load_sent().get(victim.lower(), [])


def _remember(victim, killer):
    with _sent_lock:
        sent = _load_sent()
        sent.setdefault(victim.lower(), {})[killer.lower()] = time.time()
        try:
            with open(SENT_PATH, 'w', encoding='utf-8') as f:
                json.dump(sent, f, indent=1)
        except OSError as e:
            print(f'[KILL] could not save {SENT_PATH} ({e})')


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


def send(killer, victim=''):
    """Whispers a random MESSAGES line to `killer` (who killed our `victim`). True if sent."""
    if victim and already_sent(victim, killer):
        print(f'[KILL] {killer} already got a message for {victim} in the last '
              f'{get_settings("kill_reply", _DEFAULTS)["FORGET_HOURS"]}h — not sending again (ONCE_PER_KILLER)')
        return False
    lines = _messages()
    if not lines:
        print('[KILL] no reply messages set — nothing sent')
        return False
    focus_game_window()
    hit = screen.find(screen.grab_gray(), _anchor, CONF)
    if not hit:
        print('[KILL] chat bar not found on screen — reply skipped')
        return False
    ax, ay = hit[0], hit[1]
    message = random.choice(lines)
    with gi.KEYS:   # nothing else (the F10 presser) may type into the chat meanwhile
        if not _pick_whisper(ax, ay):
            print('[KILL] could not switch the chat to Whisper — reply skipped (not sent on Talk)')
            return False
        _fill(ax + NAME_BOX[0], ay + NAME_BOX[1], killer)
        _fill(ax + TEXT_BOX[0], ay + TEXT_BOX[1], message)
        gi.press('enter')
    print(f'[KILL] sent to {killer}: {message!r}')
    if victim:
        _remember(victim, killer)
    return True
