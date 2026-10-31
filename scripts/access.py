"""The Auto features (Auto hunt, Monk hunt, Auto heal, Mail & Bank Compose) are for the users
on the server (worker/license_worker.js, a Cloudflare Worker) - Static is for everyone.

activate(user, password) asks the server once, from the panel's Activate Auto Features; the login
is then kept on this PC (the password DPAPI-encrypted, this Windows user only) and asked again at
every start and every RECHECK seconds - a user turned off / expired / used on another PC stops.
No answer from the server (no internet): the last good one counts for GRACE seconds.
The server knows this PC by a hash of its Windows MachineGuid (device()).

    python access.py check           the saved login, asked again"""
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import secret
from dragon_settings import override_dir

SERVER  = 'https://dragon-users.ayman-abolilaa.workers.dev'   # the Worker's address
PATH    = os.path.join(override_dir(), 'license.json')
RECHECK = 6 * 3600          # seconds between asks of the server while static runs
GRACE   = 3 * 24 * 3600     # no answer from the server: the last good one counts this long
TIMEOUT = 10

_lock = threading.Lock()
_state = {'active': False, 'user': '', 'expires': '', 'message': ''}


def device():
    """This PC, as the server knows it: a hash of its Windows MachineGuid."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            guid = winreg.QueryValueEx(key, 'MachineGuid')[0]
    except OSError:
        guid = os.environ.get('COMPUTERNAME', 'pc')
    return hashlib.sha256(f'dragon-co2|{guid}'.encode()).hexdigest()[:32]


def call(path, body, admin_key=None):
    """POSTs `body` to the server -> its JSON answer. Raises OSError with no answer."""
    request = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(), method='POST',
                                     headers={'content-type': 'application/json',
                                              'user-agent': 'dragon-co2'})
    if admin_key:
        request.add_header('authorization', f'Bearer {admin_key}')
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except ValueError:
            raise OSError(f'server answered {e.code}')
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise OSError(f'no answer from the server ({e})')


def _saved():
    try:
        with open(PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(override_dir(), exist_ok=True)
    with open(PATH + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    os.replace(PATH + '.tmp', PATH)


def _set(active, user='', expires='', message=''):
    with _lock:
        _state.update(active=active, user=user, expires=expires, message=message)


def state():
    with _lock:
        return dict(_state)


def active():
    with _lock:
        return _state['active']


def activate(user, password):
    """Asks the server; kept on this PC if it says yes. -> (ok, message)"""
    try:
        answer = call('/check', {'user': user.strip(), 'password': password, 'device': device()})
    except OSError as e:
        return False, str(e)
    if not answer.get('ok'):
        return False, answer.get('error') or 'refused'
    _save({'user': answer['user'], 'pass': secret.protect(password), 'expires': answer['expires'],
           'last_ok': time.time()})
    _set(True, answer['user'], answer['expires'])
    print(f'[ACCESS] {answer["user"]}: Auto features on (until {answer["expires"]})')
    return True, ''


def sign_out():
    try:
        os.remove(PATH)
    except OSError:
        pass
    _set(False, message='signed out')
    print('[ACCESS] signed out - Auto features off')


def check():
    """The saved login, asked again (at start, every RECHECK). -> active"""
    saved = _saved()
    if not saved.get('user'):
        _set(False)
        return False
    password = secret.unprotect(saved.get('pass') or '')
    if not password:
        _set(False, message='Activate again on this PC')
        return False
    try:
        answer = call('/check', {'user': saved['user'], 'password': password, 'device': device()})
    except OSError as e:
        if time.time() - saved.get('last_ok', 0) < GRACE:      # offline: the last good answer
            _set(True, saved['user'], saved.get('expires', ''), 'server not reached - working offline')
            return True
        _set(False, saved['user'], message=str(e))
        return False
    if not answer.get('ok'):
        _set(False, saved['user'], message=answer.get('error') or 'refused')
        print(f'[ACCESS] {saved["user"]}: {answer.get("error")} - Auto features off')
        return False
    saved.update(expires=answer['expires'], last_ok=time.time())
    _save(saved)
    _set(True, answer['user'], answer['expires'])
    return True


def keep_checking():
    """At start and every RECHECK seconds - on a thread of its own."""
    def loop():
        while True:
            try:
                check()
            except Exception as e:
                print(f'[ACCESS] {type(e).__name__}: {e}')
            time.sleep(RECHECK)
    threading.Thread(target=loop, daemon=True, name='access').start()


if __name__ == '__main__':
    if sys.argv[1:] == ['check']:
        print(check(), state())
