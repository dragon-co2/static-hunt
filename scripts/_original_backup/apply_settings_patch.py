# -*- coding: utf-8 -*-
"""
One-time patcher: rewires the 9 dragon-co2 scripts to read their tunable
constants from dragon_settings.get_settings(...) instead of hard-coding
them, WITHOUT changing any behavior, variable name, or execution order.

Safety:
  - Backs up every original file, once, under _original_backup/ (never
    overwrites an existing backup, so re-running this is harmless).
  - Each patch is an exact, whitespace-for-whitespace string replacement.
    If the expected original text isn't found exactly once in a file,
    that file is left untouched and an error is reported -- nothing is
    ever guessed or force-written.
  - After patching, every changed file is checked with py_compile.

Run this once, from inside the scripts/ folder, on the machine that has
the actual script files (this container's Linux python can compile-check
them fine even though it can't import pyautogui/ctypes.windll).
"""
import os
import py_compile
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, '_original_backup')

PATCHES = {}


def add(fname, old, new):
    PATCHES.setdefault(fname, []).append((old, new))


# ---------------------------------------------------------------------------
# auto_left_click.py
# ---------------------------------------------------------------------------
add('auto_left_click.py',
    "CLICK_INTERVAL = 0.1  # seconds between each left click while held\n",
    "from dragon_settings import get_settings\n"
    "_S = get_settings('auto_left_click', {'CLICK_INTERVAL': 0.1})\n"
    "\n"
    "CLICK_INTERVAL = _S['CLICK_INTERVAL']  # seconds between each left click while held\n")

# ---------------------------------------------------------------------------
# fly.py
# ---------------------------------------------------------------------------
add('fly.py',
    "CONFIDENCE = 0.9\n",
    "from dragon_settings import get_settings\n"
    "_S = get_settings('fly', {'CONFIDENCE': 0.9})\n"
    "\n"
    "CONFIDENCE = _S['CONFIDENCE']\n")

# ---------------------------------------------------------------------------
# revive.py
# ---------------------------------------------------------------------------
add('revive.py',
    "REVIVE_WAIT  = 1    # seconds to wait after death before the revive button becomes clickable\n"
    "REVIVE_RETRY = 1.0   # seconds between re-checks once the wait is over\n",
    "from dragon_settings import get_settings\n"
    "_S = get_settings('revive', {'REVIVE_WAIT': 1, 'REVIVE_RETRY': 1.0})\n"
    "\n"
    "REVIVE_WAIT  = _S['REVIVE_WAIT']    # seconds to wait after death before the revive button becomes clickable\n"
    "REVIVE_RETRY = _S['REVIVE_RETRY']   # seconds between re-checks once the wait is over\n")

# ---------------------------------------------------------------------------
# repaire.py
# ---------------------------------------------------------------------------
add('repaire.py',
    "WH_CLOSE_OFF = (326, 13)   # X button, relative to the warehouse panel's top-left\n"
    "VIP_BTN_POS  = (652, 984)  # VIP button — fixed HUD position, doesn't move, no need to search for it\n"
    "\n"
    "CONF_WH  = 0.5\n"
    "WAIT     = 0.5   # seconds between steps\n"
    "RETRIES  = 15    # attempts (1s apart) to wait for each UI element to appear\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('repaire', {\n"
    "    'WH_CLOSE_OFF': (326, 13),\n"
    "    'VIP_BTN_POS':  (652, 984),\n"
    "    'CONF_WH':      0.5,\n"
    "    'WAIT':         0.5,\n"
    "    'RETRIES':      15,\n"
    "})\n"
    "\n"
    "WH_CLOSE_OFF = tuple(_S['WH_CLOSE_OFF'])   # X button, relative to the warehouse panel's top-left\n"
    "VIP_BTN_POS  = tuple(_S['VIP_BTN_POS'])    # VIP button — fixed HUD position, doesn't move, no need to search for it\n"
    "\n"
    "CONF_WH  = _S['CONF_WH']\n"
    "WAIT     = _S['WAIT']       # seconds between steps\n"
    "RETRIES  = _S['RETRIES']    # attempts (1s apart) to wait for each UI element to appear\n")

