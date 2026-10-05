"""Scatter arrows: once per desktop visit, right-clicks around CLICKS randomly picked corners of
the VIP page (no fixed order), each at a random spot near the point OFFSET px outside that
corner, so the character casts scatter arrows there.

The VIP page is found by its top strip (frame + "VIP" box + X button), which looks the same on
every tab; the rest of vip_menu.jpg is the Overview tab, but the page is left on Compose.
Settings live under 'scatter' in settings_schema.json and are re-read on every volley."""
import os
import random
import time

import cv2
import pyautogui

import game_input as gi  # real-device input via the Interception driver

import screen
from _paths import app_root
from dragon_settings import get_settings
from game_window import focus_game_window

_DEFAULTS = {'ENABLED': True, 'CLICKS': 1, 'OFFSET': 20, 'JITTER': 15, 'DELAY': 0.3}

CONF_VIP_TOP = 0.9  # header match; an empty black screen already scores ~0.74, so keep this high
HEADER_H     = 48   # px of vip_menu.jpg used (above the "VIP time remaining" text, which changes)

_vip = cv2.imread(os.path.join(app_root(), 'reference_images', 'vip_menu.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_top = _vip[:HEADER_H] if _vip is not None else None
PAGE_W, PAGE_H = (_vip.shape[1], _vip.shape[0]) if _vip is not None else (0, 0)

CORNERS = ('top-left', 'top-right', 'bottom-right', 'bottom-left')


def _vip_page():
    """(x, y) of the VIP page's top-left corner on screen, or None if it isn't showing."""
    found = screen.find(screen.grab_gray(), _tmpl_top, CONF_VIP_TOP)
    return found[:2] if found else None


def volley():
    """Right-clicks around CLICKS different corners of the VIP page, picked at random: each click
    lands within JITTER px of the point OFFSET px outside that corner (diagonally away from the
    page's center), DELAY seconds apart. Skipped if disabled or the VIP page isn't on screen."""
    s = get_settings('scatter', _DEFAULTS)
    if not s['ENABLED']:
        return False
    page = _vip_page()
    if not page:
        print('  [SCATTER] VIP page not found — skipping')
        return False

    x, y = page
    o = int(s['OFFSET'])
    points = ((x - o, y - o), (x + PAGE_W + o, y - o),
              (x + PAGE_W + o, y + PAGE_H + o), (x - o, y + PAGE_H + o))
    sw, sh = pyautogui.size()

    j = int(s['JITTER'])
    picked = random.sample(range(len(CORNERS)), min(int(s['CLICKS']), len(CORNERS)))

    focus_game_window()
    for i, c in enumerate(picked):
        if i:
            time.sleep(float(s['DELAY']))  # only between clicks — no wait after the last one
        px = points[c][0] + random.randint(-j, j)
        py = points[c][1] + random.randint(-j, j)
        px, py = min(max(px, 0), sw - 1), min(max(py, 0), sh - 1)
        gi.move(px, py)
        time.sleep(0.05)
        gi.down('right')
        time.sleep(0.1)
        gi.up('right')
    print('  [SCATTER] right-clicked ' + ', '.join(CORNERS[c] for c in picked))
    return True
