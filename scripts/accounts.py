"""Game accounts from the 'accounts' settings section: 4 or more (the settings page's "+"). The filled-in accounts run one
per virtual desktop, in order (the first filled-in account on desktop 1, the next on desktop 2...).
Passwords are stored DPAPI-encrypted (see secret.py) and only decrypted here, when needed."""
import os
from dataclasses import dataclass

import secret
from dragon_settings import account_slots, get_settings

_DEFAULTS = {'ENABLED': False, 'GAME_PATH': ''}


def _defaults(slots):
    out = dict(_DEFAULTS)
    for i in range(1, slots + 1):
        out[f'ACCOUNT_{i}_USER'] = ''
        out[f'ACCOUNT_{i}_PASS'] = ''
        out[f'ACCOUNT_{i}_NAME'] = ''
    return out


@dataclass
class Account:
    slot: int        # 1, 2... — its row in the settings
    desktop: int     # 1-based virtual desktop it runs on (its position among the filled-in ones)
    username: str
    password: str    # decrypted
    name: str = ''   # in-game character name (as it shows in the chat), '' if not filled in

    def __repr__(self):  # never print the password into the log
        return (f'Account(slot={self.slot}, desktop={self.desktop}, username={self.username!r}, '
                f'name={self.name!r})')


def enabled():
    return bool(get_settings('accounts', _DEFAULTS)['ENABLED'])


def load():
    """Configured accounts (username + a password that decrypts), in slot order. Slots with no
    username are skipped. Empty if the feature is off."""
    slots = account_slots()
    s = get_settings('accounts', _defaults(slots))
    if not s['ENABLED']:
        return []
    out = []
    for i in range(1, slots + 1):
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
        name = (s[f'ACCOUNT_{i}_NAME'] or '').strip()
        out.append(Account(i, len(out) + 1, user, password, name))
    return out


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
