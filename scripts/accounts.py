"""Game accounts from the 'accounts' settings section: up to 4. The filled-in accounts run one
per virtual desktop, in order (the first filled-in account on desktop 1, the next on desktop 2...).
Passwords are stored DPAPI-encrypted (see secret.py) and only decrypted here, when needed."""
import os
from dataclasses import dataclass

import secret
from dragon_settings import get_settings

MAX_ACCOUNTS = 4
REQUIRED_SCREEN = (1920, 1080)  # the login pictures only match at this resolution

_DEFAULTS = {'ENABLED': False, 'GAME_PATH': ''}
for _i in range(1, MAX_ACCOUNTS + 1):
    _DEFAULTS[f'ACCOUNT_{_i}_USER'] = ''
    _DEFAULTS[f'ACCOUNT_{_i}_PASS'] = ''


@dataclass
class Account:
    slot: int        # 1..4 — its row in the settings
    desktop: int     # 1-based virtual desktop it runs on (its position among the filled-in ones)
    username: str
    password: str    # decrypted

    def __repr__(self):  # never print the password into the log
        return f'Account(slot={self.slot}, desktop={self.desktop}, username={self.username!r})'


def enabled():
    return bool(get_settings('accounts', _DEFAULTS)['ENABLED'])


def load():
    """Configured accounts (username + a password that decrypts), in slot order. Slots with no
    username are skipped. Empty if the feature is off."""
    s = get_settings('accounts', _DEFAULTS)
    if not s['ENABLED']:
        return []
    out = []
    for i in range(1, MAX_ACCOUNTS + 1):
        user = (s[f'ACCOUNT_{i}_USER'] or '').strip()
        if not user:
            continue
        stored = s[f'ACCOUNT_{i}_PASS']
        if not stored:
            print(f'[ACCOUNTS] account {i} ({user}): no password saved — skipped')
            continue
        password = secret.unprotect(stored)
        if not password:
            print(f'[ACCOUNTS] account {i} ({user}): could not decrypt the saved password (saved on '
                  'another PC or Windows user?) — skipped; set it again in the settings on this PC')
            continue
        print(f'[ACCOUNTS] account {i} ({user}): password decrypted ({len(password)} chars)')
        out.append(Account(i, len(out) + 1, user, password))
    return out


def screen_ok():
    """(ok, width, height) of the primary screen against REQUIRED_SCREEN."""
    import mss
    with mss.MSS() as sct:
        mon = next((m for m in sct.monitors[1:] if m.get('is_primary')), sct.monitors[1])
    w, h = mon['width'], mon['height']
    return (w, h) == REQUIRED_SCREEN, w, h


def game_path():
    """The game launcher chosen in the settings ('' if not set or the file isn't there)."""
    path = (get_settings('accounts', _DEFAULTS)['GAME_PATH'] or '').strip().strip('"')
    if path and not os.path.isfile(path):
        print(f'[ACCOUNTS] game launcher not found: {path}')
        return ''
    return path


def sync_desktops(count):
    """Makes the number of virtual desktops exactly `count`: creates the missing ones, removes the
    extra ones from the end (their windows move to desktop 1). Returns the resulting count."""
    import desktops
    have = desktops.count()
    if have < count:
        for _ in range(count - have):
            desktops.create()
        print(f'[ACCOUNTS] created {count - have} desktop(s) — now {count}')
    elif have > count:
        for n in range(have, count, -1):
            desktops.remove(n)
        print(f'[ACCOUNTS] removed {have - count} extra desktop(s) — now {count}')
    return desktops.count()
