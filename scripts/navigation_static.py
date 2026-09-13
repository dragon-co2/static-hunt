import os
import time
import math
import random
import re
import ctypes
import threading
import pytesseract
import pyautogui
from pynput import keyboard as pynput_kb

from stash2 import stash_items
from bank_compose import run_compose
from repaire import run_repair

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

# Path to tesseract.exe — adjust if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reference_images')
REVIVE_PATH = os.path.join(_DIR, 'revive.jpg')
SPEED_PATH  = os.path.join(_DIR, 'speed.jpg')


# ── Stops ────────────────────────────────────────────────────────
NAV_STOPS = [
#red devel
    # (321,167)
    # (523,205)
    #cave bull monster top
    # (214,357)
    # (177,411),(170,429),(163,450),(162,399),(202,399),(237,411),(218,427),(238,432),(202,399),(163,450)
    # (177,411),(170,429),(163,450),(183,412),(184,392),   (233,411),(236,431),(216,425),(207,400)  ,(184,392),  (184,366),(204,353),(222,363),(293,326),(260,339),(278,318),(313,332),(260,339),(282,303),(260,339),(226,361),(215,359),(189,343),(171,328),(162,312),(140,310),(113,299),(93,291),(92,315),(106,361),(132,375),(171,408),(172,428),
    #cave bull monster bottom
    # (343,354)
    # (208,410),(215,431),(236,430),(235,406)
    # (239,409),(213,431),(239,409),(201,399),(167,396),(165,445),(167,396),
    #desert
    # (700,723), (787, 644), (799,726), (869,744),(826,670),(627,559),(617,703),(737,756),(644,622),(592,539)
    #alien serpent
    # (664,541),(902,571),(685,773),(902,571),(589,419),(733,449),(589,419),(536,287)   ,   (608,335),(588,415),(499, 319), (682,402) , (600,477),(688, 470), (593,714), (865,572),
    #cave bats
    # (265,316),(267,340),(284,320), (304,333), (284,320)
    #(310,339)
    # (268,336),(263,315),(311,332),(280,321)
    #tomb_bat
    # (323,634),(328,608),(345,609),(353,592),(392,605),(396,628),(374,630),(368,600),(357,594),(348,608),(336,570),(350,555),(362,546),(332,527),(331,514),(306,505),(287,501),(271,485),(251,478),(251,497),(250,509),(265,512),(270,502),(247,513),(249,534),(266,552),(293,576),(323,604),
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
    
    
    #cave
    # (274,323),(258,306),(262,338),(280,336),(279,323),(309,332),(318,332),(305,342),(270,324),
    # (274,328),(256,343),(273,379),(314,409),(328,422),(328,472),(345,471),(345,444),(346,427),(358,427),(374,426),(395,433),(397,450),(381,440),(399,449),(393,431),(366,422),(350,444),(350,412),(346,385),(381,380),(401,377),(413,368),(395,383),(355,367),(349,341),(317,331),(294,326),(273,321),
    # (256,342),(231,366),(213,358),(176,332),(166,338),(185,314),(139,306),(117,302),(92,297),(93,283),(114,281),(90,317),(99,344),(118,370),(146,388),(168,402),(156,447),(173,445),(187,419),(193,405),(212,404),(174,402),(237,408),(233,431),(219,432),(214,401),(197,399),(191,411),(179,373),(179,354),(213,359),(240,349),(263,338),(272,324),


    #phonix_bird
    (61,54),#(55,37),(52,56)
    ]

# Safe spot(s) to stand at while running bank compose. Only visited once the bot
# reaches NAV_STOPS[-1] (the easiest stop to reach it from), and the character
# returns to NAV_STOPS[-1] afterward — the route then naturally continues on to
# NAV_STOPS[0] next. Leave empty to skip this and just compose in place, like before.
NAV_COMPOSE = [
    
    #cave bats
    # (240, 341)
    #bull
    # (154,463)
    #tomb_bat
    # (319,647)
    # (408,364)

    ]