# ---------------------------------------------------------------------------
# compose.py
# ---------------------------------------------------------------------------
add('compose.py',
    "# Fixed offsets from the compose dialog's top-left corner.\n"
    "# Tune if clicks land wrong — the print line shows dialog@(dx,dy) so you can verify.\n"
    "DIALOG_MAIN_OFF    = (68,  80)   # center of the left (Main) slot\n"
    "DIALOG_MINOR_OFF   = (200, 80)   # center of the right (Minor) slot\n"
    "DIALOG_COMPOSE_OFF = (167, 260)  # center of the Compose button\n"
    "\n"
    "# ── Inventory grid ─────────────────────────────────────────────────────────────\n"
    "CONF_INV     = 0.3\n"
    "INV_OFFSET_X = 18\n"
    "INV_OFFSET_Y = 10\n"
    "INV_SLOT_W   = 43\n"
    "INV_SLOT_H   = 43\n"
    "INV_COLS     = 5\n"
    "INV_ROWS     = 8\n"
    "\n"
    "WAIT = 0.7  # seconds between steps\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('compose', {\n"
    "    'DIALOG_MAIN_OFF': (68, 80), 'DIALOG_MINOR_OFF': (200, 80), 'DIALOG_COMPOSE_OFF': (167, 260),\n"
    "    'CONF_INV': 0.3, 'INV_OFFSET_X': 18, 'INV_OFFSET_Y': 10,\n"
    "    'INV_SLOT_W': 43, 'INV_SLOT_H': 43, 'INV_COLS': 5, 'INV_ROWS': 8,\n"
    "    'WAIT': 0.7,\n"
    "})\n"
    "\n"
    "# Fixed offsets from the compose dialog's top-left corner.\n"
    "# Tune if clicks land wrong — the print line shows dialog@(dx,dy) so you can verify.\n"
    "DIALOG_MAIN_OFF    = tuple(_S['DIALOG_MAIN_OFF'])     # center of the left (Main) slot\n"
    "DIALOG_MINOR_OFF   = tuple(_S['DIALOG_MINOR_OFF'])    # center of the right (Minor) slot\n"
    "DIALOG_COMPOSE_OFF = tuple(_S['DIALOG_COMPOSE_OFF'])  # center of the Compose button\n"
    "\n"
    "# ── Inventory grid ─────────────────────────────────────────────────────────────\n"
    "CONF_INV     = _S['CONF_INV']\n"
    "INV_OFFSET_X = _S['INV_OFFSET_X']\n"
    "INV_OFFSET_Y = _S['INV_OFFSET_Y']\n"
    "INV_SLOT_W   = _S['INV_SLOT_W']\n"
    "INV_SLOT_H   = _S['INV_SLOT_H']\n"
    "INV_COLS     = _S['INV_COLS']\n"
    "INV_ROWS     = _S['INV_ROWS']\n"
    "\n"
    "WAIT = _S['WAIT']  # seconds between steps\n")

