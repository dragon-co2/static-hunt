import os
import time
import threading
import ctypes
import tkinter as tk
import numpy as np
import cv2
import mss
import pyautogui
from pynput import keyboard as pynput_kb

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
# ── Templates ──────────────────────────────────────────────────────────────────
_tmpl_inv    = cv2.imread(os.path.join(_DIR, 'inventory_title.jpg'), cv2.IMREAD_GRAYSCALE)
_tmpl_dialog = cv2.imread(os.path.join(_DIR, 'compose_items.jpg'),  cv2.IMREAD_GRAYSCALE)

# Fixed offsets from the compose dialog's top-left corner.
# Tune if clicks land wrong — the print line shows dialog@(dx,dy) so you can verify.
DIALOG_MAIN_OFF    = (68,  80)   # center of the left (Main) slot
DIALOG_MINOR_OFF   = (200, 80)   # center of the right (Minor) slot
DIALOG_COMPOSE_OFF = (167, 260)  # center of the Compose button

# ── Inventory grid ─────────────────────────────────────────────────────────────
CONF_INV     = 0.3
INV_OFFSET_X = 18
INV_OFFSET_Y = 10
INV_SLOT_W   = 43
INV_SLOT_H   = 43
INV_COLS     = 5
INV_ROWS     = 8

WAIT = 0.7  # seconds between steps

# ── Badge configs ──────────────────────────────────────────────────────────────
def _load_badge(name, threshold, color):
    bgra = cv2.imread(os.path.join(_DIR, f'{name}.png'), cv2.IMREAD_UNCHANGED)
    bgr  = bgra[:, :, :3]
    mask = bgra[:, :, 3]
    n_px   = float(np.count_nonzero(mask)) * 3
    max_sq = n_px * (255.0 ** 2)

    def score(cell_bgr):
        th, tw = bgr.shape[:2]
        if cell_bgr.shape[0] < th or cell_bgr.shape[1] < tw:
            return 0.0
        res = cv2.matchTemplate(cell_bgr, bgr, cv2.TM_SQDIFF, mask=mask)
        return 1.0 - (float(cv2.minMaxLoc(res)[0]) / max_sq)

    return {'label': f'+{name[-1]}', 'score': score, 'threshold': threshold, 'color': color}

BADGES = [
    _load_badge('plus1', 0.95, '#00ff00'),  # green
    _load_badge('plus2', 0.96, '#ff0000'),  # red
    _load_badge('plus3', 0.96, '#ffff00'),  # yellow
    _load_badge('plus4', 0.96, '#00ccff'),  # cyan
]

# ── Detection helpers ──────────────────────────────────────────────────────────
def _find_panel(gray):
    if _tmpl_inv is None:
        return None
    th, tw = _tmpl_inv.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, _tmpl_inv, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    return (loc[0], loc[1], tw, th) if val >= CONF_INV else None


def _find_dialog(gray):
    """Returns top-left (x, y) of the Compose Items dialog, or None."""
    if _tmpl_dialog is None:
        return None
    th, tw = _tmpl_dialog.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, _tmpl_dialog, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    return (loc[0], loc[1]) if val >= 0.6 else None


def _find_badge_cell(raw, ox, oy, badge):
    """Returns center of the last inventory cell matching the badge, or None."""
    found = None
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = ox + c * INV_SLOT_W
            y1 = oy + r * INV_SLOT_H
            crop = raw[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W, :3].copy()
            if badge['score'](crop) >= badge['threshold']:
                found = (x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2)
    return found


def _count_badge_cells(raw, ox, oy, badge):
    count = 0
    for r in range(INV_ROWS):
        for c in range(INV_COLS):
            x1 = ox + c * INV_SLOT_W
            y1 = oy + r * INV_SLOT_H
            crop = raw[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W, :3].copy()
            if badge['score'](crop) >= badge['threshold']:
                count += 1
    return count

# ── Cached UI positions (set once, reused for all badge levels) ────────────────
_ui = {'main': None, 'minor': None, 'compose': None}

def _locate_ui():
    """Find the compose dialog and derive main/minor/compose from fixed offsets."""
    with mss.MSS() as sct:
        gray = cv2.cvtColor(np.array(sct.grab(sct.monitors[1])), cv2.COLOR_BGRA2GRAY)
    dlg = _find_dialog(gray)
    if not dlg:
        print('  [UI] compose dialog not found')
        return False
    dx, dy = dlg
    _ui['main']    = (dx + DIALOG_MAIN_OFF[0],    dy + DIALOG_MAIN_OFF[1])
    _ui['minor']   = (dx + DIALOG_MINOR_OFF[0],   dy + DIALOG_MINOR_OFF[1])
    _ui['compose'] = (dx + DIALOG_COMPOSE_OFF[0], dy + DIALOG_COMPOSE_OFF[1])
    print(f"  [UI] dialog@({dx},{dy})  main={_ui['main']}  minor={_ui['minor']}  compose={_ui['compose']}")
    return True

