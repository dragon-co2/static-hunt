"""Closing the game's panels at the end of a service visit: the VIP menu (its X button), the
warehouse (its X button) and the inventory (its "Close" button). Each button sits at a fixed
offset from its panel's top-left corner, measured on the reference images. A close is checked
by the panel disappearing, and retried a few times."""
import os
import time

import cv2

import game_input as gi
import screen
from _paths import app_root
from game_window import focus_game_window

VIP_CLOSE_OFF = (797, 18)    # X button, from the VIP page's top-left (vip_menu.jpg, new 825x651 design)
WH_CLOSE_OFF  = (337, 17)    # X button, from the warehouse panel's top-left (warehouse_title.jpg)
INV_CLOSE_OFF = (187, 442)   # "Close" text, from the inventory panel's top-left (inventory_title.jpg)

CONF_VIP_TOP = 0.9  # VIP page header match; an empty black screen already scores ~0.74
CLOSE_TRIES  = 3    # clicks per panel before giving up on closing it

_vip = cv2.imread(os.path.join(app_root(), 'reference_images', 'vip_menu.jpg'), cv2.IMREAD_GRAYSCALE)
VIP_HEADER_H = 36  # top strip of vip_menu.jpg: crown + "VIP" title + X — the same on every tab
_tmpl_vip_top = _vip[:VIP_HEADER_H] if _vip is not None else None


def find_vip(gray=None):
    """(x, y, w, h) of the VIP page, found by its top strip, or None."""
    return screen.find(screen.grab_gray() if gray is None else gray, _tmpl_vip_top, CONF_VIP_TOP)


def _close(name, find, offset):
    """Clicks `offset` from the panel's top-left until `find()` no longer sees it."""
    for _ in range(CLOSE_TRIES):
        panel = find()
        if not panel:
            return True
        focus_game_window()
        gi.click(panel[0] + offset[0], panel[1] + offset[1])
        for _ in range(4):
            time.sleep(0.25)
            if not find():
                print(f'  [CLOSE] {name} closed')
                return True
    print(f'  [CLOSE] {name} still open after {CLOSE_TRIES} clicks')
    return False


def close_all():
    """Closes the VIP menu, then the warehouse, then the inventory — whichever are open."""
    _close('VIP menu', find_vip, VIP_CLOSE_OFF)
    _close('warehouse', screen.find_warehouse, WH_CLOSE_OFF)
    _close('inventory', screen.find_inventory, INV_CLOSE_OFF)
