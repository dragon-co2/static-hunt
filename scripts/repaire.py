import os
import time
import ctypes
import ctypes.wintypes
import numpy as np
import cv2
import mss
import pyautogui

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
WAREHOUSE_TITLE_PATH = os.path.join(_DIR, 'warehouse_title.jpg')
STATUS_PATH          = os.path.join(_DIR, 'status.png')
BODY_PATH            = os.path.join(_DIR, 'body.png')
REPAIR_PATH          = os.path.join(_DIR, 'repair.png')
YES_PATH             = os.path.join(_DIR, 'yes.png')
CLOSE_STATUS_PATH    = os.path.join(_DIR, 'closeStatus.png')

from dragon_settings import get_settings
_S = get_settings('repaire', {
    'WH_CLOSE_OFF': (337, 17),
    'REPAIR_OFF':   (160, -109),
    'CONF_REPAIR_NEAR': 0.5,
    'CONF_WH':      0.5,
    'WAIT':         0.5,
    'RETRIES':      15,
})

WH_CLOSE_OFF = tuple(_S['WH_CLOSE_OFF'])   # X button, relative to the warehouse panel's top-left
REPAIR_OFF   = tuple(_S['REPAIR_OFF'])     # Repair button center, relative to the Body button's center
CONF_REPAIR_NEAR = _S['CONF_REPAIR_NEAR']  # looser match for repair.png at its expected spot (it's often half-covered)

CONF_WH  = _S['CONF_WH']
WAIT     = _S['WAIT']       # seconds between steps
RETRIES  = _S['RETRIES']    # attempts (1s apart) to wait for each UI element to appear
TRIALS   = 5                # attempts per step before giving up on validating it
ALT_P_HOLD = 1.0            # seconds to hold Alt+P down when opening the remote warehouse
CLICK_HOLD = 0.1            # seconds to hold the left mouse button down on every click

MEM_WINDOW = 'GhostArrow'  # partial game window title, same as navigation.py

_tmpl_wh = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)

_wait_if_dead = None  # optional callback set by run_repair(); polled to pause mid-step during a revive


def _check_alive():
    if _wait_if_dead:
        _wait_if_dead()


