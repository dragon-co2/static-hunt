import pyautogui
import time
import math
import re
import random
import ctypes
import ctypes.wintypes
import struct
import pytesseract
import os
import threading
from pynput import keyboard as pynput_kb
from stash import stash_items
from bank_compose import run_compose
from repaire import run_repair

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

# Path to tesseract.exe — adjust if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# ── Target stops ──────────────────────────────────────────────
# Waypoints visited in order each navigation run. Add more tuples to expand the route.
NAV_STOPS = [
    #red devel
    # (274,135),(274,165),(297,152),(308,156),(297,165)
    #cave bull monster top
    # (162,451),(153,389),(194,408),(178,380),(153,431),
    #cave bull monster bottom
    # (208,410),(215,431),(236,430),(235,406)
    # (239,409),(213,431),(239,409),(201,399),(167,396),(165,445),(167,396),
    #desert
    (700,723), (787, 644), (799,726), (869,744),(826,670),(627,559),(617,703),(737,756),(644,622),(592,539)
    #alien serpent
    # (664,541),(902,571),(685,773),(902,571),(589,419),(733,449),(589,419),(536,287)   ,   (608,335),(588,415),(499, 319), (682,402) , (600,477),(688, 470), (593,714), (865,572),
     #cave bats
    # (254,305),(259,335),(284,320), (316,334), (284,320)
    #ape
    # (436,275),(532,190),(679,443),(425,187),(467,407) #giant ape
    #phonix
    # (521,534), (307,334), (154,331),(463,255),(356,382)
    #bird
    # (720, 635), (726, 697), (656,677), (650, 719),(687,763), (807, 643), (722, 724)
    #birds(island)
    # (451,680),(490,779),(528,758),(559,767),(509,685),(453,721),(500,772),(544,728),
    # bird bandits
    # (331,323),(453,369),(381,331),(446,568),(541,488),(359,426),(560,534),(523,449),(459,487),(351,396)

]
TARGET_X = NAV_STOPS[0][0]   # used by the standalone navigate_to() below
TARGET_Y = NAV_STOPS[0][1]

# ── Coord OCR region ──────────────────────────────────────────
# Pixel region (left, top, width, height) of the coordinate text in-game.
# From the screenshot the text "[DragonConquer] (495,666) | Ping..." sits
# in the very top-left strip.  Adjust if the game window is not full-screen.
COORD_REGION = (110, 0, 177, 17)   # (left, top, width, height)

# ── Coord source ──────────────────────────────────────────────
MEM_WINDOW     = 'GhostArrow'  # partial game window title for finding the process

# ── Tuning ────────────────────────────────────────────────────
ARRIVE_THRESHOLD = 10    # stop when within this many map units
CLICK_INTERVAL   = 0.3  # seconds between each movement click
MOUSE_SPEED      = 0.1
PLAYER_Y_OFFSET  = 100  # same as archer_orig — player below screen center
MAP_SCALE        = 1  # screen pixels per map unit (tune if overshooting)
NAV_CTRL_CYCLE   = 1    # 1 = alternate ctrl on/off each second while navigating, 0 = never press ctrl
NAV_CTRL_HOLD    = 7.0  # seconds ctrl stays pressed during each nav cycle
NAV_CTRL_RELEASE = 0.0  # seconds ctrl stays released during each nav cycle
NAV_MOUSE_CAP    = 0.25  # max mouse distance from player as a fraction of the smaller screen dimension (0.0–0.5)
NAV_SOUTH_MOUSE_CAP_MULT = 0.2  # multiplies NAV_MOUSE_CAP when heading south (screen sy > 0), keeping clicks further from the bottom hotbar
NAV_JITTER       = 60   # ± px of random spread added to each nav click (perpendicular spread)
STASH_DELAY      = 25    # seconds between stash-only runs
COMPOSE_DELAY    = 600   # seconds between bank compose runs (stash always follows compose)
REPAIR_DELAY     = 1000    # seconds between repair runs
SWITCH_RANDOM_DURATION = 0  # seconds spent in random mode before switching to ordered mode, and vice versa
STUCK_CHECK_INTERVAL = 2  # seconds — if coords haven't moved enough in this window, abandon the stop
STUCK_MOVE_THRESHOLD = 10  # map units — must move at least this far within STUCK_CHECK_INTERVAL or we're "stuck"

