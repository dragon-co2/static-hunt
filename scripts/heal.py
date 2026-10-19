"""Auto heal - run it on its own (python scripts/heal.py). It opens a small window listing every
running account; only the ticked ones are healed (the ticks are remembered for the next run in
heal_accounts.json next to the settings). Closing the window stops it.
Each row also has a box per buff in BUFFS (Stigma, Shield): ticked, that skill is cast on the
character itself whenever its status icon is gone (read from the status flags) - for the
characters that have the skill; works whether or not healing is ticked.
Ctrl+F in a game: jumps next to the player named in the chat's name box (Shift+click them), then
uses the character's right-click skill on them RCLICK_TIMES times, RCLICK_EVERY seconds apart.

Several times a second it reads each game's HP, MP and potions from memory (game_memory.py,
read only) and, for any account that needs it, uses the potions from inside the game
(game_actions.py) - in the background: no keys, no window in front, no desktop
switch, so it never gets in the way of you or static.py:
    7StarOintment (+5000 HP)   when HP is more than HP_MISSING below the max
    SerenityPill  (+5000 MP)   when MP is below MP_MIN_PCT % of the most seen
    open a 7StarPouch / SerenityPouch once the ointments / pills are down to KEEP_HP / KEEP_MP_POTIONS
When the pouches run low (fewer than BUY_BELOW) it buys more from the Shopping Mall: SerenityPouches
up to MP_POUCHES, every other free slot 7StarPouches.
game_actions.py WRITES to the game (a small stub runs inside it for each action)."""
import json
import os
import sys
import threading
import time

import game_actions
import game_memory
from dragon_settings import override_dir

HP_MISSING  = 100   # drink once HP is this much below the max
MP_MIN_PCT  = 90     # drink a pill once MP is below this % of the highest MP seen
MP_MAX      = 800    # max MP to assume until a higher MP is seen
CHECK_EVERY = 0.1    # seconds between reads of every game
DRINK_WAIT  = 0.05   # seconds between checks while an account is being healed
DRINK_AGAIN = 0.5    # after a potion, the next one of its kind waits until HP / MP moved, or this long
                     # (one message per potion - a flood of them can get the account disconnected)
KEEP_HP_POTIONS = 3  # open a 7StarPouch as soon as the 7StarOintments are down to this many
KEEP_MP_POTIONS = 1  # open a SerenityPouch as soon as the SerenityPills are down to this many
POUCH_WAIT  = 0.5    # seconds after opening a pouch for its potions to show up in the bag
BUY_BELOW   = 10      # buy from the mall once 7StarPouches (or SerenityPouches) are fewer than this
HP_POUCHES  = 3     # 7StarPouches to keep - only topped up to this, the rest of the bag stays free
MP_POUCHES  = 1      # SerenityPouches to keep - only topped up to this
POUCH_OPENS_INTO = 5 # potions in a pouch: the bag needs this many free slots to open one
KEEP_FREE_SLOTS = 5  # bag slots the purchase leaves empty (for loot)
BUY_WAIT    = 3      # seconds after a purchase for the bag to show it before buying again

_max_mp = {}         # character -> highest MP seen (the game doesn't keep the max anywhere)
_last_use = {}       # character -> time.time() before which nothing more is used
_drank = {}          # (character, potion) -> (time.time(), HP or MP then) of the last one drunk
_last_buy = {}       # character -> time.time() of its last mall purchase
_warned = set()      # (character, 'hp' / 'mp') already told it's out of potions


def _uses_for(name, v):
    """[(what, item id)] this account should use now, in order."""
    uses = []
    pots, ids = v['potions'], v['ids']
    room = game_actions.BAG_SLOTS - v['bag'] >= POUCH_OPENS_INTO   # a pouch only opens with room
    _max_mp[name] = max(_max_mp.get(name, MP_MAX), v['mp'])
    for need, potion, pouch, keep, key in (
            (v['max_hp'] - v['hp'] > HP_MISSING, '7StarOintment', '7StarPouch', KEEP_HP_POTIONS, 'hp'),
            (v['mp'] < _max_mp[name] * MP_MIN_PCT / 100, 'SerenityPill', 'SerenityPouch', KEEP_MP_POTIONS, 'mp')):
        if need and pots[potion]:
            at, before = _drank.get((name, potion), (0, None))
            if v[key] != before or time.time() - at >= DRINK_AGAIN:   # the last one landed (or got lost)
                uses.append((potion, ids[potion][0]))
        elif need and not pots[pouch] and (name, key) not in _warned:
            _warned.add((name, key))
            print(f'[HEAL] {name}: {key.upper()} low and no {potion} / {pouch} left')
        if not need:
            _warned.discard((name, key))
        if pots[potion] <= keep and pots[pouch] and room:
            uses.append((pouch, ids[pouch][0]))
            room = False                                         # one pouch at a time
    return uses