# ---------------------------------------------------------------------------
# bank_compose.py  (two separate spots: the big constants block, and
# DRAGONBALL_NEED further down)
# ---------------------------------------------------------------------------
add('bank_compose.py',
    "WH_OFFSET_X  = 120\n"
    "WH_OFFSET_Y  = 40\n"
    "WH_SLOT_W    = 43\n"
    "WH_SLOT_H    = 43\n"
    "WH_COLS      = 5\n"
    "WH_ROWS      = 4\n"
    "\n"
    "WH_TAB_X     = 5\n"
    "WH_TAB_Y     = 26\n"
    "WH_TAB_W     = 110\n"
    "WH_TAB_H     = 31\n"
    "WH_TAB_COUNT = 7\n"
    "\n"
    "CONF_WH      = 0.35\n"
    "CONF_EMPTY   = 0.9\n"
    "CLICK_MULTIPLIER = 1.5  # extra clicks over the detected item count, e.g. 1.5 = +50%\n"
    "MEM_WINDOW   = 'GhostArrow'  # partial game window title, same as navigation.py\n"
    "VIS          = 1\n"
    "CYCLES       = 7\n"
    "WAREHOUSE_MIN_ITEMS = 20  # only run the compose flow if the warehouse has at least this many items\n"
    "VIP_BTN_PATH = os.path.join(_DIR, 'vip_btn.png')  # VIP button image, searched for on screen\n"
    "\n"
    "# ── Inventory / compose dialog ─────────────────────────────────────────────────\n"
    "CONF_INV     = 0.3\n"
    "CONF_DIALOG  = 0.6\n"
    "INV_OFFSET_X = 18\n"
    "INV_OFFSET_Y = 10\n"
    "INV_SLOT_W   = 43\n"
    "INV_SLOT_H   = 43\n"
    "INV_COLS     = 5\n"
    "INV_ROWS     = 8\n"
    "\n"
    "# Fixed offsets from the compose dialog's top-left corner.\n"
    "DIALOG_MAIN_OFF    = (68,  80)   # center of the left (Main) slot\n"
    "DIALOG_MINOR_OFF   = (200, 80)   # center of the right (Minor) slot\n"
    "DIALOG_COMPOSE_OFF = (167, 260)  # center of the Compose button\n"
    "DIALOG_CANCEL_OFF  = (244, 260)  # center of the Cancel button\n"
    "\n"
    "WAIT = 0.4  # seconds between compose sub-steps\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('bank_compose', {\n"
    "    'WH_OFFSET_X': 120, 'WH_OFFSET_Y': 40, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,\n"
    "    'WH_COLS': 5, 'WH_ROWS': 4,\n"
    "    'WH_TAB_X': 5, 'WH_TAB_Y': 26, 'WH_TAB_W': 110, 'WH_TAB_H': 31, 'WH_TAB_COUNT': 7,\n"
    "    'CONF_WH': 0.35, 'CONF_EMPTY': 0.9, 'CLICK_MULTIPLIER': 1.5,\n"
    "    'VIS': 1, 'CYCLES': 7, 'WAREHOUSE_MIN_ITEMS': 20,\n"
    "    'CONF_INV': 0.3, 'CONF_DIALOG': 0.6,\n"
    "    'INV_OFFSET_X': 18, 'INV_OFFSET_Y': 10, 'INV_SLOT_W': 43, 'INV_SLOT_H': 43,\n"
    "    'INV_COLS': 5, 'INV_ROWS': 8,\n"
    "    'DIALOG_MAIN_OFF': (68, 80), 'DIALOG_MINOR_OFF': (200, 80),\n"
    "    'DIALOG_COMPOSE_OFF': (167, 260), 'DIALOG_CANCEL_OFF': (244, 260),\n"
    "    'WAIT': 0.4,\n"
    "    'DRAGONBALL_NEED': 10,\n"
    "})\n"
    "\n"
    "WH_OFFSET_X  = _S['WH_OFFSET_X']\n"
    "WH_OFFSET_Y  = _S['WH_OFFSET_Y']\n"
    "WH_SLOT_W    = _S['WH_SLOT_W']\n"
    "WH_SLOT_H    = _S['WH_SLOT_H']\n"
    "WH_COLS      = _S['WH_COLS']\n"
    "WH_ROWS      = _S['WH_ROWS']\n"
    "\n"
    "WH_TAB_X     = _S['WH_TAB_X']\n"
    "WH_TAB_Y     = _S['WH_TAB_Y']\n"
    "WH_TAB_W     = _S['WH_TAB_W']\n"
    "WH_TAB_H     = _S['WH_TAB_H']\n"
    "WH_TAB_COUNT = _S['WH_TAB_COUNT']\n"
    "\n"
    "CONF_WH      = _S['CONF_WH']\n"
    "CONF_EMPTY   = _S['CONF_EMPTY']\n"
    "CLICK_MULTIPLIER = _S['CLICK_MULTIPLIER']  # extra clicks over the detected item count, e.g. 1.5 = +50%\n"
    "MEM_WINDOW   = 'GhostArrow'  # partial game window title, same as navigation.py\n"
    "VIS          = _S['VIS']\n"
    "CYCLES       = _S['CYCLES']\n"
    "WAREHOUSE_MIN_ITEMS = _S['WAREHOUSE_MIN_ITEMS']  # only run the compose flow if the warehouse has at least this many items\n"
    "VIP_BTN_PATH = os.path.join(_DIR, 'vip_btn.png')  # VIP button image, searched for on screen\n"
    "\n"
    "# ── Inventory / compose dialog ─────────────────────────────────────────────────\n"
    "CONF_INV     = _S['CONF_INV']\n"
    "CONF_DIALOG  = _S['CONF_DIALOG']\n"
    "INV_OFFSET_X = _S['INV_OFFSET_X']\n"
    "INV_OFFSET_Y = _S['INV_OFFSET_Y']\n"
    "INV_SLOT_W   = _S['INV_SLOT_W']\n"
    "INV_SLOT_H   = _S['INV_SLOT_H']\n"
    "INV_COLS     = _S['INV_COLS']\n"
    "INV_ROWS     = _S['INV_ROWS']\n"
    "\n"
    "# Fixed offsets from the compose dialog's top-left corner.\n"
    "DIALOG_MAIN_OFF    = tuple(_S['DIALOG_MAIN_OFF'])     # center of the left (Main) slot\n"
    "DIALOG_MINOR_OFF   = tuple(_S['DIALOG_MINOR_OFF'])    # center of the right (Minor) slot\n"
    "DIALOG_COMPOSE_OFF = tuple(_S['DIALOG_COMPOSE_OFF'])  # center of the Compose button\n"
    "DIALOG_CANCEL_OFF  = tuple(_S['DIALOG_CANCEL_OFF'])   # center of the Cancel button\n"
    "\n"
    "WAIT = _S['WAIT']  # seconds between compose sub-steps\n")