# ── Excluded UI zones ─────────────────────────────────────────
# Nav clicks that land inside these boxes are pushed just outside the left edge.
# Keep in sync with EXCLUDE_BOXES in archer_orig.py.
EXCLUDE_BOXES = [
    (445, 937, 1471, 1079),   # bottom hotbar
    (0, 0, 650, 22),   # top hotbar
    (0, 0, 120, 1080), # left edge
]

_DIR              = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
SPEED_PATH        = os.path.join(_DIR, 'speed.jpg')
REVIVE_PATH       = os.path.join(_DIR, 'revive.jpg')
ITEMS_BUTTON_PATH = os.path.join(_DIR, 'items_button.jpg')
CANCEL2_PATH      = os.path.join(_DIR, 'cancel2.jpg')   # "Are you sure you want to drop it?" popup
CANCEL2_CANCEL_OFF = (239, 91)   # Cancel button, relative to popup top-left
CHAT_PATH         = os.path.join(_DIR, 'chat.jpg')      # incoming chat/whisper window
CHAT_CLOSE_OFF    = (404, 300)   # Close button, relative to window top-left
CHAT2_PATH        = os.path.join(_DIR, 'chat2.png')     # chat settings panel
CHAT2_CLOSE_OFF   = (178, 203)   # Close button, relative to window top-left
NO_PATH           = os.path.join(_DIR, 'no.png')        # "No" confirm button

_nav_paused = threading.Event()   # set while waiting to revive
_manual_paused = threading.Event()  # set while paused via Ctrl+C hotkey
_reviving = threading.Event()  # set while a revive is in progress — stashing must wait
_action_lock = threading.Lock()  # held while stashing/composing or repairing — keeps the two from overlapping
_shift_held = False

def _on_key_release(key):
    global _shift_held
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_r):
        _shift_held = False

def _on_key_press(key):
    global _shift_held
    if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_r):
        _shift_held = True
        return
    try:
        if key.char == '\x03':   # Ctrl+C via pynput
            if _shift_held:
                print("  [HOTKEY] Ctrl+Shift+C — exiting")
                os._exit(0)
            elif _manual_paused.is_set():
                _manual_paused.clear()
                _nav_paused.clear()
                print("  [HOTKEY] Ctrl+C — resuming")
            else:
                _manual_paused.set()
                _nav_paused.set()
                pyautogui.keyUp('ctrl')
                print("  [HOTKEY] Ctrl+C — paused")
            return
    except AttributeError:
        pass
    if key == pynput_kb.Key.space:
        try:
            loc = pyautogui.locateOnScreen(ITEMS_BUTTON_PATH, confidence=0.8, grayscale=True)
            if loc:
                bx, by = pyautogui.center(loc)
                pyautogui.keyUp('ctrl')
                pyautogui.moveTo(bx, by, duration=MOUSE_SPEED)
                pyautogui.click()
                print(f"  [SPACE] items_button clicked at ({bx},{by})")
            else:
                print("  [SPACE] items_button not found on screen")
        except Exception as e:
            print(f"  [SPACE] error: {e}")

pynput_kb.Listener(on_press=_on_key_press, on_release=_on_key_release, daemon=True).start()

# ── Memory reading ────────────────────────────────────────────

_game_handle = None

