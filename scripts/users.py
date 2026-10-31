"""The users of the Auto features, on the server (worker/license_worker.js) - for you only: it
needs the server's ADMIN_KEY (asked once, kept DPAPI-encrypted on this PC).

    python users.py add <user> [days]     a new user (30 days by default) - its password is made
                                          up and shown once: give it to them
    python users.py list                  every user: until when, its PC, last seen
    python users.py extend <user> [days]  more days (from today if it ran out)
    python users.py reset <user>          its PC forgotten - the next PC it's used on is kept
    python users.py disable <user>        off (enable: on again)
    python users.py remove <user>
The panel (static.py) has the same as a window - its Users button, on a PC with the ADMIN_KEY."""
import getpass
import json
import os
import secrets
import string
import sys

import access
import secret
from dragon_settings import override_dir

KEY_PATH = os.path.join(override_dir(), 'users_admin.json')


def saved_key():
    """The ADMIN_KEY kept on this PC ('' if none)."""
    try:
        with open(KEY_PATH, encoding='utf-8') as f:
            return secret.unprotect(json.load(f)['key']) or ''
    except (OSError, ValueError, KeyError):
        return ''


def save_key(key):
    os.makedirs(override_dir(), exist_ok=True)
    with open(KEY_PATH, 'w', encoding='utf-8') as f:
        json.dump({'key': secret.protect(key.strip())}, f)


def admin(op, user='', days=30):
    """One admin op on the server -> its answer (an add's made-up password in it as 'password').
    Raises OSError with no answer; a refused key is forgotten."""
    body = {'op': op, 'user': user}
    if op in ('add', 'extend'):
        body['days'] = int(days)
    if op == 'add':
        body['password'] = _password()
    answer = access.call('/admin', body, saved_key())
    if answer.get('error') == 'not allowed':
        try:
            os.remove(KEY_PATH)                         # a wrong key: asked again next time
        except OSError:
            pass
    if answer.get('ok') and op == 'add':
        answer['password'] = body['password']
    return answer


def _admin_key():
    key = saved_key()
    if not key:
        key = getpass.getpass('The server\'s ADMIN_KEY (kept encrypted on this PC): ').strip()
        save_key(key)
    return key


def _password():
    letters = string.ascii_letters + string.digits
    return ''.join(secrets.choice(letters) for _ in range(10))


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ('add', 'list', 'extend', 'reset', 'disable', 'enable', 'remove'):
        sys.exit(__doc__)
    op, user = args[0], args[1] if len(args) > 1 else ''
    body = {'op': op, 'user': user}
    if op in ('add', 'extend'):
        body['days'] = int(args[2]) if len(args) > 2 else 30
    if op == 'add':
        body['password'] = _password()
    try:
        answer = access.call('/admin', body, _admin_key())
    except OSError as e:
        sys.exit(f'the server: {e}')
    if not answer.get('ok'):
        if answer.get('error') == 'not allowed':
            os.remove(KEY_PATH)                         # a wrong key: asked again next time
        sys.exit(f'refused: {answer.get("error")}')
    if op == 'list':
        print(f'{"user":16} {"until":11} {"PC":9} {"last seen":17} off')
        for u in sorted(answer['users'], key=lambda u: u['user'].lower()):
            print(f'{u["user"]:16} {u["expires"]:11} {u["device"] or "-":9} {u["last_seen"] or "-":17} '
                  f'{"yes" if u["disabled"] else ""}')
        return
    if op == 'add':
        print(f'{answer["user"]}: until {answer["expires"]}\n  username: {answer["user"]}\n'
              f'  password: {body["password"]}   (shown only now - give it to them)')
        return
    print(f'{user}: {op} done' + (f' - until {answer["expires"]}' if 'expires' in answer else ''))


if __name__ == '__main__':
    main()
