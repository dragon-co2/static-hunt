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
BUY_BELOW   = 2      # buy from the mall once 7StarPouches (or SerenityPouches) are fewer than this
HP_POUCHES  = 1     # 7StarPouches to keep - only topped up to this, the rest of the bag stays free
MP_POUCHES  = 1      # SerenityPouches to keep - only topped up to this
POUCH_OPENS_INTO = 5 # potions in a pouch: the bag needs this many free slots to open one
KEEP_FREE_SLOTS = 1  # bag slots the purchase leaves empty (for loot)
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
                _note_chat_box(pid)
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

    # Ctrl+F target: picked in the game (chat name box / Shift+click); shown here
    tk.Frame(root, height=1, bg='#ccc').pack(fill='x', padx=10, pady=(6, 4))
    target_lbl = tk.Label(root, text='Ctrl+F target: -', font=('Segoe UI', 10, 'bold'), anchor='w')
    target_lbl.pack(fill='x', padx=10)

    opts = tk.Frame(root)
    opts.pack(fill='x', padx=10, pady=(2, 6))
    skip_var = tk.BooleanVar(value=SKIP_NEAR[0])

    def set_skip():
        SKIP_NEAR[0] = skip_var.get()
    tk.Checkbutton(opts, text="don't jump if target is within", variable=skip_var,
                   command=set_skip, font=('Segoe UI', 9)).pack(side='left')
    near = tk.IntVar(value=NEAR_CELLS[0])

    def set_near(*_):
        try:
            NEAR_CELLS[0] = max(1, int(near.get()))
        except (tk.TclError, ValueError):
            pass
    tk.Spinbox(opts, from_=1, to=16, width=3, textvariable=near, command=set_near,
               font=('Segoe UI', 9)).pack(side='left', padx=(2, 0))
    near.trace_add('write', set_near)
    tk.Label(opts, text='cells (attack from here instead)', font=('Segoe UI', 9)).pack(side='left', padx=(2, 0))

    def update_label():
        name = ''
        pid = _foreground_game()
        if pid:
            name = _label_name(pid, game_actions.players(pid))
        target_lbl.config(text=f'Target: {name}', fg='black')
        root.after(300, update_label)

    refresh()
    update_label()
    root.mainloop()


JUMP_VK      = 0x46         # Ctrl+F in the game in front: jump next to the player whose name is in the
RCLICK_TIMES = 2            # chat's name box (Shift+click them), then use the right-click skill
RCLICK_EVERY = 0.5          # on them RCLICK_TIMES times, RCLICK_EVERY seconds apart
JUMP_LAND    = 0            # seconds after the jump before the first skill: 0 = right away, in the air
SKIP_NEAR = [False]         # don't jump when the target is within NEAR_CELLS - attack from here (window)
NEAR_CELLS = [3]            # how close "near" is, in map cells


def _foreground_game():
    """The pid of the game window in front, or None."""
    import ctypes
    import ctypes.wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    owner = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value if owner.value in game_memory.game_pids() else None
_press = 0                  # Ctrl+F presses so far: a newer press cancels what's left of an older one
_inject = threading.Lock()  # one action sent into the game at a time
_chat_box = {}              # pid -> [last id seen, last typed text seen, which changed last: 'id' / 'text']


def _note_chat_box(pid):
    """Remembers which way the chat name box was filled last: Shift+click (the id changes) or
    typing a name (the typed text changes). Called from the heal loop, every round."""
    seen = _chat_box.setdefault(pid, [None, None, 'id'])
    now_id, now_text = game_actions.chat_target(pid), game_actions.chat_text(pid)
    if seen[0] is not None and now_text != seen[1] and now_text:
        seen[2] = 'text'
    if seen[0] is not None and now_id != seen[0]:
        seen[2] = 'id'
    seen[0], seen[1] = now_id, now_text


_id_names = {}              # player id -> name, for every player seen around (kept after they leave)


def _ids_around(pid, around):
    """{id: name} of the players around - and remembers them in _id_names."""
    names = {game_actions.role_id(pid, r): n for n, r in around.items()}
    _id_names.update(names)
    return names