def _open_game_process():
    global _game_handle
    found_pid = ctypes.c_ulong(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        if MEM_WINDOW.lower() in buf.value.lower():
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
            return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    pid = found_pid.value
    if pid:
        _game_handle = ctypes.windll.kernel32.OpenProcess(0x0010, False, pid)
        print(f"  [MEM] Opened game process PID={pid}  handle={_game_handle}")
    else:
        print(f"  [MEM] Game window '{MEM_WINDOW}' not found")

def _read_int32(address):
    global _game_handle
    if not _game_handle:
        _open_game_process()
    if not _game_handle:
        return None
    buf = ctypes.create_string_buffer(4)
    read = ctypes.c_size_t(0)
    ok = ctypes.windll.kernel32.ReadProcessMemory(
        _game_handle, ctypes.c_void_p(address), buf, 4, ctypes.byref(read)
    )
    if not ok or read.value != 4:
        _game_handle = None  # stale handle — will reopen next call
        return None
    return struct.unpack('<i', buf)[0]

# ── Helpers ───────────────────────────────────────────────────

def _locate(path, confidence=0.7):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except Exception:
        return None


def _wait_if_dead():
    """Blocks while a revive is in progress — passed into stash/repair so they pause mid-action."""
    while _reviving.is_set():
        time.sleep(0.5)

def _scan_and_handle():
    while True:
        # Speed — checked independently
        if _locate(SPEED_PATH):
            print("  [SPEED] Found — pressing F9 until gone")
            while _locate(SPEED_PATH):
                pyautogui.press('f9')
                time.sleep(0.3)
            print("  [SPEED] Gone")

        # Revive — checked even if nav is already paused (e.g. mid-stash/repair),
        # so a death during those actions still gets caught.
        loc = _locate(REVIVE_PATH)
        if loc and not _reviving.is_set():
            was_paused = _nav_paused.is_set()
            _reviving.set()
            _nav_paused.set()
            rx, ry = pyautogui.center(loc)
            print(f"  [REVIVE] Died — hovering at ({rx},{ry}), waiting 1 minute...")
            pyautogui.moveTo(rx, ry, duration=MOUSE_SPEED)
            time.sleep(360)  # wait for revive button to appear
            loc2 = _locate(REVIVE_PATH)
            if loc2:
                rx2, ry2 = pyautogui.center(loc2)
                pyautogui.click(rx2, ry2)
                print("  [REVIVE] Clicked!")
            if not was_paused:
                _nav_paused.clear()   # only resume nav if we're the one who paused it
            _reviving.clear()

        # Accidental "are you sure you want to drop it?" popup — click Cancel
        loc = _locate(CANCEL2_PATH, confidence=0.9)
        if loc:
            cx, cy = loc.left + CANCEL2_CANCEL_OFF[0], loc.top + CANCEL2_CANCEL_OFF[1]
            pyautogui.moveTo(cx, cy, duration=MOUSE_SPEED)
            pyautogui.click()
            print(f"  [POPUP] drop-confirm dialog — clicked Cancel at ({cx},{cy})")

        # Accidental incoming chat window — click Close
        loc = _locate(CHAT_PATH, confidence=0.9)
        if loc:
            cx, cy = loc.left + CHAT_CLOSE_OFF[0], loc.top + CHAT_CLOSE_OFF[1]
            pyautogui.moveTo(cx, cy, duration=MOUSE_SPEED)
            pyautogui.click()
            print(f"  [POPUP] chat window — clicked Close at ({cx},{cy})")

        # Accidental chat settings panel — click Close
        loc = _locate(CHAT2_PATH, confidence=0.9)
        if loc:
            cx, cy = loc.left + CHAT2_CLOSE_OFF[0], loc.top + CHAT2_CLOSE_OFF[1]
            pyautogui.moveTo(cx, cy, duration=MOUSE_SPEED)
            pyautogui.click()
            print(f"  [POPUP] chat2 panel — clicked Close at ({cx},{cy})")

        # Accidental confirmation popup — click No
        loc = _locate(NO_PATH, confidence=0.8)
        if loc:
            nx, ny = pyautogui.center(loc)
            pyautogui.moveTo(nx, ny, duration=MOUSE_SPEED)
            pyautogui.click()
            print(f"  [POPUP] No button — clicked at ({nx},{ny})")

        time.sleep(1.0)


def _safe_nav_click(x, y):
    for x1, y1, x2, y2 in EXCLUDE_BOXES:
        if x1 <= x <= x2 and y1 <= y <= y2:
            x = x1 - 5
            break
    return int(x), int(y)

def get_current_coords():

    # OCR fallback
    img = pyautogui.screenshot(region=COORD_REGION)
    img = img.resize((img.width * 3, img.height * 3))
    text = pytesseract.image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789(),[]| ')
    m = re.search(r'\((\d+),\s*(\d+)\)', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def debug_ocr():
    """Print what OCR reads from the coord region — use to tune COORD_REGION."""
    img = pyautogui.screenshot(region=COORD_REGION)
    img = img.resize((img.width * 3, img.height * 3))
    text = pytesseract.image_to_string(img, config='--psm 7')
    print(f"  OCR raw text: {repr(text)}")


def map_to_screen_delta(dx_map, dy_map):
    sx = (dx_map - dy_map) * MAP_SCALE
    sy = (dx_map + dy_map) * MAP_SCALE * 0.5
    return sx, sy


def navigate_to(target_x, target_y, stop_label='', jitter=NAV_JITTER):
    screen_w, screen_h = pyautogui.size()
    cx = screen_w // 2
    cy = screen_h // 2 + PLAYER_Y_OFFSET
    edge_cap = min(screen_w, screen_h) * NAV_MOUSE_CAP

    print(f"  [NAV] Heading to {stop_label}({target_x},{target_y})  — top-left to abort")

    stuck_ref_pos  = None
    stuck_ref_time = time.time()

    while True:
        if _nav_paused.is_set():
            time.sleep(0.5)
            continue

        coords = get_current_coords()
        if coords is None:
            print("  [NAV] Can't read coords, retrying...")
            jx = random.randint(-150, 150)
            jy = random.randint(-150, 150)
            click_x, click_y = _safe_nav_click(
                max(10, min(screen_w - 10, cx + jx)),
                max(10, min(screen_h - 10, cy + jy)),
            )
            pyautogui.moveTo(click_x, click_y, duration=MOUSE_SPEED)
            pyautogui.click()
            time.sleep(0.5)
            continue

        cur_x, cur_y = coords
        dx_map = target_x - cur_x
        dy_map = target_y - cur_y
        dist   = math.hypot(dx_map, dy_map)

        print(f"  [NAV] pos=({cur_x},{cur_y})  →({target_x},{target_y})  dist={dist:.1f}")

        now = time.time()
        if stuck_ref_pos is None:
            stuck_ref_pos, stuck_ref_time = (cur_x, cur_y), now
        elif now - stuck_ref_time >= STUCK_CHECK_INTERVAL:
            moved = math.hypot(cur_x - stuck_ref_pos[0], cur_y - stuck_ref_pos[1])
            if moved < STUCK_MOVE_THRESHOLD:
                print(f"  [NAV] Stuck — barely moved in {STUCK_CHECK_INTERVAL}s, abandoning this stop")
                break
            stuck_ref_pos, stuck_ref_time = (cur_x, cur_y), now

        if dist < ARRIVE_THRESHOLD:
            print(f"  [NAV] Arrived at {stop_label}({target_x},{target_y})!")
            break

        sx, sy = map_to_screen_delta(dx_map, dy_map)
        mag    = math.hypot(sx, sy) or 1
        cap    = edge_cap * NAV_SOUTH_MOUSE_CAP_MULT if sy > 0 else edge_cap
        sx     = sx / mag * cap
        sy     = sy / mag * cap

        jx = random.randint(-jitter, jitter)
        jy = random.randint(-jitter, jitter)
        click_x, click_y = _safe_nav_click(
            max(10, min(screen_w - 10, int(cx + sx + jx))),
            max(10, min(screen_h - 10, int(cy + sy + jy))),
        )

        if NAV_CTRL_CYCLE:
            _cycle = NAV_CTRL_HOLD + NAV_CTRL_RELEASE
            if (time.time() % _cycle) < NAV_CTRL_HOLD:
                pyautogui.keyDown('ctrl')
            else:
                pyautogui.keyUp('ctrl')
        else:
            pyautogui.keyUp('ctrl')

        try:
            pyautogui.moveTo(click_x, click_y, duration=MOUSE_SPEED)
            pyautogui.click()
        except pyautogui.FailSafeException:
            print("  [NAV] Emergency stop!")
            pyautogui.keyUp('ctrl')
            return False

        time.sleep(CLICK_INTERVAL)

    return True


def navigate_route():
    if not NAV_STOPS:
        print("  [NAV] NAV_STOPS is empty — nothing to navigate.")
        return

    print(f"=== Route: {len(NAV_STOPS)} stops ===")
    for i, (tx, ty) in enumerate(NAV_STOPS):
        label = f"stop {i+1}/{len(NAV_STOPS)} "
        ok = navigate_to(tx, ty, stop_label=label)
        if not ok:
            print("  [NAV] Route aborted.")
            return

    print("=== Route complete! ===")


if __name__ == '__main__':
    _running = True

    threading.Thread(target=_scan_and_handle, daemon=True).start()

    def _click_loop():
        while _running:
            if _nav_paused.is_set():
                time.sleep(0.1)
                continue
            try:
                pyautogui.click(button='right')
                pyautogui.click(button='left')
            except pyautogui.FailSafeException:
                os._exit(0)
            time.sleep(CLICK_INTERVAL)

    threading.Thread(target=_click_loop, daemon=True).start()

    def _f10_loop():
        while True:
            time.sleep(60)
            pyautogui.press('f10')
            print("  [F10] pressed")

    threading.Thread(target=_f10_loop, daemon=True).start()

    def _stash_loop():
        next_stash   = time.time() + STASH_DELAY
        next_compose = time.time() + COMPOSE_DELAY

        while True:
            time.sleep(1.0)
            now          = time.time()
            compose_due  = now >= next_compose
            stash_due    = now >= next_stash

            if not compose_due and not stash_due:
                continue

            while _reviving.is_set():
                time.sleep(0.5)
            with _action_lock:
                _nav_paused.set()
                time.sleep(0.5)
                pyautogui.keyUp('ctrl')

                if compose_due:
                    print("[COMPOSE] Running bank compose...")
                    try:
                        run_compose()
                    except Exception as e:
                        print(f"[COMPOSE] Error: {e}")
                    next_compose = time.time() + COMPOSE_DELAY
                    stash_due = True   # always stash after compose

                if stash_due:
                    print("[STASH] Running stash...")
                    try:
                        stash_items(wait_if_dead=_wait_if_dead)
                    except Exception as e:
                        print(f"[STASH] Error: {e}")
                    next_stash = time.time() + STASH_DELAY

                time.sleep(1.0)
                screen_w, screen_h = pyautogui.size()
                rand_x = screen_w // 2 + random.randint(-300, 300)
                rand_y = screen_h // 2 + random.randint(-300, 300)
                pyautogui.moveTo(rand_x, rand_y, duration=MOUSE_SPEED)
                pyautogui.keyDown('ctrl')
                pyautogui.click()
                _nav_paused.clear()
                print("[STASH] Done — resuming navigation")

    threading.Thread(target=_stash_loop, daemon=True).start()

    def _repair_loop():
        next_repair = time.time() + REPAIR_DELAY

        while True:
            time.sleep(1.0)
            if time.time() < next_repair:
                continue

            while _reviving.is_set():
                time.sleep(0.5)
            with _action_lock:
                _nav_paused.set()
                time.sleep(0.5)
                pyautogui.keyUp('ctrl')

                print("[REPAIR] Running repair...")
                try:
                    run_repair(wait_if_dead=_wait_if_dead)
                except Exception as e:
                    print(f"[REPAIR] Error: {e}")
                next_repair = time.time() + REPAIR_DELAY

                time.sleep(1.0)
                screen_w, screen_h = pyautogui.size()
                rand_x = screen_w // 2 + random.randint(-300, 300)
                rand_y = screen_h // 2 + random.randint(-300, 300)
                pyautogui.moveTo(rand_x, rand_y, duration=MOUSE_SPEED)
                pyautogui.keyDown('ctrl')
                pyautogui.click()
                _nav_paused.clear()
                print("[REPAIR] Done — resuming navigation")

    threading.Thread(target=_repair_loop, daemon=True).start()

    print("=== Nav Farm Bot ===")
    print("Move mouse to TOP-LEFT to emergency stop.")
    print("Starting in 3 seconds — switch to the game window...")
    time.sleep(3)

    try:
        ordered_i = 0
        start_time = time.time()
        while True:
            in_random = False
            if SWITCH_RANDOM_DURATION > 0:
                _cycle = (time.time() - start_time) % (2 * SWITCH_RANDOM_DURATION)
                in_random = _cycle < SWITCH_RANDOM_DURATION
            if in_random:
                tx, ty = random.choice(NAV_STOPS)
                label = 'random stop '
            else:
                tx, ty = NAV_STOPS[ordered_i % len(NAV_STOPS)]
                label = f'ordered stop {ordered_i % len(NAV_STOPS) + 1}/{len(NAV_STOPS)} '
                ordered_i += 1
            print(f"  [BOT] Stop: ({tx},{ty})")
            ok = navigate_to(tx, ty, stop_label=label)
            if not ok:
                raise SystemExit
    except (KeyboardInterrupt, SystemExit):
        os._exit(0)