add('bank_compose.py',
    "DRAGONBALL['label'] = 'dragonball'\n"
    "DRAGONBALL_NEED = 10  # right-click one once at least this many are in the inventory, to convert them into a scroll\n",
    "DRAGONBALL['label'] = 'dragonball'\n"
    "DRAGONBALL_NEED = _S['DRAGONBALL_NEED']  # right-click one once at least this many are in the inventory, to convert them into a scroll\n")

# ---------------------------------------------------------------------------
# new_bank_compose.py
# ---------------------------------------------------------------------------
add('new_bank_compose.py',
    "WH_OFFSET_X = 128\n"
    "WH_OFFSET_Y = 46\n"
    "WH_SLOT_W   = 43\n"
    "WH_SLOT_H   = 43\n"
    "WH_COLS     = 5\n"
    "WH_ROWS     = 4\n"
    "\n"
    "CONF_WH    = 0.35\n"
    "CONF_EMPTY = 0.8\n"
    "CONF_ARROW = 0.5\n"
    "CLICK_MULTIPLIER = 2  # extra clicks over the detected item count, e.g. 1.5 = +50%\n"
    "WAREHOUSE_MIN_ITEMS = 10  # only withdraw + run the vip flow if the warehouse has at least this many items\n"
    "\n"
    "WAIT = 0.4  # seconds between steps\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('new_bank_compose', {\n"
    "    'WH_OFFSET_X': 128, 'WH_OFFSET_Y': 46, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,\n"
    "    'WH_COLS': 5, 'WH_ROWS': 4,\n"
    "    'CONF_WH': 0.35, 'CONF_EMPTY': 0.8, 'CONF_ARROW': 0.5,\n"
    "    'CLICK_MULTIPLIER': 2, 'WAREHOUSE_MIN_ITEMS': 10,\n"
    "    'WAIT': 0.4,\n"
    "})\n"
    "\n"
    "WH_OFFSET_X = _S['WH_OFFSET_X']\n"
    "WH_OFFSET_Y = _S['WH_OFFSET_Y']\n"
    "WH_SLOT_W   = _S['WH_SLOT_W']\n"
    "WH_SLOT_H   = _S['WH_SLOT_H']\n"
    "WH_COLS     = _S['WH_COLS']\n"
    "WH_ROWS     = _S['WH_ROWS']\n"
    "\n"
    "CONF_WH    = _S['CONF_WH']\n"
    "CONF_EMPTY = _S['CONF_EMPTY']\n"
    "CONF_ARROW = _S['CONF_ARROW']\n"
    "CLICK_MULTIPLIER = _S['CLICK_MULTIPLIER']  # extra clicks over the detected item count, e.g. 1.5 = +50%\n"
    "WAREHOUSE_MIN_ITEMS = _S['WAREHOUSE_MIN_ITEMS']  # only withdraw + run the vip flow if the warehouse has at least this many items\n"
    "\n"
    "WAIT = _S['WAIT']  # seconds between steps\n")