def _focus_game_window():
    found_hwnd = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        if MEM_WINDOW.lower() in buf.value.lower():
            found_hwnd.value = hwnd
            return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    if found_hwnd.value:
        ctypes.windll.user32.ShowWindow(found_hwnd.value, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(found_hwnd.value)
        time.sleep(0.2)
        return True
    return False


def _grab_primary_gray():
    """Grayscale capture of the primary monitor. mss's monitors[1] isn't necessarily the primary
    one on multi-monitor setups, so pick it by `is_primary` (primary sits at 0,0 = click coords)."""
    with mss.MSS() as sct:
        mon = next((m for m in sct.monitors[1:] if m.get('is_primary')), sct.monitors[1])
        return cv2.cvtColor(np.array(sct.grab(mon)), cv2.COLOR_BGRA2GRAY)


def _find_warehouse(gray):
    """Returns (x, y, w, h) of the warehouse panel, or None."""
    if _tmpl_wh is None:
        return None
    th, tw = _tmpl_wh.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, _tmpl_wh, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    return (loc[0], loc[1], tw, th) if val >= CONF_WH else None


def _best_match(path):
    """Returns (x, y, w, h, score) of the best on-screen match for `path`, regardless of confidence."""
    tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tmpl is None:
        return None
    gray = _grab_primary_gray()
    th, tw = tmpl.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    return (loc[0], loc[1], tw, th, float(val))


def _center(path, gray, confidence):
    """Center of the best match for `path` in `gray` if it scores >= confidence, else None."""
    tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if tmpl is None:
        return None
    th, tw = tmpl.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    _, val, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED))
    return (loc[0] + tw // 2, loc[1] + th // 2) if val >= confidence else None


def _repair_pos():
    """Where to click Repair: a confident on-screen match of repair.png if there is one
    (still right if the UI ever moves), otherwise the Body button's center + REPAIR_OFF
    (works even while the character model covers the button). Returns ((x, y), how) or None."""
    gray = _grab_primary_gray()
    found = _center(REPAIR_PATH, gray, 0.8)
    if found:
        return found, 'found repair.png'
    body = _center(BODY_PATH, gray, 0.8)
    if body:
        return (body[0] + REPAIR_OFF[0], body[1] + REPAIR_OFF[1]), 'Body + offset'
    return None


def _repair_ready():
    """Body tab is showing: repair.png matches confidently anywhere, or at least loosely
    right where Body + REPAIR_OFF says it should be (it's often half-hidden)."""
    gray = _grab_primary_gray()
    if _center(REPAIR_PATH, gray, 0.8):
        return True
    body = _center(BODY_PATH, gray, 0.8)
    tmpl = cv2.imread(REPAIR_PATH, cv2.IMREAD_GRAYSCALE)
    if not body or tmpl is None:
        return False
    th, tw = tmpl.shape[:2]
    cx, cy = body[0] + REPAIR_OFF[0], body[1] + REPAIR_OFF[1]
    pad = 10  # tolerate a few px of drift
    x1, y1 = max(cx - tw // 2 - pad, 0), max(cy - th // 2 - pad, 0)
    crop = gray[y1:y1 + th + 2 * pad, x1:x1 + tw + 2 * pad]
    if crop.shape[0] < th or crop.shape[1] < tw:
        return False
    return cv2.minMaxLoc(cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED))[1] >= CONF_REPAIR_NEAR


def _click_repair():
    pos = _repair_pos()
    if not pos:
        print('  [CLICK] repair — neither repair.png nor body.png found, nothing to click')
        return
    (x, y), how = pos
    _click_at(x, y)
    print(f'  [CLICK] repair @ ({x},{y})  ({how})')


def _click_at(x, y):
    _focus_game_window()
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    pyautogui.mouseDown()
    time.sleep(CLICK_HOLD)
    pyautogui.mouseUp()


def _click_image(path, confidence=0.8, retries=RETRIES, delay=1.0):
    """Waits for `path` to appear on screen (up to `retries` attempts) and clicks its center.
    If it's never confidently found, clicks the best on-screen match anyway — no screenshots,
    always take the best guess and move on to the next step."""
    name = os.path.basename(path)
    for attempt in range(retries):
        _check_alive()
        try:
            loc = pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
        except pyautogui.ImageNotFoundException:
            loc = None
        if loc:
            x, y = pyautogui.center(loc)
            _click_at(x, y)
            print(f'  [CLICK] {name} @ ({x},{y})')
            return True
        time.sleep(delay)

    best = _best_match(path)
    if best:
        bx, by, bw, bh, score = best
        x, y = bx + bw // 2, by + bh // 2
        _click_at(x, y)
        print(f'  [CLICK] {name} not confidently found (score={score:.2f}, need {confidence}) '
              f'— best-effort click @ ({x},{y})')
    else:
        print(f'  [CLICK] {name} — template failed to load, nothing to click.')
    return True


def _is_visible(path, confidence=0.8):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True) is not None
    except pyautogui.ImageNotFoundException:
        return False


def _warehouse_open():
    gray = _grab_primary_gray()
    return _find_warehouse(gray) is not None


def _run_step(action, check, expect, trials=TRIALS, verify_tries=6, verify_delay=0.5):
    """Runs `action`, then validates it by polling `check` (up to verify_tries * verify_delay
    seconds). Prints the validation result; re-runs the action up to `trials` times until it
    validates. Always returns True — a step that never validates is reported and skipped past."""
    for trial in range(1, trials + 1):
        _check_alive()
        action()
        for _ in range(verify_tries):
            _check_alive()
            time.sleep(verify_delay)
            if check():
                print(f'  [OK] {expect}')
                return True
        print(f'  [FAIL] {expect} — not validated (trial {trial}/{trials})')
    print(f'  [FAIL] {expect} — gave up after {trials} trials, continuing anyway.')
    return True


