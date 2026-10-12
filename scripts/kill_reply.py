"""Sends the killer a chat message: their name in the chat bar's left box, a random line from
the 'kill_reply' MESSAGES setting in the right box, then Enter.

The chat bar is found by its right end (the smiley, Clear and Chat buttons — chat.jpg from
x=565), which never changes; the two boxes are at fixed offsets from there, since their
contents (old names / half-typed text) would spoil a match on the whole bar."""
import os
import random
import time

import cv2

import game_input as gi
import screen
from _paths import app_root
from dragon_settings import get_settings
from game_window import focus_game_window

_bar = cv2.imread(os.path.join(app_root(), 'reference_images', 'chat.jpg'), cv2.IMREAD_GRAYSCALE)
ANCHOR_X = 565                                    # where the fixed right end of chat.jpg starts
_anchor = _bar[:, ANCHOR_X:] if _bar is not None else None
CONF = 0.8

# clicked near the right end of each box (caret after any old text), from the anchor's top-left
NAME_BOX = (120 - ANCHOR_X, 12)   # left box  (x 32..140 in chat.jpg): who the message goes to
TEXT_BOX = (520 - ANCHOR_X, 12)   # right box (x 225..565): the message
CLEAR_KEYS = 30

_DEFAULTS = {'ENABLED': False, 'MESSAGES': 'plz stop\nenough plz\nwtf\nwhy killing me?!'}


def enabled():
    return bool(get_settings('kill_reply', _DEFAULTS)['ENABLED'])


def _messages():
    text = get_settings('kill_reply', _DEFAULTS)['MESSAGES'] or ''
    return [line.strip() for line in text.replace('\\n', '\n').splitlines() if line.strip()]


def _fill(x, y, text):
    gi.click(x, y)
    time.sleep(0.2)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    return gi.type_text(text)


def send(killer):
    """Whispers a random MESSAGES line to `killer`. True if it was sent."""
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
        _fill(ax + NAME_BOX[0], ay + NAME_BOX[1], killer)
        _fill(ax + TEXT_BOX[0], ay + TEXT_BOX[1], message)
        gi.press('enter')
    print(f'[KILL] sent to {killer}: {message!r}')
    return True
