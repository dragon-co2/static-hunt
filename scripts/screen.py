"""Shared screen capture + template matching, so every script sees the same screen the same way.

Always captures the PRIMARY monitor only: it starts at (0, 0) in Windows' coordinates, so a
position found in a capture is exactly where pyautogui moves/clicks. (mss's monitors[0] is all
monitors stitched together — its origin shifts if a monitor sits left of / above the primary.)

One match threshold per panel, used by every script:
  warehouse_title.jpg — open ~0.93, closed ~0.26
  inventory_title.jpg — open ~0.81, closed ~0.12-0.15
"""
import os
import time

import cv2
import mss
import numpy as np

from _paths import app_root

_DIR = os.path.join(app_root(), 'reference_images')

CONF_WH  = 0.5  # warehouse (bank) panel
CONF_INV = 0.5  # inventory panel

tmpl_wh  = cv2.imread(os.path.join(_DIR, 'warehouse_title.jpg'), cv2.IMREAD_GRAYSCALE)
tmpl_inv = cv2.imread(os.path.join(_DIR, 'inventory_title.jpg'), cv2.IMREAD_GRAYSCALE)


def _primary(sct):
    return next((m for m in sct.monitors[1:] if m.get('is_primary')), sct.monitors[1])


RETRY_EVERY = 2  # seconds between capture attempts while the screen is unavailable


def _grab_raw():
    """BGRA capture of the primary monitor. Windows refuses captures for a moment while the
    session switches screens (e.g. an RDP session handed back to the console with tscon) or
    while it's locked; instead of crashing, wait here until capturing works again."""
    waiting = False
    while True:
        try:
            with mss.MSS() as sct:
                raw = np.array(sct.grab(_primary(sct)))
            if waiting:
                print('[SCREEN] screen is available again — carrying on')
            return raw
        except Exception as e:  # mss ScreenShotError (BitBlt: Access is denied), etc.
            if not waiting:
                print(f'[SCREEN] cannot capture the screen ({e}) — session locked or switching; '
                      f'retrying every {RETRY_EVERY}s')
                waiting = True
            time.sleep(RETRY_EVERY)


def wait_for_screen():
    """Blocks until the screen can be captured (returns at once if it already can)."""
    _grab_raw()


def grab_bgr():
    """Color (BGR) capture of the primary monitor."""
    return cv2.cvtColor(_grab_raw(), cv2.COLOR_BGRA2BGR)


def grab_gray():
    """Grayscale capture of the primary monitor."""
    return cv2.cvtColor(_grab_raw(), cv2.COLOR_BGRA2GRAY)


def best(gray, template):
    """(x, y, w, h, score) of the best match of `template` in `gray`, or None if it can't fit."""
    if template is None:
        return None
    th, tw = template.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    _, val, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED))
    return loc[0], loc[1], tw, th, float(val)


def find(gray, template, threshold):
    """(x, y, w, h) of `template` in `gray` if it scores >= threshold, else None."""
    b = best(gray, template)
    return b[:4] if b and b[4] >= threshold else None


def find_warehouse(gray=None):
    """(x, y, w, h) of the warehouse panel, or None."""
    return find(grab_gray() if gray is None else gray, tmpl_wh, CONF_WH)


def find_inventory(gray=None):
    """(x, y, w, h) of the inventory panel, or None."""
    return find(grab_gray() if gray is None else gray, tmpl_inv, CONF_INV)
