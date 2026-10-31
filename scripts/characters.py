"""Each character's own options - set in the control panel (panel.py), kept in
Documents\\DragonCO2\\characters.json:
    name        the character (read from its game)
    user, pass  its login - the password DPAPI-encrypted (secret.py), this Windows user only
    auto_login  logged in by the script (Log in, and again after a crash / disconnect)
    f10         the F10 hotkey (an XP skill) every 2 s while Static / Auto hunt runs
    mail_to     the character its compose items are mailed to ('' - none)
    mail_cps    ...and its CPs with them, all but cps_keep
    send_to     the Mail & Bank Compose mode's Send to
The first time, it's filled from the old settings (Accounts, F10, Mail compose items)."""
import json
import os
import threading

import secret
from dragon_settings import _load_json, override_dir, override_path

PATH = os.path.join(override_dir(), 'characters.json')
NAMES_PATH = os.path.join(override_dir(), 'account_characters.json')   # username -> character (old)
DEFAULT = {'name': '', 'user': '', 'pass': '', 'auto_login': False, 'f10': False,
           'mail_to': '', 'mail_cps': True, 'cps_keep': 5000, 'send_to': ''}

_lock = threading.RLock()
_data = None        # key -> profile


def key_of(name='', user=''):
    return name.lower() if name else f'user:{user.lower()}'


def _load():
    global _data
    if _data is not None:
        return _data
    if os.path.exists(PATH):
        raw = _load_json(PATH)
    else:
        raw = _from_old_settings()
    _data = {}
    for p in raw.values() if isinstance(raw, dict) else []:
        p = {**DEFAULT, **p}
        if p['name'] or p['user']:
            _data[key_of(p['name'], p['user'])] = p
    if not os.path.exists(PATH):
        _save()
        if _data:
            print(f'[CHARACTERS] {len(_data)} character(s) taken from the old settings: '
                  + ', '.join(p['name'] or p['user'] for p in _data.values()))
    return _data


def _from_old_settings():
    """The old Accounts slots, F10 and Mail compose settings - as profiles."""
    overrides = _load_json(override_path())
    accts, static = overrides.get('accounts', {}), overrides.get('static', {})
    names = _load_json(NAMES_PATH)
    mail_to = (static.get('MAIL_TO') or '').strip() if static.get('MAIL_COMPOSE') else ''
    out = {}
    for i in range(1, 5):
        user = (accts.get(f'ACCOUNT_{i}_USER') or '').strip()
        if not user:
            continue
        name = names.get(user, '')
        password = accts.get(f'ACCOUNT_{i}_PASS') or ''
        out[key_of(name, user)] = {
            'name': name, 'user': user, 'pass': password,
            'auto_login': bool(accts.get('ENABLED')) and bool(password),
            'f10': bool(static.get('F10_CYCLONE')),
            'mail_to': mail_to if mail_to.lower() != name.lower() else '',
            'mail_cps': bool(static.get('MAIL_CPS', True)), 'cps_keep': int(static.get('CPS_KEEP', 5000))}
    return out


def _save():
    try:
        os.makedirs(override_dir(), exist_ok=True)
        tmp = PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_data, f, indent=1)
        os.replace(tmp, PATH)
    except OSError as e:
        print(f'[CHARACTERS] could not save {PATH} ({e})')


def all_keys():
    with _lock:
        return list(_load())


def get(key):
    """A copy of the profile (DEFAULT if there's none)."""
    with _lock:
        return dict(_load().get(key) or DEFAULT)


def ensure(name):
    """The key of `name`'s profile - made if it's new."""
    with _lock:
        data, key = _load(), key_of(name)
        if key not in data:
            data[key] = {**DEFAULT, 'name': name}
            _save()
        return key


def update(key, **fields):
    with _lock:
        data = _load()
        if key in data:
            data[key].update({k: v for k, v in fields.items() if k in DEFAULT})
            _save()


def set_login(key, user, password):
    """The login: `password` encrypted; an empty one keeps the saved one."""
    with _lock:
        p = _load().get(key)
        if p is None:
            return
        p['user'] = user.strip()
        if password:
            p['pass'] = secret.protect(password)
        _save()


def named(key, name):
    """The character's name is known now (a profile made from a username) -> its new key."""
    with _lock:
        data = _load()
        new = key_of(name)
        if key == new or key not in data:
            return new if new in data else key
        p = data.pop(key)
        p['name'] = name
        data[new] = {**data.get(new, {}), **{k: v for k, v in p.items() if v or k not in data.get(new, {})}}
        _save()
        return new


def forget(key):
    with _lock:
        if _load().pop(key, None) is not None:
            _save()


def password(key):
    """The decrypted password ('' if none / it can't be decrypted on this PC)."""
    stored = get(key)['pass']
    return secret.unprotect(stored) if stored else ''