def _restock(pid, name, v):
    """Buys pouches from the mall once either kind is fewer than BUY_BELOW: SerenityPouches up to
    MP_POUCHES, 7StarPouches up to HP_POUCHES - never into the room kept for opening a pouch
    (POUCH_OPENS_INTO) and for loot (KEEP_FREE_SLOTS)."""
    if time.time() - _last_buy.get(name, 0) < BUY_WAIT:
        return
    pots = v['potions']
    hp_low, mp_low = pots['7StarPouch'] < BUY_BELOW, pots['SerenityPouch'] < BUY_BELOW
    if not (hp_low or mp_low):
        return
    free = game_actions.BAG_SLOTS - v['bag'] - KEEP_FREE_SLOTS - POUCH_OPENS_INTO
    mp = min(max(MP_POUCHES - pots['SerenityPouch'], 0), max(free, 0)) if mp_low else 0
    hp = min(max(HP_POUCHES - pots['7StarPouch'], 0), free - mp) if hp_low else 0
    buys = [(kind, n) for kind, n in (('SerenityPouch', mp), ('7StarPouch', hp)) if n > 0]
    if not buys:
        return
    _last_buy[name] = time.time()
    for kind, amount in buys:
        try:
            game_actions.buy(pid, game_actions.POUCHES[kind], amount)
            print(f'[HEAL] {name}: bought {amount} {kind} from the mall ({pots[kind]} left, {v["cp"]} CPs)')
        except Exception as e:
            print(f'[HEAL] {name}: buying {kind} failed ({type(e).__name__}: {e})')


def _tend(pid, name, v):
    """Drinks / opens what this account needs now. True when it used something."""
    if time.time() < _last_use.get(name, 0):
        return False
    uses = _uses_for(name, v)
    for what, item_id in uses:
        try:
            game_actions.use(pid, item_id)
        except Exception as e:
            print(f'[HEAL] {name}: using {what} failed ({type(e).__name__}: {e})')
            continue
        pouch = what.endswith('Pouch')
        _last_use[name] = time.time() + (POUCH_WAIT if pouch else DRINK_WAIT)
        if not pouch:
            _drank[(name, what)] = (time.time(), v['hp' if what == '7StarOintment' else 'mp'])
        print(f'[HEAL] {name}: HP {v["hp"]}/{v["max_hp"]} MP {v["mp"]} -> {what}')
    return bool(uses)


class _Tee:
    """Everything printed also goes to logs/heal.log, each line with the time."""
    def __init__(self, stream, path):
        self._stream, self._file, self._fresh = stream, open(path, 'a', encoding='utf-8'), True

    def write(self, text):
        self._stream.write(text)
        for part in text.splitlines(keepends=True):
            if self._fresh:
                self._file.write(time.strftime('%Y-%m-%d %H:%M:%S '))
            self._file.write(part)
            self._fresh = part.endswith('\n')
        self._file.flush()

    def flush(self):
        self._stream.flush()


BUFFS = {            # skill type -> (name, its bit in the status flags) - kept up when ticked,
    1095: ('Stigma', 0x200),     # in this order: Shield only goes on once Stigma is on
    1090: ('Shield', 0x100),
}
BUFF_DELAY  = 0.1      # seconds a buff stays off (its icon gone) before it's cast again
BUFF_RETRY  = 5      # seconds before casting a buff again when the last cast didn't take (no mana...)
SKILLS_EVERY = 30    # seconds between re-reads of which skills each character has

CHECKED_PATH = os.path.join(override_dir(), 'heal_accounts.json')   # the ticks, kept across runs
_checked = set()     # characters ticked in the window - only these are healed
_buffs = {}          # character -> {skill type} ticked to keep up
_seen = {}           # character -> its latest vitals (None while not running), for the window
_skills = {}         # character -> (time.time() read, {skill types it has})
_cast_at = {}        # (character, skill type) -> time.time() of the last cast
_off_since = {}      # (character, skill type) -> time.time() its status icon was first seen gone
_lock = threading.Lock()