# ── 8-step cycle ───────────────────────────────────────────────────────────────
def _step(badge):
    label = badge['label']
    with mss.MSS() as sct:
        mon = sct.monitors[1]

        panel = None
        for _ in range(10):
            raw  = np.array(sct.grab(mon))
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            panel = _find_panel(gray)
            if panel:
                break
            time.sleep(1.0)
        if not panel:
            print(f'[{label}] Inventory not visible.')
            return False

        def fresh_item():
            r = np.array(sct.grab(mon))
            g = cv2.cvtColor(r, cv2.COLOR_BGRA2GRAY)
            p = _find_panel(g)
            if not p:
                return None
            return _find_badge_cell(r, p[0] + INV_OFFSET_X, p[1] + INV_OFFSET_Y, badge)

        px, py, _, _ = panel
        ox, oy = px + INV_OFFSET_X, py + INV_OFFSET_Y

        # S1 – select item
        item = _find_badge_cell(raw, ox, oy, badge)
        if not item:
            print(f'[{label}] No items found.')
            return False
        if _count_badge_cells(raw, ox, oy, badge) <= 2:
            print(f'[{label}] Only 1-2 items — skipping to next badge.')
            return False
        pyautogui.moveTo(*item, duration=0.05); pyautogui.click()
        print(f'  S1: {label} item at {item}'); time.sleep(WAIT)

        # S2 – main slot
        pyautogui.moveTo(*_ui['main'], duration=0.05); pyautogui.click()
        print(f'  S2: main at {_ui["main"]}'); time.sleep(WAIT)

        # Count after S2 — need at least 2 more items (S3 + S6)
        _r = np.array(sct.grab(mon))
        _p = _find_panel(cv2.cvtColor(_r, cv2.COLOR_BGRA2GRAY))
        if _p:
            _rem = _count_badge_cells(_r, _p[0] + INV_OFFSET_X, _p[1] + INV_OFFSET_Y, badge)
            print(f'[{label}] Items: {_rem}')
            if _rem <= 1:
                print(f'[{label}] Not enough items after S2 — moving to next badge.')
                return False

        # S3 – select item again
        item = fresh_item()
        if not item:
            print(f'[{label}] No item for S3.'); return False
        pyautogui.moveTo(*item, duration=0.05); pyautogui.click()
        print(f'  S3: {label} item at {item}'); time.sleep(WAIT)

        # S4 – minor slot
        pyautogui.moveTo(*_ui['minor'], duration=0.05); pyautogui.click()
        print(f'  S4: minor at {_ui["minor"]}'); time.sleep(WAIT)

        # S5 – compose
        pyautogui.moveTo(*_ui['compose'], duration=0.05); pyautogui.click()
        print(f'  S5: compose'); time.sleep(WAIT)

        # S6 – select item again
        item = fresh_item()
        if not item:
            print(f'[{label}] No item for S6.'); return False
        pyautogui.moveTo(*item, duration=0.05); pyautogui.click()
        print(f'  S6: {label} item at {item}'); time.sleep(WAIT)

        # S7 – minor slot
        pyautogui.moveTo(*_ui['minor'], duration=0.05); pyautogui.click()
        print(f'  S7: minor at {_ui["minor"]}'); time.sleep(WAIT)

        # S8 – compose
        pyautogui.moveTo(*_ui['compose'], duration=0.05); pyautogui.click()
        print(f'  S8: compose')
        return True


def run_badge(badge):
    """Loops the 8-step cycle until 0 or 1 items remain, then returns 'done'."""
    label = badge['label']
    n = 0
    while True:
        with mss.MSS() as sct:
            raw = np.array(sct.grab(sct.monitors[1]))
        gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        panel = _find_panel(gray)
        if panel:
            ox = panel[0] + INV_OFFSET_X
            oy = panel[1] + INV_OFFSET_Y
            count = _count_badge_cells(raw, ox, oy, badge)
            if count == 0:
                print(f'[{label}] No more items — moving to next badge.')
                return 'done'
            if count <= 2:
                print(f'[{label}] Only {count} item(s) — moving to next badge.')
                return 'done'
        else:
            print(f'[{label}] Panel not found — skipping.')
            return 'done'

        n += 1
        if not _step(badge):
            break
        time.sleep(WAIT)
    return 'done'

# ── Keyboard + action loop ─────────────────────────────────────────────────────
_ctrl_dn   = [False]
_next_iter = threading.Event()
_pending   = {'start': 0}


