"""Opens the game and logs an account in, on the current virtual desktop.

  1. start GAME_PATH (Play.exe)            -> wait for play.jpg
  2. click PLAY                            -> wait for the login form (userlogin.jpg)
  3. type username + password, click Login -> logged in once the login form disappears

Run on its own to test:   python login.py [account number]   (default: the first filled-in one)
Add --no-launch to skip step 1 when the launcher is already open."""
import os
import subprocess
import sys
import time

import cv2

import accounts
import game_input as gi
import screen
from _paths import app_root
from game_window import focus_game_window

_DIR = os.path.join(app_root(), 'reference_images')
_tmpl_play = cv2.imread(os.path.join(_DIR, 'play.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_login = cv2.imread(os.path.join(_DIR, 'userlogin.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_login_small = cv2.imread(os.path.join(_DIR, 'userlogin_small.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_hud = cv2.imread(os.path.join(_DIR, 'options_btn.png'), cv2.IMREAD_GRAYSCALE)  # in-game bottom bar

CONF_PLAY  = 0.8
CONF_LOGIN = 0.8

# The login form comes in two layouts (both at 1920x1080). Positions are from each image's
# top-left corner:
#   user  = ~3/4 along the Username box: past any remembered text, clear of the box's edge/label
#   pass  = ~3/4 along the Password box, clear of the show/hide eye icon
#   login = the Login button
FORMS = [
    ('big',   _tmpl_login,       {'user': (185, 70), 'pass': (175, 105), 'login': (176, 218)}),  # 354x282
    ('small', _tmpl_login_small, {'user': (140, 78), 'pass': (130, 105), 'login': (140, 195)}),  # 282x243
]
CLEAR_KEYS     = 40           # backspaces to empty a field that might hold a remembered value

WAIT_PLAY   = 60   # seconds for the launcher's PLAY button to show up
WAIT_FORM   = 60   # seconds for the login form after clicking PLAY
WAIT_ENTER  = 90   # seconds for the login form to go away after clicking Login (usually ~20)
WAIT_LOADED = 90   # seconds after that for the map to load (the bottom bar's Options button shows)
SETTLE      = 3    # extra seconds once loaded, before anything clicks in the game
CONF_HUD    = 0.8


def _find(template, conf):
    return screen.find(screen.grab_gray(), template, conf)


def find_form():
    """(name, (x, y, w, h), positions) of whichever login form layout is showing, or None."""
    gray = screen.grab_gray()
    for name, tmpl, pos in FORMS:
        hit = screen.find(gray, tmpl, CONF_LOGIN)
        if hit:
            return name, hit, pos
    return None


def form_showing():
    return find_form() is not None


def _wait_form(seconds, gone=False):
    """Polls for the login form (either layout); with gone=True, until neither shows."""
    end = time.time() + seconds
    while time.time() < end:
        f = find_form()
        if gone and not f:
            return True
        if not gone and f:
            return f
        time.sleep(0.5)
    return None


def _wait_for(template, conf, seconds, gone=False):
    """Polls every 0.5s until `template` is on screen (or, with gone=True, no longer is).
    Returns its (x, y, w, h) when it appears / True when it's gone, or None on timeout."""
    end = time.time() + seconds
    while time.time() < end:
        found = _find(template, conf)
        if gone and not found:
            return True
        if not gone and found:
            return found
        time.sleep(0.5)
    return None


def _click(x, y):
    focus_game_window()
    gi.click(x, y)
    time.sleep(0.3)


def _fill(box, field_end, text):
    """Clicks at the right end of a field (caret after any saved text), clears it, types `text`."""
    fx, fy = box[0] + field_end[0], box[1] + field_end[1]
    _click(fx, fy)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    gi.type_text(text)


def wait_until_loaded():
    """After the login form goes away the map is still loading; nothing in the game reacts until
    the bottom bar (its Options button) is up. Waits for it, then a few seconds more."""
    if not _wait_for(_tmpl_hud, CONF_HUD, WAIT_LOADED):
        print(f'[LOGIN] the game did not finish loading within {WAIT_LOADED}s (no bottom bar)')
        return False
    time.sleep(SETTLE)
    return True


def _save_debug(layout, box, pos, account):
    """Saves the login form as typed (just before Login is clicked) with a red cross where each
    click went, to logs/login_debug_<account>.png — to see if the clicks hit the boxes and the
    password dots were typed. The password itself only shows as the game's dots."""
    try:
        img = screen.grab_bgr()
        x, y, w, h = box
        pad = 20
        crop = img[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad].copy()
        ox, oy = x - max(0, x - pad), y - max(0, y - pad)
        for name in ('user', 'pass', 'login'):
            px, py = pos[name][0] + ox, pos[name][1] + oy
            cv2.drawMarker(crop, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            cv2.putText(crop, name, (px + 8, py - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        cv2.putText(crop, f'{layout} form, password {len(account.password)} chars',
                    (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        out_dir = os.path.join(app_root(), 'logs')
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f'login_debug_{account.slot}.png')
        cv2.imwrite(path, crop)
        print(f'[LOGIN] debug picture saved: {path}')
    except Exception as e:
        print(f'[LOGIN] could not save the debug picture ({e})')


def launch(game_path):
    """Starts the game launcher on the current desktop."""
    print(f'[LOGIN] starting {game_path}')
    subprocess.Popen([game_path], cwd=os.path.dirname(game_path))


def login(account, launch_first=True):
    """Runs the whole login for `account`. Returns True once the login form has gone away."""
    if launch_first:
        path = accounts.game_path()
        if not path:
            print('[LOGIN] GAME_PATH is not set or the file is missing — cannot start the game')
            return False
        launch(path)

    if not form_showing():                        # the form may already be up (launcher skipped)
        play = _wait_for(_tmpl_play, CONF_PLAY, WAIT_PLAY)
        if not play:
            print(f'[LOGIN] PLAY button did not show up within {WAIT_PLAY}s')
            return False
        print('[LOGIN] clicking PLAY')
        _click(play[0] + play[2] // 2, play[1] + play[3] // 2)

    form = _wait_form(WAIT_FORM)
    if not form:
        print(f'[LOGIN] login form did not show up within {WAIT_FORM}s')
        return False
    layout, box, pos = form
    print(f'[LOGIN] login form ({layout})')

    hwnd = __import__('game_window').find_game_window()
    if hwnd:
        gi.use_english_layout(hwnd)   # so the typed keys come out as Latin letters
        time.sleep(0.2)

    print(f'[LOGIN] typing username {account.username!r} and the password')
    _fill(box, pos['user'], account.username)
    _fill(box, pos['pass'], account.password)
    time.sleep(0.3)
    _save_debug(layout, box, pos, account)
    print('[LOGIN] clicking Login')
    _click(box[0] + pos['login'][0], box[1] + pos['login'][1])

    if _wait_form(WAIT_ENTER, gone=True):
        print(f'[LOGIN] {account.username}: login form gone — waiting for the game to load')
        if not wait_until_loaded():
            return False
        print(f'[LOGIN] {account.username}: in game')
        return True
    print(f'[LOGIN] {account.username}: still on the login form after {WAIT_ENTER}s '
          '(wrong password? server busy?)')
    return False


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    ok, sw, sh = accounts.screen_ok()
    if not ok:
        print(f'[LOGIN] ERROR: the screen is {sw}x{sh}; logging in only works at '
              f'{accounts.REQUIRED_SCREEN[0]}x{accounts.REQUIRED_SCREEN[1]}')
        os._exit(1)
    accts = accounts.load()
    if not accts:
        print('[LOGIN] no accounts — turn on Accounts in the settings and fill one in')
        os._exit(1)
    pick = accts[0]
    if args:
        n = int(args[0])
        pick = next((a for a in accts if a.slot == n), None)
        if not pick:
            print(f'[LOGIN] account {n} is not filled in / enabled')
            os._exit(1)
    ok = login(pick, launch_first='--no-launch' not in sys.argv)
    os._exit(0 if ok else 1)
