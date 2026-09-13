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
WH_BTN_PATH          = os.path.join(_DIR, 'wh_btn.png')

WH_CLOSE_OFF = (326, 13)   # X button, relative to the warehouse panel's top-left
VIP_BTN_POS  = (652, 984)  # VIP button — fixed HUD position, doesn't move, no need to search for it

CONF_WH  = 0.5
WAIT     = 0.5   # seconds between steps
RETRIES  = 15    # attempts (1s apart) to wait for each UI element to appear

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
    with mss.MSS() as sct:
        raw = np.array(sct.grab(sct.monitors[1]))
    gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
    th, tw = tmpl.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    return (loc[0], loc[1], tw, th, float(val))


def _click_at(x, y):
    _focus_game_window()
    pyautogui.moveTo(x, y, duration=0.05)
    time.sleep(0.05)
    pyautogui.mouseDown()
    time.sleep(0.08)
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
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2GRAY)
    return _find_warehouse(gray) is not None


def _click_verify(path, verify_fn, confidence=0.8, outer_retries=3, verify_tries=6, verify_delay=0.5):
    """Clicks `path`, then confirms the expected UI change via verify_fn; re-clicks (up to
    `outer_retries` times) if the change never shows up. Always returns True — even if the
    change never confirms, we've already best-effort clicked, so move on to the next step."""
    for attempt in range(outer_retries):
        _check_alive()
        _click_image(path, confidence=confidence)
        for _ in range(verify_tries):
            _check_alive()
            time.sleep(verify_delay)
            if verify_fn():
                return True
        print(f'  [CHECK] {os.path.basename(path)} click not confirmed — retrying ({attempt + 1}/{outer_retries})')
    print(f'  [CHECK] {os.path.basename(path)} still not confirmed after retries — continuing anyway.')
    return True


def _click_vip_btn(verify_fn, outer_retries=3, verify_tries=6, verify_delay=0.5):
    """Clicks the VIP button at its fixed screen position (no image search needed),
    then confirms the menu opened; re-clicks if it didn't. Always returns True."""
    for attempt in range(outer_retries):
        _check_alive()
        _click_at(*VIP_BTN_POS)
        print(f'  [CLICK] vip_btn (static) @ {VIP_BTN_POS}')
        for _ in range(verify_tries):
            _check_alive()
            time.sleep(verify_delay)
            if verify_fn():
                return True
        print(f'  [CHECK] vip_btn click not confirmed — retrying ({attempt + 1}/{outer_retries})')
    print('  [CHECK] vip_btn still not confirmed after retries — continuing anyway.')
    return True


def _close_warehouse():
    """Step 1 — closes the warehouse panel if it's open; a no-op otherwise."""
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2GRAY)
    if not _find_warehouse(gray):
        print('  [CLICK] warehouse not open — skipping')
        return True

    for attempt in range(3):
        _check_alive()
        with mss.MSS() as sct:
            gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2GRAY)
        panel = _find_warehouse(gray)
        if not panel:
            print('  [CLICK] warehouse closed')
            return True
        px, py, _, _ = panel
        x, y = px + WH_CLOSE_OFF[0], py + WH_CLOSE_OFF[1]
        _click_at(x, y)
        print(f'  [CLICK] warehouse X @ ({x},{y})  (attempt {attempt + 1}/3)')
        time.sleep(0.7)

    print('  [CHECK] warehouse still open after retries — continuing anyway')
    return True


def run_repair(wait_if_dead=None):
    """Repairs equipped gear, then reopens the remote warehouse.

    1. Close the warehouse panel (X button), if it's open.
    2. Open Status.
    3. Select Body.
    4. Click Repair.
    5. Confirm Yes.
    6. Close the Status window.
    7. Open the VIP menu.
    8. Open the remote warehouse.

    Every step tries to confirm the resulting UI change actually happened, re-clicking
    if it didn't — but never aborts the sequence. If a button is never confidently found,
    it clicks the best on-screen match anyway (no screenshots) and moves on regardless.

    `wait_if_dead`, if given, is polled between steps and inside every retry loop
    so a mid-repair death (revive screen) pauses the sequence instead of it flailing
    against a screen it no longer recognizes.
    """
    global _wait_if_dead
    _wait_if_dead = wait_if_dead

    steps = [
        ('close warehouse', _close_warehouse),
        ('status',          lambda: _click_verify(STATUS_PATH, lambda: _is_visible(BODY_PATH))),
        ('body',            lambda: _click_verify(BODY_PATH, lambda: _is_visible(REPAIR_PATH))),
        ('repair',          lambda: _click_verify(REPAIR_PATH, lambda: _is_visible(YES_PATH))),
        ('yes',             lambda: _click_verify(YES_PATH, lambda: not _is_visible(YES_PATH))),
        ('close status',    lambda: _click_verify(
            CLOSE_STATUS_PATH,
            lambda: not _is_visible(CLOSE_STATUS_PATH) and not _is_visible(BODY_PATH),
        )),
        ('vip menu',        lambda: _click_vip_btn(lambda: _is_visible(WH_BTN_PATH))),
        ('warehouse',       lambda: _click_verify(WH_BTN_PATH, _warehouse_open)),
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