def _on_kb_press(key):
    if key in (pynput_kb.Key.ctrl_l, pynput_kb.Key.ctrl_r):
        _ctrl_dn[0] = True; return
    if not _ctrl_dn[0]:
        return
    vk = getattr(key, 'vk', None)
    if vk in (0x31, 0x32, 0x33, 0x34):
        idx = vk - 0x31
        print(f'[KEY] Ctrl+{idx + 1} — starting from {BADGES[idx]["label"]}')
        _pending['start'] = idx
        _next_iter.set()


def _on_kb_release(key):
    if key in (pynput_kb.Key.ctrl_l, pynput_kb.Key.ctrl_r):
        _ctrl_dn[0] = False


def _action_loop():
    while True:
        _next_iter.wait()
        _next_iter.clear()

        # Detect dialog position once before any items are placed into slots.
        print('[SEQ] Locating compose dialog...')
        for attempt in range(15):
            if _locate_ui():
                break
            print(f'  [UI] not ready ({attempt + 1}/15)')
            time.sleep(1.0)
        else:
            print('[SEQ] Could not find compose dialog — waiting for next trigger.')
            continue

        for badge in BADGES[_pending['start']:]:
            run_badge(badge)
        print('[SEQ] All done — waiting for next trigger.')

# ── Visualization overlay ──────────────────────────────────────────────────────
def _classify_cell(crop):
    for badge in BADGES:
        if badge['score'](crop) >= badge['threshold']:
            return badge['label'], badge['color']
    return None, None


def _scan_loop(canvas, root, border, cell_ids, cell_labels):
    prev = [None]
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        while True:
            raw   = np.array(sct.grab(mon))
            gray  = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            panel = _find_panel(gray)
            cells = []
            if panel:
                px, py, pw, ph = panel
                ox, oy = px + INV_OFFSET_X, py + INV_OFFSET_Y
                for r in range(INV_ROWS):
                    for c in range(INV_COLS):
                        x1 = ox + c * INV_SLOT_W
                        y1 = oy + r * INV_SLOT_H
                        crop = raw[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W, :3].copy()
                        tag, color = _classify_cell(crop)
                        cells.append((x1, y1, tag, color))
            state = (panel, tuple(t for _, _, t, _ in cells))
            if state == prev[0]:
                time.sleep(0.3); continue
            prev[0] = state

            def draw(panel=panel, cells=cells):
                if panel:
                    px, py, _, _ = panel
                    canvas.coords(border, px, py, px + pw, py + ph)
                    canvas.itemconfig(border, outline='#ffffff', state='normal')
                    for i, (x1, y1, tag, color) in enumerate(cells):
                        if tag:
                            canvas.coords(cell_ids[i], x1, y1, x1 + INV_SLOT_W, y1 + INV_SLOT_H)
                            canvas.itemconfig(cell_ids[i], outline=color, state='normal')
                            canvas.coords(cell_labels[i], x1 + 2, y1 + 2)
                            canvas.itemconfig(cell_labels[i], text=tag, state='normal', fill=color)
                        else:
                            canvas.itemconfig(cell_ids[i], state='hidden')
                            canvas.itemconfig(cell_labels[i], state='hidden')
                    for i in range(len(cells), len(cell_ids)):
                        canvas.itemconfig(cell_ids[i], state='hidden')
                        canvas.itemconfig(cell_labels[i], state='hidden')
                else:
                    canvas.itemconfig(border, state='hidden')
                    for cid in cell_ids:   canvas.itemconfig(cid, state='hidden')
                    for lid in cell_labels: canvas.itemconfig(lid, state='hidden')
            root.after(0, draw)
            time.sleep(0.3)


if __name__ == '__main__':
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    root.attributes('-topmost', True)
    root.attributes('-transparentcolor', 'black')
    root.configure(bg='black')

    canvas = tk.Canvas(root, bg='black', highlightthickness=0)
    canvas.pack(fill='both', expand=True)

    border      = canvas.create_rectangle(0, 0, 0, 0, outline='', width=3, state='hidden')
    cell_ids    = [canvas.create_rectangle(0, 0, 0, 0, outline='', width=2, state='hidden')
                   for _ in range(INV_COLS * INV_ROWS)]
    cell_labels = [canvas.create_text(0, 0, text='', font=('Arial', 9, 'bold'),
                                      anchor='nw', state='hidden')
                   for _ in range(INV_COLS * INV_ROWS)]

    def _set_clickthrough(_=None):
        hwnd = root.winfo_id()
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x00080000 | 0x00000020 | 0x08000000)
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, 1)

    root.bind('<Map>', lambda _: _set_clickthrough())
    root.after(200, _set_clickthrough)

    pynput_kb.Listener(on_press=_on_kb_press, on_release=_on_kb_release, daemon=True).start()
    threading.Thread(target=_scan_loop,
                     args=(canvas, root, border, cell_ids, cell_labels), daemon=True).start()
    threading.Thread(target=_action_loop, daemon=True).start()

    print('Waiting for Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4...')
    root.mainloop()
