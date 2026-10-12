"""Telegram notifications ('telegram' settings section, shown as "Notifications").

The script talks to the Telegram Bot API over plain HTTPS (urllib, nothing to install):
  - getUpdates  -> finds the chat to write to: the last chat (private chat or group) that sent
                   the bot a message. Saved in telegram_chat.json next to the settings, so it
                   still works after Telegram forgets the message (it keeps them ~24h).
  - sendMessage -> sends the notification.
Only outgoing requests — no port to open. Messages go out from a background thread, so a slow
or missing connection never holds up the loop; a failed send is logged and dropped.
The bot token is stored DPAPI-encrypted (like account passwords) and never printed."""
import hashlib
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import secret
from dragon_settings import get_settings, override_dir

NOTIFY_EVERY  = 'Every kill'
NOTIFY_REPEAT = 'Same killer again'
NOTIFY_BURST  = '3+ kills in 30 min'
BURST_KILLS, BURST_WINDOW = 3, 30 * 60

_DEFAULTS = {'ENABLED': False, 'BOT_TOKEN': '', 'NOTIFY': NOTIFY_EVERY}
API = 'https://api.telegram.org/bot{token}/{method}'
TIMEOUT = 10
CHAT_PATH = os.path.join(override_dir(), 'telegram_chat.json')   # {token id: chat id}

_queue = queue.Queue()
_worker = None
_lock = threading.Lock()


def _settings():
    return get_settings('telegram', _DEFAULTS)


def _token():
    stored = _settings()['BOT_TOKEN']
    return secret.unprotect(stored) if stored else ''


def enabled():
    return bool(_settings()['ENABLED']) and bool(_token())


def mode():
    return _settings()['NOTIFY']


def _call(token, method, params=None):
    """One Bot API request -> its 'result'. Raises on network / API errors (the token is never
    part of the error text)."""
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(API.format(token=token, method=method), data, timeout=TIMEOUT) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except ValueError:
            raise OSError(f'HTTP {e.code}') from None
    except urllib.error.URLError as e:
        raise OSError(f'no connection ({e.reason})') from None
    if not body.get('ok'):
        raise OSError(body.get('description', 'Telegram refused the request'))
    return body['result']


def _token_id(token):
    return hashlib.sha256(token.encode()).hexdigest()[:12]   # which bot the saved chat belongs to


def _chat_id(token):
    """The chat to write to: the one saved for this bot, else the last chat that messaged it."""
    try:
        with open(CHAT_PATH, encoding='utf-8') as f:
            saved = json.load(f).get(_token_id(token))
        if saved:
            return saved
    except (OSError, ValueError):
        pass
    chat = None
    for update in _call(token, 'getUpdates'):
        msg = update.get('message') or update.get('channel_post') or update.get('my_chat_member') or {}
        chat = msg.get('chat') or chat
    if not chat:
        return None
    try:
        os.makedirs(os.path.dirname(CHAT_PATH), exist_ok=True)
        with open(CHAT_PATH, 'w', encoding='utf-8') as f:
            json.dump({_token_id(token): chat['id']}, f)
    except OSError:
        pass
    name = chat.get('title') or chat.get('first_name') or chat.get('username') or 'chat'
    print(f'[NOTIFY] sending notifications to the Telegram {chat.get("type", "chat")} "{name}"')
    return chat['id']


def _send_now(text):
    token = _token()
    if not token:
        return False
    try:
        chat = _chat_id(token)
        if not chat:
            print('[NOTIFY] no chat yet — open your bot in Telegram and send it any message, '
                  'then the notifications start')
            return False
        _call(token, 'sendMessage', {'chat_id': chat, 'text': text})
        return True
    except OSError as e:
        print(f'[NOTIFY] Telegram message not sent ({e})')
        return False


def _work():
    while True:
        _send_now(_queue.get())


def send(text):
    """Queues a notification (returns at once). Does nothing when notifications are off."""
    global _worker
    if not enabled():
        return
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_work, daemon=True, name='telegram')
            _worker.start()
    _queue.put(text)


def send_and_wait(text):
    """Sends right away and waits (a few seconds at most) — for the last message before the
    script exits, e.g. a crash."""
    return enabled() and _send_now(text)


_kills = {}   # character (lower) -> [time.time() of each recent kill]


def kill(character, desktop, killer, streak, revive_wait=None):
    """A kill of one of our characters; notifies according to the NOTIFY setting.
    streak = kills in a row by this killer (1 = a new one), revive_wait = seconds or None."""
    now = time.time()
    times = [t for t in _kills.get(character.lower(), []) if now - t < BURST_WINDOW] + [now]
    _kills[character.lower()] = times
    how = mode()
    if how == NOTIFY_REPEAT and streak < 2:
        return
    if how == NOTIFY_BURST and len(times) < BURST_KILLS:
        return
    lines = [f'\U0001F480 {character} (desktop {desktop}) was killed by {killer}']
    if streak > 1:
        lines.append(f'{streak} times in a row by {killer}')
    if len(times) >= BURST_KILLS:
        lines.append(f'⚠️ {len(times)} deaths in the last {BURST_WINDOW // 60} min')
    if revive_wait:
        lines.append(f'revive in {int(revive_wait) // 60}:{int(revive_wait) % 60:02d}')
    send('\n'.join(lines))