# ---------------------------------------------------------------------------
# grab_arrows.py
# ---------------------------------------------------------------------------
add('grab_arrows.py',
    "INV_OFFSET_X = 18\n"
    "INV_OFFSET_Y = 10\n"
    "INV_SLOT_W   = 43\n"
    "INV_SLOT_H   = 43\n"
    "INV_COLS     = 5\n"
    "INV_ROWS     = 8\n"
    "\n"
    "WH_OFFSET_X  = 120\n"
    "WH_OFFSET_Y  = 40\n"
    "WH_SLOT_W    = 43\n"
    "WH_SLOT_H    = 43\n"
    "WH_COLS      = 5\n"
    "WH_ROWS      = 4\n"
    "\n"
    "WH_TAB_X     = 5\n"
    "WH_TAB_Y     = 26\n"
    "WH_TAB_W     = 110\n"
    "WH_TAB_H     = 31\n"
    "WH_TAB_COUNT = 7\n"
    "\n"
    "CONF_INV   = 0.3\n"
    "CONF_WH    = 0.35\n"
    "CONF_ARROW = 0.5\n"
    "\n"
    "ARROW_TAB_INDEX = 6   # warehouse tab #7 (0-indexed)\n"
    "BANK_TAB_COUNT  = 6   # tabs 0-5 are regular banks; tab 6 is reserved for arrows\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('grab_arrows', {\n"
    "    'INV_OFFSET_X': 18, 'INV_OFFSET_Y': 10, 'INV_SLOT_W': 43, 'INV_SLOT_H': 43,\n"
    "    'INV_COLS': 5, 'INV_ROWS': 8,\n"
    "    'WH_OFFSET_X': 120, 'WH_OFFSET_Y': 40, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,\n"
    "    'WH_COLS': 5, 'WH_ROWS': 4,\n"
    "    'WH_TAB_X': 5, 'WH_TAB_Y': 26, 'WH_TAB_W': 110, 'WH_TAB_H': 31, 'WH_TAB_COUNT': 7,\n"
    "    'CONF_INV': 0.3, 'CONF_WH': 0.35, 'CONF_ARROW': 0.5,\n"
    "    'ARROW_TAB_INDEX': 6, 'BANK_TAB_COUNT': 6,\n"
    "})\n"
    "\n"
    "INV_OFFSET_X = _S['INV_OFFSET_X']\n"
    "INV_OFFSET_Y = _S['INV_OFFSET_Y']\n"
    "INV_SLOT_W   = _S['INV_SLOT_W']\n"
    "INV_SLOT_H   = _S['INV_SLOT_H']\n"
    "INV_COLS     = _S['INV_COLS']\n"
    "INV_ROWS     = _S['INV_ROWS']\n"
    "\n"
    "WH_OFFSET_X  = _S['WH_OFFSET_X']\n"
    "WH_OFFSET_Y  = _S['WH_OFFSET_Y']\n"
    "WH_SLOT_W    = _S['WH_SLOT_W']\n"
    "WH_SLOT_H    = _S['WH_SLOT_H']\n"
    "WH_COLS      = _S['WH_COLS']\n"
    "WH_ROWS      = _S['WH_ROWS']\n"
    "\n"
    "WH_TAB_X     = _S['WH_TAB_X']\n"
    "WH_TAB_Y     = _S['WH_TAB_Y']\n"
    "WH_TAB_W     = _S['WH_TAB_W']\n"
    "WH_TAB_H     = _S['WH_TAB_H']\n"
    "WH_TAB_COUNT = _S['WH_TAB_COUNT']\n"
    "\n"
    "CONF_INV   = _S['CONF_INV']\n"
    "CONF_WH    = _S['CONF_WH']\n"
    "CONF_ARROW = _S['CONF_ARROW']\n"
    "\n"
    "ARROW_TAB_INDEX = _S['ARROW_TAB_INDEX']  # warehouse tab #7 (0-indexed)\n"
    "BANK_TAB_COUNT  = _S['BANK_TAB_COUNT']   # tabs 0-5 are regular banks; tab 6 is reserved for arrows\n")