# ── Coord OCR region ─────────────────────────────────────────────
# Top-left and bottom-right corners (x, y) of the coordinate text box in-game.
COORD_REGION_TOP_LEFT     = (133, 8)
COORD_REGION_BOTTOM_RIGHT = (189, 28)

COORD_REGION = (
    COORD_REGION_TOP_LEFT[0],
    COORD_REGION_TOP_LEFT[1],
    COORD_REGION_BOTTOM_RIGHT[0] - COORD_REGION_TOP_LEFT[0],
    COORD_REGION_BOTTOM_RIGHT[1] - COORD_REGION_TOP_LEFT[1],
)


# ── Tuning ───────────────────────────────────────────────────────
ARRIVE_THRESHOLD  = 10    # stop when within this many map units
CLICK_INTERVAL    = 0.2   # seconds between each movement click
MOUSE_SPEED       = 0.1
PLAYER_Y_OFFSET   = 0   # player sits below screen center
MAP_SCALE         = 1     # screen pixels per map unit (tune if overshooting)
NAV_MOUSE_CAP     = 0.25  # max mouse distance from player as a fraction of the smaller screen dimension
NAV_JITTER        = 120    # ± px of random spread added to each nav click
STUCK_CHECK_INTERVAL = 3  # seconds — if coords haven't moved enough in this window, abandon this stop
STUCK_MOVE_THRESHOLD = 10 # map units — must move at least this far within STUCK_CHECK_INTERVAL or we're "stuck"

RIGHT_CLICK_INTERVAL = 0.3  # seconds between right-clicks while navigating between stops; 0 disables it

HOLD_DURATION      = 0.2   # seconds to circle in place at each stop
HOLD_RADIUS_MIN    = 50   # px min distance from screen-center for the random circle
HOLD_RADIUS_MAX    = 300  # px max distance from screen-center for the random circle
HOLD_STEP_INTERVAL = 0.5  # seconds between each mouse move + right-click while holding

REVIVE_WAIT   = 30   # seconds to wait after death before checking for the revive button

STASH_DELAY   = 10    # seconds between stash-only runs
COMPOSE_DELAY = 1200   # seconds between bank compose runs (stash always follows compose)
REPAIR_DELAY  = 50000  # seconds between repair runs

_nav_paused    = threading.Event()  # set while stashing/composing/repairing/reviving
_at_last_stop  = threading.Event()  # set while the bot is holding at NAV_STOPS[-1] — easiest spot to reach NAV_COMPOSE from
_reviving      = threading.Event()  # set while a revive is in progress
_manual_paused = threading.Event()  # set while paused via Ctrl+C hotkey
_action_lock   = threading.Lock()   # held while stashing/composing or repairing — keeps the two from overlapping
_shift_held    = False


def _locate(path, confidence=0.7):
    try:
        return pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
    except Exception:
        return None


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
    except AttributeError:
        pass


pynput_kb.Listener(on_press=_on_key_press, on_release=_on_key_release, daemon=True).start()


def _wait_if_dead():
    """Blocks while a revive is in progress — passed into stash/repair so they pause mid-action."""
    while _reviving.is_set():
        time.sleep(0.5)


