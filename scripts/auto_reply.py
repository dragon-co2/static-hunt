"""Answers a player who whispers one of our characters to start talking ("hi", "hey", "yo"...)
with a short line ("what?", "hi?"...) - the 'auto_reply' RULES setting, one rule a line:
    hi, hey, hello => what? | hi? | ?
The words left of => start a message (in any case, with any punctuation, letters drawn out -
"HEYYY!!" is "hey"); one of the lines right of it, picked at random, is whispered back after a
few seconds (as someone reading it would) - a whisper with a whisper, a Talk line (to everyone
around) in the Talk. Each player gets one auto reply per COOLDOWN minutes, so a talk that goes on is
left to the Telegram replies. Only whispers and Talk count (not World / Team...).
With AI on (and an API key set), the reply is written by Claude instead - from the talk so far
with that player (_history), at most AI_LIMIT replies a player per AI_WINDOW minutes; whatever it
can't answer as a player would (a deal, a trade...) it leaves to you (Telegram); "are you a bot /
auto hunting?" is answered no, playing normally.
Staff ([GM], [PM], [PH]) never get an auto reply.
(When to send is decided by static.py.)"""
import collections
import json
import random
import re
import threading
import time

import secret
from dragon_settings import get_settings

_DEFAULTS = {'ENABLED': False, 'COOLDOWN': 30, 'RULES': '', 'AI': True, 'AI_MODEL': 'Haiku 5.5',
             'API_KEY': '', 'AI_LIMIT': 3, 'AI_WINDOW': 10}
DELAY = (2.0, 5.0)              # seconds before the reply, at random between these
MODELS = {'Haiku 5.5': 'claude-haiku-5-5', 'Sonnet 5.5': 'claude-sonnet-5-5', 'Opus 5.5': 'claude-opus-5-5'}
HISTORY = 12                    # lines of the talk with a player kept for the AI
STAFF = re.compile(r'\[(?:AM|DM|LH)\]', re.I)   # in a name: the game's staff - never auto-replied to

_last = {}                      # player (lower) -> when they last got an auto reply (rules)
_ai_sent = {}                   # player (lower) -> [times of the AI replies to them]
_history = {}                   # (character, player) lower -> deque of (who, text)
_lock = threading.Lock()
_client = [None, None]          # [the decrypted key it was made with, anthropic client]

SYSTEM = """You write chat replies for the player of the Conquer Online character {name}, who is busy \
hunting monsters and glances at the chat now and then. Someone wrote to them in the game ({channel}).

Write the one line the player would type back: casual and short (usually 1-6 words), lowercase, no emojis, \
no quotes, small typos are fine. Only letters, numbers and plain punctuation - it is typed on a US keyboard.

Answer in the language they wrote in:
- English -> casual gamer English ("hi", "ty", "np", "lol yeah").
- Franco / Arabizi (Egyptian Arabic in latin letters, with 2 = hamza, 3 = ain, 5 = kh, 7 = ha, 8 = gh) \
-> answer in Franco the same way. Examples: "ezayak" -> "tamam el7amdolelah", "enta feen" -> "bahunt aho", \
"3amel eh" -> "tamam w enta", "7elw el bow da" -> "tslm", "salam 3aleko" -> "w 3aleko el salam", \
"fe 7d hena?" -> "aywa".
- Any other latin-letter language -> answer in it; otherwise English.
Everyday questions (where are you, what are you doing, how are you, nice gear) get a short vague answer \
like a busy player's - no exact places, plans or numbers.
Asked to team / party / hunt together: say no - they won't keep hunting here (e.g. "sry not staying here", \
"leaving soon sry"; Franco: "msh hakamel tahneet hena", "ana mashy sorry").

Asked whether they are a bot, an AI, auto hunting, using a macro / script, or AFK: say no, they are \
playing normally, a bit annoyed at the question (e.g. "no lol im playing", "nah im here", "no just \
hunting"; Franco: "la ana bal3ab 3ady", "la ana mawgood aho", "auto eh ya 3am ana bal3ab").
Asked their real name, age, country / city or anything personal: don't tell, brush it off (e.g. "why u \
asking", "doesnt matter", "rather not say"; Franco: "yhmak f eh", "msh mohem", "msh 3ayez a2ol esmy").

Leave it to the real player (skip = true) when the message asks for anything only the player can decide (a trade, selling or buying, gold, items, a guild, a \
meeting, PK, account details); is rude enough that ignoring is better; or whenever you \
are not sure. Never promise or agree to anything."""

