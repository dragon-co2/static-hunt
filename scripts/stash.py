import tkinter as tk
import random
import threading
import time
import os
import ctypes
import numpy as np
import cv2
import mss
import pyautogui

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
INVENTORY_TITLE_PATH = os.path.join(_DIR, 'inventory_title.jpg')
WAREHOUSE_TITLE_PATH = os.path.join(_DIR, 'warehouse_title.jpg')
ARROW_PATH           = os.path.join(_DIR, 'arrow.jpg')
EMPTY_PATH           = os.path.join(_DIR, 'emptycell.jpg')

INV_OFFSET_X = 18
INV_OFFSET_Y = 10
INV_SLOT_W   = 43
INV_SLOT_H   = 43
INV_COLS     = 5
INV_ROWS     = 8

WH_OFFSET_X  = 120
WH_OFFSET_Y  = 40
WH_SLOT_W    = 43
WH_SLOT_H    = 43
WH_COLS      = 5
WH_ROWS      = 4

WH_TAB_X     = 5
WH_TAB_Y     = 26
WH_TAB_W     = 110
WH_TAB_H     = 31
WH_TAB_COUNT = 7

CONF         = 0.4
CONF_WH      = 0.4
CONF_EMPTY   = 0.95
STASH_DELAY  = 1
VIS          = 0