def get_current_coords():
    img = pyautogui.screenshot(region=COORD_REGION)
    img = img.resize((img.width * 3, img.height * 3))
    text = pytesseract.image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789(),[]| ')
    print(f"  [OCR] region={COORD_REGION} text={text!r}")
    m = re.search(r'\(?(\d+),\s*(\d+)\)?', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def map_to_screen_delta(dx_map, dy_map):
    sx = (dx_map - dy_map) * MAP_SCALE
    sy = (dx_map + dy_map) * MAP_SCALE * 0.5
    return sx, sy


def navigate_to(target_x, target_y, check_pause=True, right_click_interval=RIGHT_CLICK_INTERVAL):
    """`check_pause=False` skips the _nav_paused wait — used when stash/compose/repair
    itself needs to drive the character somewhere (e.g. to a compose safe spot) while
    the main bot loop is already paused and inert.
    `right_click_interval` right-clicks in place every N seconds while en route;
    pass 0 to disable it for this call."""
    screen_w, screen_h = pyautogui.size()
    cx = screen_w // 2
    cy = screen_h // 2 + PLAYER_Y_OFFSET
    edge_cap = min(screen_w, screen_h) * NAV_MOUSE_CAP

    print(f"  [NAV] Heading to ({target_x},{target_y})")
    pyautogui.keyDown('ctrl')

    stuck_ref_pos  = None
    stuck_ref_time = time.time()
    last_right_click = time.time()
    tried_unstick  = False

    try:
        while True:
            if check_pause and _nav_paused.is_set():
                pyautogui.keyUp('ctrl')   # not moving while paused (stash/compose/repair/revive)
                while _nav_paused.is_set():
                    time.sleep(0.5)
                pyautogui.keyDown('ctrl')

            coords = get_current_coords()
            if coords is None:
                print("  [NAV] Can't read coords — ctrl+click to nudge...")
                ang = random.uniform(0, 2 * math.pi)
                rad = random.uniform(0, edge_cap)
                nudge_x = max(10, min(screen_w - 10, int(cx + rad * math.cos(ang))))
                nudge_y = max(10, min(screen_h - 10, int(cy + rad * math.sin(ang))))
                pyautogui.keyDown('ctrl')
                pyautogui.moveTo(nudge_x, nudge_y, duration=MOUSE_SPEED)
                pyautogui.click()
                time.sleep(0.5)
                continue

            cur_x, cur_y = coords
            dx_map = target_x - cur_x
            dy_map = target_y - cur_y
            dist   = math.hypot(dx_map, dy_map)
            print(f"  [NAV] pos=({cur_x},{cur_y})  ->({target_x},{target_y})  dist={dist:.1f}")

            now = time.time()
            if stuck_ref_pos is None:
                stuck_ref_pos, stuck_ref_time = (cur_x, cur_y), now
            elif now - stuck_ref_time >= STUCK_CHECK_INTERVAL:
                moved = math.hypot(cur_x - stuck_ref_pos[0], cur_y - stuck_ref_pos[1])
                if moved < STUCK_MOVE_THRESHOLD:
                    if not tried_unstick:
                        print(f"  [NAV] Stuck — barely moved in {STUCK_CHECK_INTERVAL}s, "
                              f"releasing ctrl for 0.1s to try to unstick...")
                        pyautogui.keyUp('ctrl')
                        time.sleep(0.1)
                        pyautogui.keyDown('ctrl')
                        tried_unstick = True
                        stuck_ref_pos, stuck_ref_time = (cur_x, cur_y), now  # give it one more window
                    else:
                        print(f"  [NAV] Still stuck after releasing ctrl — abandoning this stop")
                        return False
                else:
                    stuck_ref_pos, stuck_ref_time = (cur_x, cur_y), now
                    tried_unstick = False   # moving fine again — reset for next time

            if dist < ARRIVE_THRESHOLD:
                print(f"  [NAV] Arrived at ({target_x},{target_y})!")
                return True

            sx, sy = map_to_screen_delta(dx_map, dy_map)
            mag    = math.hypot(sx, sy) or 1
            sx     = sx / mag * edge_cap
            sy     = sy / mag * edge_cap

            jx = random.randint(-NAV_JITTER, NAV_JITTER)
            jy = random.randint(-NAV_JITTER, NAV_JITTER)
            click_x = max(10, min(screen_w - 10, int(cx + sx + jx)))
            click_y = max(10, min(screen_h - 10, int(cy + sy + jy)))

            try:
                pyautogui.moveTo(click_x, click_y, duration=MOUSE_SPEED)
                pyautogui.click()
            except pyautogui.FailSafeException:
                print("  [NAV] Emergency stop!")
                raise SystemExit

            if right_click_interval and time.time() - last_right_click >= right_click_interval:
                pyautogui.click(button='right')
                last_right_click = time.time()

            time.sleep(CLICK_INTERVAL)
    finally:
        pyautogui.keyUp('ctrl')


def hold_and_circle(duration):
    """Stands still, right-clicking random points on a circle around screen-center."""
    screen_w, screen_h = pyautogui.size()
    cx = screen_w // 2
    cy = screen_h // 2

    print(f"  [HOLD] Circling & right-clicking for {duration}s...")
    end_time = time.time() + duration
    while time.time() < end_time:
        if _nav_paused.is_set():
            time.sleep(0.5)
            continue

        angle  = random.uniform(0, 360)
        radius = random.uniform(HOLD_RADIUS_MIN, HOLD_RADIUS_MAX)
        rad    = math.radians(angle)
        x = int(cx + radius * math.cos(rad))
        y = int(cy + radius * math.sin(rad))
        try:
            pyautogui.moveTo(x, y, duration=MOUSE_SPEED)
            pyautogui.click(button='right')
        except pyautogui.FailSafeException:
            print("  [HOLD] Emergency stop!")
            raise SystemExit
        time.sleep(HOLD_STEP_INTERVAL)
    print("  [HOLD] Done.")


def _revive_loop():
    """Watches for the revive screen and handles it, even if nav is already paused
    (e.g. mid-stash/repair) — a death during those actions still gets caught."""
    while True:
        loc = _locate(REVIVE_PATH)
        if loc and not _reviving.is_set():
            was_paused = _nav_paused.is_set()
            _reviving.set()
            _nav_paused.set()
            rx, ry = pyautogui.center(loc)
            print(f"  [REVIVE] Died — hovering at ({rx},{ry}), waiting {REVIVE_WAIT}s...")
            pyautogui.moveTo(rx, ry, duration=MOUSE_SPEED)
            time.sleep(REVIVE_WAIT)  # wait for revive button to appear

            print("  [REVIVE] Wait done — checking for revive button...")
            while True:
                loc2 = _locate(REVIVE_PATH)
                if loc2:
                    rx2, ry2 = pyautogui.center(loc2)
                    pyautogui.click(rx2, ry2)
                    print("  [REVIVE] Clicked!")
                    break
                if get_current_coords():
                    print("  [REVIVE] Button gone and coords readable — looks like you already "
                          "revived yourself. Resuming.")
                    break
                print("  [REVIVE] Not found yet (mouse may have moved) — checking again...")
                time.sleep(1.0)

            if not was_paused:
                _nav_paused.clear()   # only resume nav if we're the one who paused it
            _reviving.clear()
        time.sleep(1.0)


def _stash_loop():
    next_stash   = time.time() + STASH_DELAY
    next_compose = time.time() + COMPOSE_DELAY

    while True:
        time.sleep(1.0)
        now = time.time()
        # Compose waits until the bot reaches the last stop — that's the easiest place
        # to reach NAV_COMPOSE from. No such wait if NAV_COMPOSE is empty.
        compose_due = now >= next_compose and (not NAV_COMPOSE or _at_last_stop.is_set())
        stash_due   = now >= next_stash

        if not compose_due and not stash_due:
            continue

        while _reviving.is_set():
            time.sleep(0.5)
        with _action_lock:
            _nav_paused.set()
            time.sleep(0.5)
            pyautogui.keyUp('ctrl')

            if compose_due:
                if NAV_COMPOSE:
                    cspot = random.choice(NAV_COMPOSE)
                    print(f"[COMPOSE] Moving to compose spot {cspot}...")
                    navigate_to(*cspot, check_pause=False)

                print("[COMPOSE] Running bank compose...")
                try:
                    run_compose()
                except Exception as e:
                    print(f"[COMPOSE] Error: {e}")

                # Stash right here, still at the compose spot (warehouse open) —
                # before heading back, not after.
                print("[STASH] Running stash (post-compose)...")
                try:
                    stash_items(wait_if_dead=_wait_if_dead)
                except Exception as e:
                    print(f"[STASH] Error: {e}")
                next_stash = time.time() + STASH_DELAY
                stash_due  = False   # already stashed just now

                if NAV_COMPOSE:
                    last_stop = NAV_STOPS[-1]
                    print(f"[COMPOSE] Returning to last stop {last_stop}...")
                    navigate_to(*last_stop, check_pause=False)

                next_compose = time.time() + COMPOSE_DELAY

            if stash_due:
                print("[STASH] Running stash...")
                try:
                    stash_items(wait_if_dead=_wait_if_dead)
                except Exception as e:
                    print(f"[STASH] Error: {e}")
                next_stash = time.time() + STASH_DELAY

            _nav_paused.clear()
            print("[STASH] Done — resuming navigation")


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

            _nav_paused.clear()
            print("[REPAIR] Done — resuming navigation")


def _f10_loop():
    while True:
        time.sleep(60)
        pyautogui.press('f10')
        print("  [F10] pressed")


def _speed_loop():
    """Keeps pressing F9 for as long as the speed buff icon (speed.jpg) is on screen."""
    while True:
        loc = _locate(SPEED_PATH)
        if loc:
            pyautogui.press('f9')
            print("  [SPEED] speed.jpg seen — pressed F9")
            time.sleep(1.0)
        else:
            time.sleep(1.0)


if __name__ == '__main__':
    print("=== Static Nav Bot ===")
    print("Move mouse to TOP-LEFT to emergency stop.")
    print("Starting in 3 seconds — switch to the game window...")
    time.sleep(3)

    threading.Thread(target=_revive_loop, daemon=True).start()
    threading.Thread(target=_stash_loop, daemon=True).start()
    threading.Thread(target=_repair_loop, daemon=True).start()
    threading.Thread(target=_f10_loop, daemon=True).start()
    threading.Thread(target=_speed_loop, daemon=True).start()

    try:
        i = 0
        while True:
            idx = i % len(NAV_STOPS)
            tx, ty = NAV_STOPS[idx]
            is_last = idx == len(NAV_STOPS) - 1
            print(f"  [BOT] Stop {idx + 1}/{len(NAV_STOPS)}: ({tx},{ty})")
            arrived = navigate_to(tx, ty)
            if arrived:
                if is_last:
                    _at_last_stop.set()   # let _stash_loop know it can head to NAV_COMPOSE now
                hold_and_circle(HOLD_DURATION)
                if is_last:
                    _at_last_stop.clear()
                i += 1
            else:
                # Stuck — walk backwards through previous stops, retrying each in turn,
                # until one is actually reached (with real movement, not a trivial
                # "already within threshold" no-op). Then resume the normal forward
                # order starting right after that recovered stop.
                back = idx
                pre_pos = get_current_coords()
                while True:
                    back = (back - 1) % len(NAV_STOPS)
                    if back == idx:
                        print(f"  [BOT] Still stuck after trying every stop — retrying stop {idx + 1} directly")
                        break
                    bx, by = NAV_STOPS[back]
                    print(f"  [BOT] Stuck at stop {idx + 1} — falling back to stop {back + 1}/{len(NAV_STOPS)}: ({bx},{by}) to adjust")
                    if navigate_to(bx, by):
                        post_pos = get_current_coords()
                        moved = (pre_pos and post_pos and
                                 math.hypot(post_pos[0] - pre_pos[0], post_pos[1] - pre_pos[1]))
                        if moved and moved >= STUCK_MOVE_THRESHOLD:
                            print(f"  [BOT] Recovered at stop {back + 1} — resuming normal order")
                            i = back + 1
                            break
                        print(f"  [BOT] Reached stop {back + 1} without actually moving — still stuck, going further back")
                        pre_pos = post_pos or pre_pos
    except (KeyboardInterrupt, SystemExit):
        os._exit(0)