ANSWER = {'type': 'object', 'additionalProperties': False, 'required': ['skip', 'reply'],
          'properties': {'skip': {'type': 'boolean'}, 'reply': {'type': 'string'}}}


def is_staff(name):
    """True if `name` carries a staff tag ([GM], [PM], [PH])."""
    return bool(STAFF.search(name or ''))


def sent_by_us(sender, to, text):
    """True if `sender` (one of ours) sent `text` to `to` itself - an auto reply or a Telegram
    reply: not answered again (two of ours would answer each other for good)."""
    with _lock:
        talk = _history.get((sender.lower(), to.lower()), ())
        return any(who == 'me' and said.strip() == text.strip() for who, said in list(talk)[-4:])


def note(character, player, who, text):
    """Adds a line to the talk between `character` and `player` (who: 'them' / 'me')."""
    with _lock:
        _history.setdefault((character.lower(), player.lower()),
                            collections.deque(maxlen=HISTORY)).append((who, text))


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


def reply_for(player, text, channel='Whisper', character=''):
    """The line to answer `player` with for `text` - written by the AI when it's on (None: it left
    it to the player, or AI_LIMIT replies in AI_WINDOW already), else by the rules (None: no rule
    starts it, or they got one less than COOLDOWN minutes ago) - or None for staff and for
    channels other than Whisper / Talk. Counts as sent. The AI takes a second or two: call it off
    the chat reader's thread."""
    if channel.lower() not in ('whisper', 'talk') or is_staff(player):   # (on or off: the
                                                                          # character's own option)
        return None
    s = _settings()
    if s['AI'] and _key():
        now = time.time()
        sent = [t for t in _ai_sent.get(player.lower(), []) if now - t < float(s['AI_WINDOW']) * 60]
        _ai_sent[player.lower()] = sent
        if len(sent) >= int(s['AI_LIMIT']):
            print(f'[AUTO REPLY] {player}: {len(sent)} AI replies in {s["AI_WINDOW"]} min already - left to you')
            return None
        line = _ai(character, player, channel)
        if line:
            sent.append(now)
            return line
        if line is not None:                            # '' - it chose to leave it to the player
            return None                                 # (None: the AI failed - the rules below)
    return _by_rules(player, text)


def _key():
    stored = _settings()['API_KEY']
    return secret.unprotect(stored) if stored else ''


def _ai(character, player, channel):
    """The AI's line for the talk so far with `player` - '' if it left it to the player, None if
    it could not be asked (no key, no network, the API refused)."""
    import anthropic
    key = _key()
    if _client[0] != key:
        _client[0], _client[1] = key, anthropic.Anthropic(api_key=key, max_retries=1, timeout=15.0)
    with _lock:
        talk = list(_history.get((character.lower(), player.lower()), ()))
    lines = '\n'.join(f'{player if who == "them" else character}: {said}' for who, said in talk)
    try:
        response = _client[1].messages.create(
            model=MODELS.get(_settings()['AI_MODEL'], MODELS['Haiku 5.5']),
            max_tokens=1024,
            output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': ANSWER}},
            system=SYSTEM.format(name=character or 'the player', channel=channel),
            messages=[{'role': 'user', 'content': f'The chat so far (newest last):\n{lines}\n\n'
                                                  f'Your reply to {player}:'}],
        )
    except anthropic.AuthenticationError:
        print('[AUTO REPLY] the API key was refused - set a new one in the settings (Auto reply)')
        return None
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        print(f'[AUTO REPLY] the AI could not be asked ({type(e).__name__}) - the rules answer instead')
        return None
    if response.stop_reason != 'end_turn':              # a refusal, cut short: left to the player
        return ''
    try:
        answer = json.loads(''.join(b.text for b in response.content if b.type == 'text'))
    except ValueError:
        return ''
    text = ' '.join(str(answer.get('reply') or '').split()).strip('"\'')
    if answer.get('skip') or not text:
        print(f'[AUTO REPLY] the AI left {player} to you')
        return ''
    return text[:120]


def _by_rules(player, text):
    """The RULES' line for `text` - or None."""
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
