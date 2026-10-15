"""Opens the game and logs an account in, on the current virtual desktop.

  1. start GAME_PATH (Play.exe)            -> wait for play.jpg
  2. click PLAY                            -> wait for the login form (userlogin.jpg)
  3. type username + password, click Login -> logged in once the login form disappears

Run on its own to test:   python login.py [account number]   (default: the first filled-in one)
Add --no-launch to skip step 1 when the launcher is already open."""
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time

import cv2

import accounts
import game_input as gi
import game_memory
import screen
from _paths import app_root
from game_window import find_game_window, focus_game_window

_DIR = os.path.join(app_root(), 'reference_images')
_tmpl_play = cv2.imread(os.path.join(_DIR, 'play.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_login = cv2.imread(os.path.join(_DIR, 'userlogin.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_login_small = cv2.imread(os.path.join(_DIR, 'userlogin_small.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_hud = cv2.imread(os.path.join(_DIR, 'options_btn.png'), cv2.IMREAD_GRAYSCALE)  # in-game bottom bar

CONF_PLAY  = 0.8
CONF_LOGIN = 0.75   # the big form scores ~0.80 when scaled to a 1440p screen, 0.95+ at 1080p

# The login screen (the "Login Hub" overlay) grows with the screen: it's laid out for a
# 1080-pixel-high screen and scaled by screen height / 1080. The pictures and positions below
# are at that 1080p size; they're scaled the same way before matching, so any resolution works.
BASE_HEIGHT = 1080

# The login form comes in two layouts. Positions are from each image's top-left corner:
#   user  = ~3/4 along the Username box: past any remembered text, clear of the box's edge/label
#   pass  = ~3/4 along the Password box, clear of the show/hide eye icon
#   login = the Login button
FORMS = [
    ('big',   _tmpl_login,       {'user': (240, 137), 'pass': (230, 178), 'login': (175, 323)}),  # 347x352
    ('small', _tmpl_login_small, {'user': (175, 104), 'pass': (165, 131), 'login': (127, 234)}),  # 254x254
]
CLEAR_KEYS     = 40           # backspaces to empty a field that might hold a remembered value

WAIT_PLAY   = 60   # seconds for the launcher's PLAY button to show up
WAIT_FORM   = 60   # seconds for the login form after clicking PLAY
WAIT_ENTER  = 90   # seconds for the login form to go away after clicking Login (usually ~20)
WAIT_LOADED = 90   # seconds after that for the map to load (the bottom bar's Options button shows)
SETTLE      = 3    # extra seconds once loaded, before anything clicks in the game
CRASH_GRACE = 10   # game window gone this long (once the login form was up) = it crashed

# Fallback when the pictures stop matching (e.g. the login screen got a new look in an update):
# the login screen is a Dear ImGui window drawn by VERSION.dll on its own window. Its login part
# ("##hubLogin": username, password, Login) is found in the game's memory - read only - then
# filled from the keyboard: click its caption, Tab -> username, Tab -> password, Enter (logs in).
HUB_CLASS     = 'ClassicLordsLoginOverlayWnd'   # the login screen's own window
HUB_LOGIN_BOX = '##hubLogin'                    # its ImGui child window holding the login fields
MEMORY_AFTER  = 15   # seconds of no picture match (with the login screen up) before the fallback
MEMORY_EVERY  = 5    # seconds between fallback attempts
CONF_HUD    = 0.8


class GameCrashed(Exception):
    """The game window went away during the login — it crashed or closed."""


class _GameWatch:
    """Armed once the login form is up (the game's own window): from then on, if no game window
    is on this desktop for CRASH_GRACE seconds in a row, the game crashed. The grace covers the
    game swapping to a new process right after Login (its window is gone for a moment)."""

    def __init__(self):
        self.missing_since = None

    def check(self):
        if find_game_window():
            self.missing_since = None
        elif self.missing_since is None:
            self.missing_since = time.time()
        elif time.time() - self.missing_since >= CRASH_GRACE:
            raise GameCrashed()


_watch = None   # the running login's _GameWatch (None until its form is up)


def _check_game():
    if _watch:
        _watch.check()


def _find(template, conf):
    return screen.find(screen.grab_gray(), template, conf)


_scaled = {}   # screen height -> FORMS with pictures and positions scaled to it


def _forms_for(height):
    if height not in _scaled:
        s = height / BASE_HEIGHT
        forms = []
        for name, tmpl, pos in FORMS:
            if tmpl is not None and abs(s - 1) > 0.01:
                tmpl = cv2.resize(tmpl, None, fx=s, fy=s,
                                  interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            forms.append((name, tmpl, {k: (round(x * s), round(y * s)) for k, (x, y) in pos.items()}))
        _scaled[height] = forms
    return _scaled[height]


def find_form():
    """(name, (x, y, w, h), positions) of whichever login form layout is showing, or None —
    found at the screen's own scale, with positions scaled to match."""
    gray = screen.grab_gray()
    for name, tmpl, pos in _forms_for(gray.shape[0]):
        hit = screen.find(gray, tmpl, CONF_LOGIN)
        if hit:
            return name, hit, pos
    return None


def form_showing():
    return find_form() is not None


def _hub_window():
    """The login screen's window (HUB_CLASS) when it's showing on this desktop, else None."""
    import desktops
    found = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _each(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, name, 64)
        if name.value == HUB_CLASS and ctypes.windll.user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(cb(_each), 0)
    return next((h for h in found if desktops.on_current_desktop(h)), None)


def _memory_form():
    """The fallback: ('memory', (x, y, w, h) on screen of the login part, None), or None."""
    hub = _hub_window()
    if not hub:
        return None
    rect = game_memory.imgui_window(hub, HUB_LOGIN_BOX)
    if not rect:
        return None
    x, y, w, h = rect
    pt = ctypes.wintypes.POINT(x, y)
    ctypes.windll.user32.ClientToScreen(hub, ctypes.byref(pt))   # ImGui coords -> screen
    return 'memory', (pt.x, pt.y, w, h), None


def _wait_form(seconds, gone=False):
    """Polls for the login form (either layout); with gone=True, until neither shows. When the
    pictures don't match for MEMORY_AFTER seconds while the login screen is up, falls back to
    finding it in the game's memory (_memory_form)."""
    start = time.time()
    end = start + seconds
    tried = 0
    while time.time() < end:
        _check_game()
        f = find_form()
        if gone and not f:
            return True
        if not gone and f:
            return f
        if not gone and time.time() - start >= MEMORY_AFTER and time.time() - tried >= MEMORY_EVERY:
            tried = time.time()
            m = _memory_form()
            if m:
                print(f'[LOGIN] the login pictures do not match — found the login box in memory at {m[1]}')
                return m
        time.sleep(0.5)
    return None


def _wait_for(template, conf, seconds, gone=False):
    """Polls every 0.5s until `template` is on screen (or, with gone=True, no longer is).
    Returns its (x, y, w, h) when it appears / True when it's gone, or None on timeout."""
    end = time.time() + seconds
    while time.time() < end:
        _check_game()
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


def _fill(box, field_end, text, secret=False):
    """Clicks at the right end of a field (caret after any saved text), clears it, types `text`.
    Returns how many characters were typed."""
    fx, fy = box[0] + field_end[0], box[1] + field_end[1]
    _click(fx, fy)
    # the login screen is its own window (ClassicLordsLoginOverlayWnd), not the game's: switch the
    # window actually in front to US English, or the keys come out in the PC's layout (e.g. Arabic)
    gi.use_english_layout(ctypes.windll.user32.GetForegroundWindow())
    time.sleep(0.2)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    return gi.type_text(text, secret=secret)


def _fill_by_keyboard(box, account):
    """The fallback fill: no positions needed besides the login box itself. Clicks its caption
    (top edge, not a control), then Tab -> username, Tab -> password (Tab selects what's there),
    Enter to log in."""
    x, y, w, h = box
    _click(x + w // 2, y + 6)
    gi.use_english_layout(ctypes.windll.user32.GetForegroundWindow())
    time.sleep(0.2)
    gi.press('tab')
    time.sleep(0.2)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    n = gi.type_text(account.username)
    print(f'[LOGIN] username typed ({n}/{len(account.username)} chars)')
    gi.press('tab')
    time.sleep(0.2)
    gi.press('backspace', CLEAR_KEYS, delay=0.01)
    n = gi.type_text(account.password, secret=True)
    print(f'[LOGIN] password typed ({n}/{len(account.password)} chars)')
    print('[LOGIN] pressing Enter')
    gi.press('enter')


def wait_until_loaded():
    """After the login form goes away the map is still loading; nothing in the game reacts until
    the bottom bar (its Options button) is up. Waits for it, then a few seconds more."""
    if not _wait_for(_tmpl_hud, CONF_HUD, WAIT_LOADED):
        print(f'[LOGIN] the game did not finish loading within {WAIT_LOADED}s (no bottom bar)')
        return False
    time.sleep(SETTLE)
    return True


def launch(game_path):
    """Starts the game launcher on the current desktop."""
    print(f'[LOGIN] starting {game_path}')
    subprocess.Popen([game_path], cwd=os.path.dirname(game_path))


def login(account, launch_first=True):
    """Runs the whole login for `account`. Returns True once it's in the game. If the game
    crashes on the way (its window disappears), gives up at once so the loop can move on to
    the next desktop; this one is tried again later."""
    global _watch
    try:
        return _login(account, launch_first)
    except GameCrashed:
        print(f'[LOGIN] {account.username}: the game crashed / closed during the login — '
              'skipping this desktop, it is tried again later')
        return False
    finally:
        _watch = None


def _login(account, launch_first):
    global _watch
    if launch_first:
        path = accounts.game_path()
        if not path:
            print('[LOGIN] GAME_PATH is not set or the file is missing — cannot start the game')
            return False
        import kill_reply, kill_watch, notify, revive
        if kill_reply.enabled() or revive.REVIVE_TIME_MULTIPLIER > 1 or notify.enabled() or revive.MONSTERS:
            kill_watch.ensure_chat_log(path)   # before login: the game reads it then
        launch(path)

    if not form_showing() and not _hub_window():  # the form may already be up (launcher skipped)
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
    _watch = _GameWatch()   # from here on the game window must stay (else it crashed)

    if layout == 'memory':
        _fill_by_keyboard(box, account)
        return _after_login(account)

    hwnd = __import__('game_window').find_game_window()
    if hwnd:
        gi.use_english_layout(hwnd)   # so the typed keys come out as Latin letters
        time.sleep(0.2)

    # password first, then username
    if not account.password:
        print('[LOGIN] no password for this account — skipping the password box')
    else:
        print(f'[LOGIN] typing password ({len(account.password)} chars)')
        n = _fill(box, pos['pass'], account.password, secret=True)
        print(f'[LOGIN] password typed ({n}/{len(account.password)} chars)'
              + ('' if n == len(account.password) else ' — some characters could not be typed'))
    print(f'[LOGIN] typing username {account.username!r}')
    n = _fill(box, pos['user'], account.username)
    print(f'[LOGIN] username typed ({n}/{len(account.username)} chars)')
    print('[LOGIN] clicking Login')
    _click(box[0] + pos['login'][0], box[1] + pos['login'][1])
    return _after_login(account)


def _after_login(account):
    """Waits for the login screen to go and the map to load. True once in the game."""
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