_tmpl_inv   = cv2.imread(INVENTORY_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_wh    = cv2.imread(WAREHOUSE_TITLE_PATH, cv2.IMREAD_GRAYSCALE)
_tmpl_arrow = cv2.imread(ARROW_PATH,           cv2.IMREAD_GRAYSCALE)
_tmpl_empty = cv2.imread(EMPTY_PATH,           cv2.IMREAD_GRAYSCALE)


def _find(gray, template, threshold=CONF):
    if template is None:
        return None
    th, tw = template.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, val, _, loc = cv2.minMaxLoc(res)
    if val >= threshold:
        return (loc[0], loc[1], tw, th)
    return None


def _inv_cell_color(cell_gray):
    th, tw = _tmpl_empty.shape[:2]
    if cell_gray.shape[0] >= th and cell_gray.shape[1] >= tw:
        res = cv2.matchTemplate(cell_gray, _tmpl_empty, cv2.TM_CCOEFF_NORMED)
        if cv2.minMaxLoc(res)[1] >= CONF_EMPTY:
            return '#444444'
    th, tw = _tmpl_arrow.shape[:2]
    if cell_gray.shape[0] >= th and cell_gray.shape[1] >= tw:
        res = cv2.matchTemplate(cell_gray, _tmpl_arrow, cv2.TM_CCOEFF_NORMED)
        if cv2.minMaxLoc(res)[1] >= CONF:
            return '#ff0000'
    return '#00ff00'


def stash_items(wait_if_dead=None):
    """Called by navigation.py — drains all green inventory cells into warehouse."""
    moved = 0
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        while True:
            if wait_if_dead:
                wait_if_dead()
            raw  = np.array(sct.grab(mon))
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            inv_panel = _find(gray, _tmpl_inv)
            wh_panel  = _find(gray, _tmpl_wh, threshold=CONF_WH)
            if not inv_panel or not wh_panel:
                print('[STASH] Panel(s) not visible — aborting')
                return
            ipx, ipy, _, _ = inv_panel
            iox = ipx + INV_OFFSET_X
            ioy = ipy + INV_OFFSET_Y
            green = None
            for r in range(INV_ROWS):
                for c in range(INV_COLS):
                    x1 = int(iox + c * INV_SLOT_W)
                    y1 = int(ioy + r * INV_SLOT_H)
                    crop = gray[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W]
                    color = _inv_cell_color(crop)
                    if color == '#444444':
                        break
                    if color == '#00ff00':
                        green = (x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2)
                        break
                if green:
                    break
            if not green:
                print(f'[STASH] Done — {moved} item(s) moved')
                return
            wpx, wpy, _, _ = wh_panel
            wh_cx   = int(wpx + WH_OFFSET_X + (WH_COLS * WH_SLOT_W) // 2)
            wh_cy   = int(wpy + WH_OFFSET_Y + (WH_ROWS * WH_SLOT_H) // 2)
            tab_idx = random.choice(range(WH_TAB_COUNT))
            tab_x   = int(wpx + WH_TAB_X + WH_TAB_W // 2)
            tab_y   = int(wpy + WH_TAB_Y + tab_idx * WH_TAB_H + WH_TAB_H // 2)
            pyautogui.moveTo(green[0], green[1], duration=0.05)
            pyautogui.click()
            time.sleep(0.3)
            pyautogui.moveTo(tab_x, tab_y, duration=0.05)
            pyautogui.click()
            time.sleep(0.3)
            pyautogui.moveTo(wh_cx, wh_cy, duration=0.05)
            pyautogui.click()
            time.sleep(0.3)
            moved += 1
            print(f'  [STASH] Moved {moved}: inv{green} -> tab{tab_idx} -> wh({wh_cx},{wh_cy})')


def _scan_cells(gray, panel, offset_x, offset_y, slot_w, slot_h, cols, rows, classify):
    px, py, _, _ = panel
    ox = px + offset_x
    oy = py + offset_y
    cells = []
    hit_empty = False
    for r in range(rows):
        for c in range(cols):
            x1 = ox + c * slot_w
            y1 = oy + r * slot_h
            if hit_empty:
                color = '#444444'
            else:
                crop = gray[y1:y1 + slot_h, x1:x1 + slot_w]
                color = classify(crop)
                if color == '#444444':
                    hit_empty = True
            cells.append((x1, y1, slot_w, slot_h, color))
    return cells


def _make_overlay_items(canvas):
    inv_border = canvas.create_rectangle(0, 0, 0, 0, outline='', width=3)
    inv_label  = canvas.create_text(0, 0, text='', fill='#00ff00',
                                    font=('Arial', 9, 'bold'), anchor='nw')
    inv_cells  = [canvas.create_rectangle(0, 0, 0, 0, outline='', fill='',
                                          stipple='gray25', width=2)
                  for _ in range(INV_COLS * INV_ROWS)]
    wh_border  = canvas.create_rectangle(0, 0, 0, 0, outline='', width=3)
    wh_label   = canvas.create_text(0, 0, text='', fill='#00aaff',
                                    font=('Arial', 9, 'bold'), anchor='nw')
    wh_tabs    = [(canvas.create_rectangle(0, 0, 0, 0, outline='', width=2),
                   canvas.create_text(0, 0, text='', fill='#ffaa00',
                                      font=('Arial', 7), anchor='nw'))
                  for _ in range(WH_TAB_COUNT)]
    wh_cells   = [canvas.create_rectangle(0, 0, 0, 0, outline='', fill='',
                                          stipple='gray25', width=2)
                  for _ in range(WH_COLS * WH_ROWS)]
    return inv_border, inv_label, inv_cells, wh_border, wh_label, wh_tabs, wh_cells


def _update_overlay(canvas, items, inv_panel, wh_panel, inv_cells, wh_cells):
    inv_border, inv_label, inv_cell_ids, wh_border, wh_label, wh_tab_ids, wh_cell_ids = items

    if inv_panel:
        px, py, pw, ph = inv_panel
        canvas.coords(inv_border, px, py, px + pw, py + ph)
        canvas.itemconfig(inv_border, outline='#00ff00', state='normal')
        canvas.coords(inv_label, px, py - 14)
        canvas.itemconfig(inv_label, text=f'inv {pw}x{ph} @ ({px},{py})', state='normal')
        for i, (x1, y1, sw, sh, color) in enumerate(inv_cells):
            canvas.coords(inv_cell_ids[i], x1, y1, x1 + sw, y1 + sh)
            canvas.itemconfig(inv_cell_ids[i], outline=color, fill=color, state='normal')
        for i in range(len(inv_cells), len(inv_cell_ids)):
            canvas.itemconfig(inv_cell_ids[i], state='hidden')
    else:
        canvas.itemconfig(inv_border, state='hidden')
        canvas.itemconfig(inv_label, state='hidden')
        for cid in inv_cell_ids:
            canvas.itemconfig(cid, state='hidden')

    if wh_panel:
        px, py, pw, ph = wh_panel
        canvas.coords(wh_border, px, py, px + pw, py + ph)
        canvas.itemconfig(wh_border, outline='#00aaff', state='normal')
        canvas.coords(wh_label, px, py - 14)
        canvas.itemconfig(wh_label, text=f'wh {pw}x{ph} @ ({px},{py})', state='normal')
        for t, (rect_id, text_id) in enumerate(wh_tab_ids):
            tx = px + WH_TAB_X
            ty = py + WH_TAB_Y + t * WH_TAB_H
            canvas.coords(rect_id, tx, ty, tx + WH_TAB_W, ty + WH_TAB_H)
            canvas.itemconfig(rect_id, outline='#ffaa00', state='normal')
            canvas.coords(text_id, tx + 4, ty + 4)
            canvas.itemconfig(text_id, text=str(t), state='normal')
        for i, (x1, y1, sw, sh, _) in enumerate(wh_cells):
            canvas.coords(wh_cell_ids[i], x1, y1, x1 + sw, y1 + sh)
            canvas.itemconfig(wh_cell_ids[i], outline='#00aaff', fill='#00aaff', state='normal')
        for i in range(len(wh_cells), len(wh_cell_ids)):
            canvas.itemconfig(wh_cell_ids[i], state='hidden')
    else:
        canvas.itemconfig(wh_border, state='hidden')
        canvas.itemconfig(wh_label, state='hidden')
        for rect_id, text_id in wh_tab_ids:
            canvas.itemconfig(rect_id, state='hidden')
            canvas.itemconfig(text_id, state='hidden')
        for cid in wh_cell_ids:
            canvas.itemconfig(cid, state='hidden')


def _scan_loop(canvas, root, overlay_items):
    prev_state = [None]
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        while True:
            raw  = np.array(sct.grab(mon))
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
            inv_panel = _find(gray, _tmpl_inv)
            wh_panel  = _find(gray, _tmpl_wh, threshold=CONF_WH)
            inv_cells = _scan_cells(gray, inv_panel,
                                    INV_OFFSET_X, INV_OFFSET_Y,
                                    INV_SLOT_W, INV_SLOT_H,
                                    INV_COLS, INV_ROWS,
                                    _inv_cell_color) if inv_panel else []
            wh_cells  = _scan_cells(gray, wh_panel,
                                    WH_OFFSET_X, WH_OFFSET_Y,
                                    WH_SLOT_W, WH_SLOT_H,
                                    WH_COLS, WH_ROWS,
                                    lambda _: '#444444') if wh_panel else []
            state = (inv_panel, wh_panel, inv_cells, wh_cells)
            if state == prev_state[0]:
                time.sleep(0.3)
                continue
            prev_state[0] = state
            def draw(inv_panel=inv_panel, wh_panel=wh_panel,
                     inv_cells=inv_cells, wh_cells=wh_cells):
                _update_overlay(canvas, overlay_items, inv_panel, wh_panel, inv_cells, wh_cells)
            root.after(0, draw)
            time.sleep(0.3)


def _stash_loop():
    print('Starting in 3s...')
    for i in range(3, 0, -1):
        print(f'  {i}...')
        time.sleep(1)

    run = 0
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        while True:
            run += 1
            moved = 0
            print(f'[STASH] Run #{run}')
            while True:
                raw  = np.array(sct.grab(mon))
                gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
                inv_panel = _find(gray, _tmpl_inv)
                wh_panel  = _find(gray, _tmpl_wh, threshold=CONF_WH)
                if not inv_panel or not wh_panel:
                    print('[STASH] Panel(s) not visible — skipping')
                    break
                ipx, ipy, _, _ = inv_panel
                iox = ipx + INV_OFFSET_X
                ioy = ipy + INV_OFFSET_Y
                green = None
                for r in range(INV_ROWS):
                    for c in range(INV_COLS):
                        x1 = int(iox + c * INV_SLOT_W)
                        y1 = int(ioy + r * INV_SLOT_H)
                        crop = gray[y1:y1 + INV_SLOT_H, x1:x1 + INV_SLOT_W]
                        color = _inv_cell_color(crop)
                        if color == '#444444':
                            break
                        if color == '#00ff00':
                            green = (x1 + INV_SLOT_W // 2, y1 + INV_SLOT_H // 2)
                            break
                    if green:
                        break
                if not green:
                    print(f'[STASH] No green cells — {moved} moved')
                    os._exit(0)
                    break
                wpx, wpy, _, _ = wh_panel
                wh_cx   = int(wpx + WH_OFFSET_X + (WH_COLS * WH_SLOT_W) // 2)
                wh_cy   = int(wpy + WH_OFFSET_Y + (WH_ROWS * WH_SLOT_H) // 2)
                tab_idx = random.choice(range(WH_TAB_COUNT))
                tab_x   = int(wpx + WH_TAB_X + WH_TAB_W // 2)
                tab_y   = int(wpy + WH_TAB_Y + tab_idx * WH_TAB_H + WH_TAB_H // 2)
                pyautogui.moveTo(green[0], green[1], duration=0.05)
                pyautogui.click()
                time.sleep(0.3)
                pyautogui.moveTo(tab_x, tab_y, duration=0.05)
                pyautogui.click()
                time.sleep(0.3)
                pyautogui.moveTo(wh_cx, wh_cy, duration=0.05)
                pyautogui.click()
                time.sleep(0.3)
                moved += 1
                print(f'  Moved {moved}: inv{green} -> tab{tab_idx} -> wh({wh_cx},{wh_cy})')
            print(f'[STASH] Waiting {STASH_DELAY}s...')
            time.sleep(STASH_DELAY)


if __name__ == '__main__':
    if VIS:
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
        root.attributes('-topmost', True)
        root.attributes('-transparentcolor', 'black')
        root.configure(bg='black')

        canvas = tk.Canvas(root, bg='black', highlightthickness=0)
        canvas.pack(fill='both', expand=True)

        overlay_items = _make_overlay_items(canvas)

        def _set_clickthrough():
            hwnd = root.winfo_id()
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20)
            # Re-apply black colorkey so transparency isn't lost after style change
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 0, 1)

        root.after(200, _set_clickthrough)

        threading.Thread(target=_scan_loop,  args=(canvas, root, overlay_items), daemon=True).start()
        threading.Thread(target=_stash_loop, daemon=True).start()

        root.mainloop()
    else:
        _stash_loop()