def _load_checked():
    """Fills _checked / _buffs from CHECKED_PATH (a plain list = the healed accounts, older format)."""
    try:
        with open(CHECKED_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(data, list):
        data = {'heal': data}
    _checked.update(data.get('heal', []))
    _buffs.update({name: set(types) for name, types in data.get('buffs', {}).items()})


def _save_checked():
    try:
        os.makedirs(os.path.dirname(CHECKED_PATH), exist_ok=True)
        with _lock:
            data = {'heal': sorted(_checked),
                    'buffs': {name: sorted(types) for name, types in _buffs.items() if types}}
        with open(CHECKED_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
    except OSError as e:
        print(f'[HEAL] could not save {CHECKED_PATH} ({e})')


def _skills_of(pid, name):
    """The skill types this character has (re-read every SKILLS_EVERY seconds)."""
    at, found = _skills.get(name, (0, set()))
    if time.time() - at >= SKILLS_EVERY:
        found = game_actions.skills(pid)
        _skills[name] = (time.time(), found)
    return found


def _keep_buffs(pid, name, v):
    """Casts the ticked buffs (on the character itself) whose status icon has been gone for
    BUFF_DELAY seconds, in BUFFS order: a later one (Shield) waits until every earlier ticked one
    (Stigma) is on."""
    with _lock:
        wanted = set(_buffs.get(name, ()))
    has = _skills_of(pid, name)
    for skill, (label, bit) in BUFFS.items():          # note when each one ran out
        if v['status'] & bit:
            _off_since.pop((name, skill), None)
        else:
            _off_since.setdefault((name, skill), time.time())
    for skill, (label, bit) in BUFFS.items():
        if v['status'] & bit or skill not in wanted or skill not in has:
            continue
        if time.time() - _off_since[(name, skill)] < BUFF_DELAY:
            return   # it just ran out: wait a moment before renewing it (and anything after it)
        if time.time() - _cast_at.get((name, skill), 0) < BUFF_RETRY:
            return   # just cast, not on yet: wait for it before anything after it
        _cast_at[(name, skill)] = time.time()
        try:
            game_actions.cast_self(pid, skill)
            print(f'[HEAL] {name}: {label} was off - cast it (MP {v["mp"]})')
        except Exception as e:
            print(f'[HEAL] {name}: casting {label} failed ({type(e).__name__}: {e})')
        return   # one cast per round: the next one goes after this one landed


def _heal_loop():
    """Background thread: reads every running game; heals the ticked accounts and keeps their
    ticked buffs up."""
    while True:
        busy = False
        running = set()
        try:
            for pid in game_memory.game_pids():
                name = game_memory._name_pid(pid)
                v = game_memory.vitals_pid(pid) if name else None
                if not name:
                    continue
                running.add(name)
                with _lock:
                    _seen[name] = v
                    ticked = name in _checked
                if not v or v['hp'] <= 0:                 # not readable / dead
                    continue
                _keep_buffs(pid, name, v)
                if not ticked:
                    continue
                _restock(pid, name, v)
                busy = _tend(pid, name, v) or busy
            with _lock:
                for name in list(_seen):
                    if name not in running:
                        _seen[name] = None
        except Exception as e:
            print(f'[HEAL] {type(e).__name__}: {e}')
            time.sleep(1)
        time.sleep(DRINK_WAIT if busy else CHECK_EVERY)   # someone is healing: check again right away


def _window():
    """The checklist: one row per account seen running - tick it to heal it."""
    import tkinter as tk

    root = tk.Tk()
    root.title('Auto heal')
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tk.Label(root, text='Ticked accounts are healed - and their ticked buffs kept up',
             font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(8, 4))
    rows = tk.Frame(root)
    rows.pack(fill='x', padx=10)
    boxes = {}   # character -> (BooleanVar, Label, {skill type: Checkbutton})

    def toggle(name, var):
        with _lock:
            (_checked.add if var.get() else _checked.discard)(name)
        _save_checked()
        print(f'[HEAL] {name}: ' + ('healing' if var.get() else 'not healing'))

    def toggle_buff(name, skill, var):
        with _lock:
            types = _buffs.setdefault(name, set())
            (types.add if var.get() else types.discard)(skill)
        _save_checked()
        print(f'[HEAL] {name}: {BUFFS[skill][0]} ' + ('kept up' if var.get() else 'left alone'))

    def tick_all(on):
        for name, (var, _, _) in boxes.items():
            if var.get() != on:
                var.set(on)
                toggle(name, var)

    def refresh():
        with _lock:
            seen = dict(_seen)
        for name in sorted(seen, key=str.lower):
            if name not in boxes:
                var = tk.BooleanVar(value=name in _checked)
                row = tk.Frame(rows)
                row.pack(fill='x', pady=1)
                tk.Checkbutton(row, text=name, variable=var, width=16, anchor='w',
                               font=('Segoe UI', 10), command=lambda n=name, v=var: toggle(n, v)).pack(side='left')
                buffs = {}
                for skill, (label, _) in BUFFS.items():
                    bvar = tk.BooleanVar(value=skill in _buffs.get(name, ()))
                    box = tk.Checkbutton(row, text=label, variable=bvar, font=('Segoe UI', 9), state='disabled',
                                         command=lambda n=name, s=skill, b=bvar: toggle_buff(n, s, b))
                    box.pack(side='left')
                    buffs[skill] = box
                info = tk.Label(row, font=('Consolas', 9), anchor='w', width=52)
                info.pack(side='left')
                boxes[name] = (var, info, buffs)
            v = seen[name]
            _, info, buffs = boxes[name]
            has = _skills.get(name, (0, set()))[1]
            for skill, box in buffs.items():           # a buff box only works if the character has it
                box.config(state='normal' if skill in has else 'disabled')
            if v is None:
                info.config(text='not running', fg='gray')
            else:
                p = v['potions']
                on = ' '.join(label for label, bit in BUFFS.values() if v['status'] & bit)
                info.config(fg='black', text=f'HP {v["hp"]}/{v["max_hp"]}  MP {v["mp"]}  '
                                             f'oint {p["7StarOintment"]}+{p["7StarPouch"]}  pill {p["SerenityPill"]}+{p["SerenityPouch"]}'
                                             + (f'  [{on}]' if on else ''))
        root.after(500, refresh)

    buttons = tk.Frame(root)
    buttons.pack(fill='x', padx=10, pady=8)
    tk.Button(buttons, text='All', width=8, command=lambda: tick_all(True)).pack(side='left')
    tk.Button(buttons, text='None', width=8, command=lambda: tick_all(False)).pack(side='left', padx=6)
    refresh()
    root.mainloop()


JUMP_VK      = 0x46         # Ctrl+F in the game in front: jump next to the player whose name is in the
RCLICK_TIMES = 2            # chat's name box (Shift+click them), then use the right-click skill
RCLICK_EVERY = 0.5          # on them RCLICK_TIMES times, RCLICK_EVERY seconds apart
JUMP_LAND    = 0.7          # seconds after the jump before the first skill (the landing)
_jumping = threading.Lock()


def _jump_and_hit():
    """Ctrl+F: in the game in front, jump next to the player in the name box, then use the
    right-click skill on them RCLICK_TIMES times."""
    import ctypes
    import ctypes.wintypes
    if not _jumping.acquire(blocking=False):
        return                                   # still busy with the last one
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        owner = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        pid = owner.value
        if pid not in game_memory.game_pids():
            print('[JUMP] Ctrl+F outside the game - ignored (the game window must be in front)')
            return
        me = game_memory._name_pid(pid)
        target_id = game_actions.chat_target(pid)
        if not target_id:
            print(f'[JUMP] {me}: the chat name box is empty (Shift+click a player first)')
            return
        around = {game_actions.role_id(pid, r): (n, r) for n, r in game_actions.players(pid).items()}
        if target_id not in around:
            print(f'[JUMP] {me}: the player in the chat name box (id {target_id}) is not around')
            return
        target, role = around[target_id]
        game_actions.jump_next_to(pid, role)
        print(f'[JUMP] {me}: jumped next to {target}')
        skill = game_actions.right_click_skill(pid)
        if not skill:
            print(f'[JUMP] {me}: no skill on the right click - nothing used on {target}')
            return
        time.sleep(JUMP_LAND)
        for n in range(RCLICK_TIMES):
            if n:
                time.sleep(RCLICK_EVERY)
            game_actions.use_skill_on(pid, skill, target_id)
        print(f'[JUMP] {me}: used skill {skill} on {target} x{RCLICK_TIMES}')
    except Exception as e:
        print(f'[JUMP] {type(e).__name__}: {e}')
    finally:
        _jumping.release()


def _hotkeys():
    """Ctrl+F -> _jump_and_hit, on a thread of its own so the keyboard stays free. Matched by the
    physical key (virtual-key code), so it works whatever the keyboard language is (with Arabic,
    F types a different letter and a character-based hotkey never matches)."""
    from pynput import keyboard
    ctrl = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
    held = set()

    def on_press(key):
        if key in ctrl:
            held.add(key)
        elif held and getattr(key, 'vk', None) == JUMP_VK:
            threading.Thread(target=_jump_and_hit, daemon=True).start()

    def on_release(key):
        held.discard(key)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()


def main():
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = sys.stderr = _Tee(sys.__stdout__, os.path.join(log_dir, 'heal.log'))
    _load_checked()
    print(f'[HEAL] an ointment when HP is more than {HP_MISSING} below the max, a pill when MP is below '
          f'{MP_MIN_PCT}%, pouches bought from the mall - only for the accounts ticked in the window')
    threading.Thread(target=_heal_loop, daemon=True, name='heal').start()
    _hotkeys()
    print(f'[JUMP] Ctrl+F in a game: jump next to the player in the chat name box, then use the '
          f'right-click skill on them x{RCLICK_TIMES}')
    _window()                      # closing the window stops healing
    print('[HEAL] window closed - stopped')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[HEAL] stopped')
