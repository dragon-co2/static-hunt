"""Turns on the game's own auto clicker ("auto hunt") after login.

  0. pick the Scatter skill: click Skill (skill.jpg) -> Scatter (scatter.jpg) shows;
     click Scatter -> the Skill button goes away (skipped if Skill isn't showing: already picked)
  1. click Options (options_btn.png)               -> wait for the Settings window (options.jpg)
     tick "Full screen" if it isn't (the screen re-lays out, so the window is looked up again)
  2. click Clear                                   -> forget any points from an earlier session
  3. for each of the window's 4 corners: click Set point, then right-click 20px outside that corner
  4. tick "Auto right click" (only if it isn't ticked already — clicking a ticked box unticks it)
  5. close the window with its X

Run on its own to test:   python auto_hunt.py   (steps 0-5)"""
import os
import time

import cv2

import game_input as gi
import screen
from _paths import app_root
from game_window import focus_game_window

_DIR = os.path.join(app_root(), 'reference_images')
_tmpl_btn = cv2.imread(os.path.join(_DIR, 'options_btn.png'), cv2.IMREAD_GRAYSCALE)
_tmpl_skill = cv2.imread(os.path.join(_DIR, 'skill.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_scatter = cv2.imread(os.path.join(_DIR, 'scatter.jpg'), cv2.IMREAD_GRAYSCALE)
_win = cv2.imread(os.path.join(_DIR, 'options.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_top = _win[:30] if _win is not None else None   # "Settings" title + X: never changes
WIN_W, WIN_H = (_win.shape[1], _win.shape[0]) if _win is not None else (0, 0)

CONF_BTN = 0.8
CONF_WIN = 0.9
CONF_SKILL = 0.8
STEP_TRIES = 3   # clicks per step before giving up on its check

# inside options.jpg (939x405), from its top-left corner
SET_POINT  = (559, 135)
CLEAR      = (640, 135)
AUTO_CHECK = (519, 74)            # the "Auto right click" checkbox
CHECK_BOX  = (511, 66, 16, 16)    # x, y, w, h of that checkbox, to read whether it's ticked
FULLSCREEN_CHECK = (19, 211)      # the "Full screen" checkbox
FULLSCREEN_BOX   = (11, 203, 16, 16)
CLOSE_X    = (918, 15)
CORNER_OFFSET = 20                # right-click this far outside each corner
STATUS_BOX = (508, 102, 200, 20)  # x, y, w, h of the "No point set" / "waiting" / "2/4 points set" line
TEXT_LEVEL = 120                  # status text is bright; the see-through background behind it is darker
TEXT_CHANGED = 25                 # text pixels that must differ for the line to count as changed
POINT_TRIES = 3                   # attempts per point (Set point + right-click) before giving up
TICKED_MIN_BRIGHT = 8             # bright (>150) pixels in the box when ticked (~19) vs not (0)


def _find_window():
    return screen.find(screen.grab_gray(), _tmpl_top, CONF_WIN)


def _wait(fn, seconds, want=True):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if bool(v) == want:
            return v or True
        time.sleep(0.3)
    return None


def _left(x, y):
    focus_game_window()
    gi.click(x, y)
    time.sleep(0.4)


def _right(x, y):
    focus_game_window()
    gi.move(x, y)
    time.sleep(0.05)
    gi.down('right')
    time.sleep(0.1)
    gi.up('right')
    time.sleep(0.4)


def _status_text(win):
    """The points status line as a black/white mask of its text only — the window is see-through,
    so the moving game behind it must not count as a change."""
    x, y, w, h = STATUS_BOX
    crop = screen.grab_gray()[win[1] + y:win[1] + y + h, win[0] + x:win[0] + x + w]
    return crop > TEXT_LEVEL


def _text_diff(a, b):
    return int((a != b).sum())


_NO_POINTS = None  # the "No point set" line, from options.jpg


def _no_points_text():
    global _NO_POINTS
    if _NO_POINTS is None:
        x, y, w, h = STATUS_BOX
        _NO_POINTS = _win[y:y + h, x:x + w] > TEXT_LEVEL
    return _NO_POINTS


def _wait_text(win, check, seconds):
    """Polls the status line until check(text) is true. Returns the text then, or None."""
    end = time.time() + seconds
    while time.time() < end:
        t = _status_text(win)
        if check(t):
            return t
        time.sleep(0.25)
    return None


def _clear_points(win):
    """Clicks Clear until the status line reads "No point set"."""
    wx, wy = win[0], win[1]
    for _ in range(POINT_TRIES):
        _left(wx + CLEAR[0], wy + CLEAR[1])
        if _wait_text(win, lambda t: _text_diff(t, _no_points_text()) < TEXT_CHANGED, 3) is not None:
            print('[HUNT] old points cleared ("No point set")')
            return True
    print('[HUNT] Clear — status line never read "No point set"')
    return False


def _set_point(win, cx, cy):
    """Set point, then right-click (cx, cy) — each checked on the status line: Set point must change
    it (to "waiting"), the right-click must change it again (to "N/4 points set"). A step the game
    missed (lag) is redone. Returns True when the point registered."""
    wx, wy = win[0], win[1]
    for attempt in range(1, POINT_TRIES + 1):
        before = _status_text(win)
        _left(wx + SET_POINT[0], wy + SET_POINT[1])
        waiting = _wait_text(win, lambda t: _text_diff(t, before) >= TEXT_CHANGED, 3)
        if waiting is None:
            print(f'[HUNT]   Set point click not taken (try {attempt}/{POINT_TRIES})')
            continue
        _right(cx, cy)
        if _wait_text(win, lambda t: _text_diff(t, waiting) >= TEXT_CHANGED, 3) is not None:
            return True
        print(f'[HUNT]   right-click not taken — still waiting (try {attempt}/{POINT_TRIES})')
    return False


def _ticked(win, box=CHECK_BOX):
    x, y, w, h = box
    crop = screen.grab_gray()[win[1] + y:win[1] + y + h, win[0] + x:win[0] + x + w]
    return int((crop > 150).sum()) >= TICKED_MIN_BRIGHT


def _open_settings():
    """The Settings window's (x, y, w, h), opening it with the Options button if needed."""
    win = _find_window()
    if win:
        return win
    btn = screen.find(screen.grab_gray(), _tmpl_btn, CONF_BTN)
    if not btn:
        print('[HUNT] Options button not found')
        return None
    print('[HUNT] opening Options')
    _left(btn[0] + btn[2] // 2, btn[1] + btn[3] // 2)
    win = _wait(_find_window, 10)
    if not win:
        print('[HUNT] Settings window did not open')
    return win


def _ensure_fullscreen(win):
    """Ticks "Full screen" if it isn't. Switching re-lays out the screen and can move (or close) the
    Settings window, so it's found again afterwards. Returns the window's current position."""
    if _ticked(win, FULLSCREEN_BOX):
        print('[HUNT] Full screen already on')
        return win
    _left(win[0] + FULLSCREEN_CHECK[0], win[1] + FULLSCREEN_CHECK[1])
    time.sleep(3)   # the game switches modes
    win = _open_settings()
    if win:
        print('[HUNT] Full screen ' + ('on' if _ticked(win, FULLSCREEN_BOX) else 'did NOT tick — check it'))
    return win


def _on_screen(template, conf=CONF_SKILL):
    return screen.find(screen.grab_gray(), template, conf)


def _click_until(template, check, label):
    """Clicks the center of `template` until check() is true (up to STEP_TRIES clicks)."""
    for _ in range(STEP_TRIES):
        at = _on_screen(template)
        if not at:
            break
        _left(at[0] + at[2] // 2, at[1] + at[3] // 2)
        if _wait(check, 5):
            print(f'[HUNT] {label}')
            return True
    print(f'[HUNT] {label} — FAILED')
    return False


def select_scatter():
    """Step 0: Skill -> Scatter. Returns True when Scatter is picked (or already was)."""
    if not _on_screen(_tmpl_skill):
        if not _on_screen(_tmpl_btn, CONF_BTN):   # no bottom bar at all: the game isn't ready
            print('[HUNT] bottom bar not showing (game still loading?) — cannot pick Scatter')
            return False
        print('[HUNT] Skill button not showing — Scatter already picked')
        return True
    if not _click_until(_tmpl_skill, lambda: _on_screen(_tmpl_scatter), 'Skill clicked — Scatter shows'):
        return False
    return _click_until(_tmpl_scatter, lambda: not _on_screen(_tmpl_skill), 'Scatter picked — Skill gone')


def start_hunting():
    """Steps 0-5: pick Scatter, then set up and switch on the auto clicker."""
    if not select_scatter():
        return False
    return setup_auto_clicker()


def setup_auto_clicker():
    """Runs steps 1-5. Returns True if the auto clicker ends up ticked and the window closed."""
    win = _open_settings()
    if not win:
        return False
    win = _ensure_fullscreen(win)   # before the points: full screen moves the window
    if not win:
        return False
    wx, wy = win[0], win[1]

    if not _clear_points(win):
        return False

    sw, sh = gi.pyautogui.size()
    o = CORNER_OFFSET
    corners = (('top-left', wx - o, wy - o), ('top-right', wx + WIN_W + o, wy - o),
               ('bottom-right', wx + WIN_W + o, wy + WIN_H + o), ('bottom-left', wx - o, wy + WIN_H + o))
    for n, (name, cx, cy) in enumerate(corners, 1):
        cx, cy = min(max(cx, 0), sw - 1), min(max(cy, 0), sh - 1)
        if not _set_point(win, cx, cy):
            print(f'[HUNT] point {n}/4 ({name}) did not register after {POINT_TRIES} tries — stopping')
            return False
        print(f'[HUNT] point {n}/4 set — {name} @ ({cx},{cy})')

    if _ticked(win):
        print('[HUNT] Auto right click already on')
    else:
        _left(wx + AUTO_CHECK[0], wy + AUTO_CHECK[1])
        print('[HUNT] Auto right click ' + ('on' if _ticked(win) else 'did NOT tick — check it'))
    on = _ticked(win)

    _left(wx + CLOSE_X[0], wy + CLOSE_X[1])
    closed = _wait(_find_window, 5, want=False)
    print('[HUNT] Settings window ' + ('closed' if closed else 'still open'))
    return bool(on and closed)


if __name__ == '__main__':
    ok = start_hunting()
    os._exit(0 if ok else 1)