# ---------------------------------------------------------------------------
# stash2.py
# ---------------------------------------------------------------------------
add('stash2.py',
    "INV_OFFSET_X = 18\n"
    "INV_OFFSET_Y = 10\n"
    "INV_SLOT_W   = 43\n"
    "INV_SLOT_H   = 43\n"
    "INV_COLS     = 5\n"
    "INV_ROWS     = 8\n"
    "\n"
    "WH_OFFSET_X  = 120\n"
    "WH_OFFSET_Y  = 40\n"
    "WH_SLOT_W    = 43\n"
    "WH_SLOT_H    = 43\n"
    "WH_COLS      = 5\n"
    "WH_ROWS      = 4\n"
    "\n"
    "WH_TAB_X     = 5\n"
    "WH_TAB_Y     = 26\n"
    "WH_TAB_W     = 110\n"
    "WH_TAB_H     = 31\n"
    "WH_TAB_COUNT = 6\n"
    "\n"
    "CONF        = 0.3\n"
    "CONF_WH     = 0.3\n"
    "CONF_EMPTY  = 0.95\n"
    "\n"
    "ALT_HOLD     = 0.1   # seconds Alt is held before/after the click\n"
    "TAB_DELAY    = 0.2   # seconds after clicking a tab, before the alt+click\n"
    "STASH_DELAY  = 0.5   # seconds between each item moved\n",

    "from dragon_settings import get_settings\n"
    "_S = get_settings('stash2', {\n"
    "    'INV_OFFSET_X': 18, 'INV_OFFSET_Y': 10, 'INV_SLOT_W': 43, 'INV_SLOT_H': 43,\n"
    "    'INV_COLS': 5, 'INV_ROWS': 8,\n"
    "    'WH_OFFSET_X': 120, 'WH_OFFSET_Y': 40, 'WH_SLOT_W': 43, 'WH_SLOT_H': 43,\n"
    "    'WH_COLS': 5, 'WH_ROWS': 4,\n"
    "    'WH_TAB_X': 5, 'WH_TAB_Y': 26, 'WH_TAB_W': 110, 'WH_TAB_H': 31, 'WH_TAB_COUNT': 6,\n"
    "    'CONF': 0.3, 'CONF_WH': 0.3, 'CONF_EMPTY': 0.95,\n"
    "    'ALT_HOLD': 0.1, 'TAB_DELAY': 0.2, 'STASH_DELAY': 0.5,\n"
    "})\n"
    "\n"
    "INV_OFFSET_X = _S['INV_OFFSET_X']\n"
    "INV_OFFSET_Y = _S['INV_OFFSET_Y']\n"
    "INV_SLOT_W   = _S['INV_SLOT_W']\n"
    "INV_SLOT_H   = _S['INV_SLOT_H']\n"
    "INV_COLS     = _S['INV_COLS']\n"
    "INV_ROWS     = _S['INV_ROWS']\n"
    "\n"
    "WH_OFFSET_X  = _S['WH_OFFSET_X']\n"
    "WH_OFFSET_Y  = _S['WH_OFFSET_Y']\n"
    "WH_SLOT_W    = _S['WH_SLOT_W']\n"
    "WH_SLOT_H    = _S['WH_SLOT_H']\n"
    "WH_COLS      = _S['WH_COLS']\n"
    "WH_ROWS      = _S['WH_ROWS']\n"
    "\n"
    "WH_TAB_X     = _S['WH_TAB_X']\n"
    "WH_TAB_Y     = _S['WH_TAB_Y']\n"
    "WH_TAB_W     = _S['WH_TAB_W']\n"
    "WH_TAB_H     = _S['WH_TAB_H']\n"
    "WH_TAB_COUNT = _S['WH_TAB_COUNT']\n"
    "\n"
    "CONF        = _S['CONF']\n"
    "CONF_WH     = _S['CONF_WH']\n"
    "CONF_EMPTY  = _S['CONF_EMPTY']\n"
    "\n"
    "ALT_HOLD     = _S['ALT_HOLD']     # seconds Alt is held before/after the click\n"
    "TAB_DELAY    = _S['TAB_DELAY']    # seconds after clicking a tab, before the alt+click\n"
    "STASH_DELAY  = _S['STASH_DELAY']  # seconds between each item moved\n")


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    errors = []
    changed_files = []

    for fname, patches in PATCHES.items():
        path = os.path.join(HERE, fname)
        if not os.path.exists(path):
            errors.append(f'{fname}: file not found at {path}')
            continue

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Idempotency: if already patched (import line present), skip cleanly.
        if 'from dragon_settings import get_settings' in content:
            print(f'{fname}: already patched — skipping.')
            continue

        original_content = content
        file_ok = True
        for old, new in patches:
            count = content.count(old)
            if count != 1:
                errors.append(
                    f'{fname}: expected exactly 1 occurrence of a known block, found {count}. '
                    f'Left file untouched. First 60 chars of the block: {old[:60]!r}')
                file_ok = False
                continue
            content = content.replace(old, new, 1)

        if not file_ok:
            continue

        backup_path = os.path.join(BACKUP_DIR, fname)
        if not os.path.exists(backup_path):
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(original_content)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        changed_files.append(path)
        print(f'{fname}: patched OK ({len(patches)} block(s)).')

    if errors:
        print('\n--- ERRORS (these files were NOT modified) ---')
        for e in errors:
            print(' -', e)

    print('\n--- py_compile check ---')
    compile_errors = []
    for path in changed_files:
        try:
            py_compile.compile(path, doraise=True)
            print(f'OK: {os.path.basename(path)}')
        except py_compile.PyCompileError as e:
            compile_errors.append(str(e))
            print(f'SYNTAX ERROR in {os.path.basename(path)}:\n{e}')

    if errors or compile_errors:
        print('\nDone WITH problems — see above.')
        sys.exit(1)
    print(f'\nDone. {len(changed_files)} file(s) patched successfully.')


if __name__ == '__main__':
    main()