def _press_alt_p():
    """Presses Alt+P (remote warehouse hotkey), holding it for ALT_P_HOLD seconds."""
    _focus_game_window()
    pyautogui.keyDown('alt')
    try:
        time.sleep(0.05)
        pyautogui.keyDown('p')
        time.sleep(ALT_P_HOLD)
        pyautogui.keyUp('p')
    finally:
        pyautogui.keyUp('alt')
    print(f'  [KEY] Alt+P (held {ALT_P_HOLD}s)')


def _click_warehouse_x():
    """Clicks the warehouse panel's X button, if the panel is on screen."""
    panel = _find_warehouse(_grab_primary_gray())
    if not panel:
        return
    px, py, _, _ = panel
    x, y = px + WH_CLOSE_OFF[0], py + WH_CLOSE_OFF[1]
    _click_at(x, y)
    print(f'  [CLICK] warehouse X @ ({x},{y})')


def _close_warehouse():
    """Step 1 — closes the warehouse panel if it's open; a no-op otherwise."""
    if not _warehouse_open():
        print('  [OK] warehouse not open — skipping')
        return True
    return _run_step(_click_warehouse_x, lambda: not _warehouse_open(), 'warehouse disappeared')


def _open_status():
    """Opens the Status window — skipped if body.png is already visible (Status already
    open), so the next step goes straight to clicking Body."""
    if _is_visible(BODY_PATH):
        print('  [OK] body.png already visible — skipping Status')
        return True
    return _run_step(lambda: _click_image(STATUS_PATH), lambda: _is_visible(BODY_PATH), 'body.png appeared')


def open_warehouse():
    """Opens the remote warehouse with Alt+P, validated; a no-op if it's already open
    (so it never toggles an open warehouse closed)."""
    if _warehouse_open():
        print('  [OK] warehouse already open — skipping Alt+P')
        return True
    return _run_step(_press_alt_p, _warehouse_open, 'warehouse appeared')


def run_repair(wait_if_dead=None):
    """Repairs equipped gear, then reopens the remote warehouse.

    1. Close the warehouse panel (X button), if it's open.
    2. Open Status.
    3. Select Body.
    4. Click Repair.
    5. Confirm Yes.
    6. Close the Status window.
    7. Open the remote warehouse (Alt+P).

    Every step is validated by the UI change it should cause (e.g. Status -> body.png
    appears, Close status -> body.png disappears), printing [OK]/[FAIL], and is retried up
    to TRIALS (5) times until it validates — but never aborts the sequence. If a button is
    never confidently found, it clicks the best on-screen match anyway and moves on regardless.

    `wait_if_dead`, if given, is polled between steps and inside every retry loop
    so a mid-repair death (revive screen) pauses the sequence instead of it flailing
    against a screen it no longer recognizes.
    """
    global _wait_if_dead
    _wait_if_dead = wait_if_dead

    steps = [
        ('close warehouse', _close_warehouse),
        ('status',          _open_status),
        ('body',            lambda: _run_step(lambda: _click_image(BODY_PATH),
                                              _repair_ready, 'repair button showing')),
        ('repair',          lambda: _run_step(_click_repair,
                                              lambda: _is_visible(YES_PATH), 'yes.png appeared')),
        ('yes',             lambda: _run_step(lambda: _click_image(YES_PATH),
                                              lambda: not _is_visible(YES_PATH), 'yes.png disappeared')),
        ('close status',    lambda: _run_step(lambda: _click_image(CLOSE_STATUS_PATH),
                                              lambda: not _is_visible(BODY_PATH), 'body.png disappeared')),
        ('warehouse',       open_warehouse),
    ]
    for label, step in steps:
        _check_alive()
        print(f'[REPAIR] {label}...')
        if not step():
            print(f'[REPAIR] Aborted at "{label}".')
            return False
        time.sleep(WAIT)
    print('[REPAIR] Done.')
    return True


if __name__ == '__main__':
    run_repair()
