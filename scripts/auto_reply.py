"""Answers a player who whispers one of our characters to start talking ("hi", "hey", "yo"...)
with a short line ("what?", "hi?"...) - the 'auto_reply' RULES setting, one rule a line:
    hi, hey, hello => what? | hi? | ?
The words left of => start a message (in any case, with any punctuation, letters drawn out -
"HEYYY!!" is "hey"); one of the lines right of it, picked at random, is whispered back after a
few seconds (as someone reading it would). Each player gets one auto reply per COOLDOWN minutes,
so a talk that goes on is left to the Telegram replies. Only whispers count (not World / Team...).
(When to send is decided by static.py.)"""
import random
import re
import time

from dragon_settings import get_settings

_DEFAULTS = {'ENABLED': False, 'COOLDOWN': 30, 'RULES': ''}
DELAY = (2.0, 5.0)              # seconds before the reply, at random between these

_last = {}                      # player (lower) -> when they last got an auto reply


def _settings():
    return get_settings('auto_reply', _DEFAULTS)


def enabled():
    return bool(_settings()['ENABLED'])


def _plain(text):
    """Lower case, letters / digits / '?' only, a letter said twice or more said once."""
    text = re.sub(r"[^a-z0-9? ]+", ' ', text.lower()).replace('?', ' ? ')   # "hi?" is "hi ?"
    return ' '.join(re.sub(r'(.)\1+', r'\1', text).split())


def _rules():
    """[([trigger, ...], [reply, ...])] from the RULES setting."""
    out = []
    for line in (_settings()['RULES'] or '').replace('\\n', '\n').splitlines():
        if '=>' not in line:
            continue
        said, answers = line.split('=>', 1)
        triggers = [_plain(t) for t in said.split(',') if _plain(t)]
        replies = [r.strip() for r in answers.split('|') if r.strip()]
        if triggers and replies:
            out.append((triggers, replies))
    return out


def reply_for(player, text, channel='Whisper'):
    """The line to whisper `player` for `text` - or None (no rule starts it, not a whisper, or
    they got one less than COOLDOWN minutes ago). Counts as sent."""
    if not enabled() or channel.lower() != 'whisper':
        return None
    now = time.time()
    if now - _last.get(player.lower(), 0) < float(_settings()['COOLDOWN']) * 60:
        return None
    said = _plain(text)
    for triggers, replies in _rules():
        if any(said == t or said.startswith(t + ' ') for t in triggers):
            _last[player.lower()] = now
            return random.choice(replies)
    return None


def when():
    """When a reply decided now goes out (time.time())."""
    return time.time() + random.uniform(*DELAY)