def _label_name(pid, around):
    """The name to show for the Ctrl+F target - what was typed / Shift+clicked, even when that
    player isn't around (then it can't be attacked, but the name is still shown). '' if none."""
    seen = _chat_box.get(pid) or [None, None, 'id']
    target_id = game_actions.chat_target(pid)
    names = _ids_around(pid, around)
    if seen[2] == 'text' or not target_id:
        typed = game_actions.chat_text(pid)
        if len(typed) < 2:
            return ''
        match = sorted((n for n in around if n.lower().startswith(typed.lower())), key=len)
        return match[0] if match else typed      # the player if around, else the raw typed name
    return names.get(target_id) or _id_names.get(target_id, '')   # Shift+clicked: remembered name


def _chat_box_target(pid, around):
    """(name, role) of the player in the chat name box, or (None, why). `around` = players().
    Uses only the way the box was last filled (_note_chat_box) - typing a name, or Shift+click -
    so whatever you did last is exactly what gets attacked. No falling back to the other one."""
    seen = _chat_box.get(pid) or [None, None, 'id']
    target_id = game_actions.chat_target(pid)
    if seen[2] == 'text' or not target_id:       # a name typed in the box (or nothing Shift+clicked)
        typed = game_actions.chat_text(pid).lower()   # the name (sometimes minus its last letter)
        if len(typed) < 2:
            return None, 'nothing typed in the chat name box'
        matches = sorted((n for n in around if n.lower().startswith(typed)), key=len)
        return (matches[0], around[matches[0]]) if matches else (None, f'"{typed}" is not around')
    names = _ids_around(pid, around)
    return (names[target_id], around[names[target_id]]) if target_id in names \
        else (None, f'{_id_names.get(target_id, "the Shift+clicked player")} is not around')


def _jump_and_hit(press):
    """Ctrl+F: in the game in front, jump next to the player in the name box, then use the
    right-click skill on them RCLICK_TIMES times. A newer Ctrl+F (press) stops this one where it is."""
    import ctypes
    import ctypes.wintypes

    def stale():
        return press != _press

    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        owner = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        pid = owner.value
        if pid not in game_memory.game_pids():
            print('[JUMP] Ctrl+F outside the game - ignored (the game window must be in front)')
            return
        me = game_memory._name_pid(pid)
        target, role = _chat_box_target(pid, game_actions.players(pid))
        if not target:
            print(f'[JUMP] {me}: {role}')
            return
        if game_actions.role_dead(pid, role):
            print(f'[JUMP] {me}: {target} is dead - not jumping')
            return
        target_id = game_actions.role_id(pid, role)
        near = False
        if SKIP_NEAR[0]:
            me_pos, t_pos = game_actions.my_pos(pid), game_actions.role_pos(pid, role)
            if me_pos and t_pos:
                near = max(abs(me_pos[0] - t_pos[0]), abs(me_pos[1] - t_pos[1])) < NEAR_CELLS[0]
        if near:
            print(f'[JUMP] {me}: {target} is within {NEAR_CELLS[0]} cells - attacking from here, no jump')
        else:
            with _inject:
                if stale():
                    return
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
            with _inject:
                if stale():
                    return                       # a newer Ctrl+F took over
                game_actions.use_skill_on(pid, skill, target_id)
        print(f'[JUMP] {me}: used skill {skill} on {target} x{RCLICK_TIMES}')
    except Exception as e:
        print(f'[JUMP] {type(e).__name__}: {e}')


def _hotkeys():
    """Ctrl+F -> _jump_and_hit, on a thread of its own so the keyboard stays free. Matched by the
    physical key (virtual-key code), so it works whatever the keyboard language is (with Arabic,
    F types a different letter and a character-based hotkey never matches)."""
    from pynput import keyboard
    ctrl = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
    held = set()
    f_down = [False]                              # F held: its auto-repeat isn't a new press

    def on_press(key):
        global _press
        if key in ctrl:
            held.add(key)
        elif getattr(key, 'vk', None) == JUMP_VK:
            if held and not f_down[0]:
                _press += 1
                threading.Thread(target=_jump_and_hit, args=(_press,), daemon=True).start()
            f_down[0] = True

    def on_release(key):
        held.discard(key)
        if getattr(key, 'vk', None) == JUMP_VK:
            f_down[0] = False

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
