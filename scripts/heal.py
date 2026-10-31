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
import travel
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
ONLY_TICKED = False  # True (static.py's panel): the buffs too only for the ticked (Auto heal) accounts

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
    if isinstance(data.get('hotkey'), dict):
        HOTKEY.update(data['hotkey'])


def _save_checked():
    try:
        os.makedirs(os.path.dirname(CHECKED_PATH), exist_ok=True)
        with _lock:
            data = {'heal': sorted(_checked),
                    'buffs': {name: sorted(types) for name, types in _buffs.items() if types},
                    'hotkey': HOTKEY}
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
                _note_targets(pid)
                with _lock:
                    _seen[name] = v
                    ticked = name in _checked
                if not v or v['hp'] <= 0 or ONLY_TICKED and not ticked:   # not readable / dead
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
    head = tk.Frame(root)
    head.pack(fill='x', padx=10)
    target_lbl = tk.Label(head, text='Target: -', font=('Segoe UI', 10, 'bold'), anchor='w')
    target_lbl.pack(side='left')
    key_btn = tk.Button(head, text=f'Shortcut: {HOTKEY["label"]}', width=22)

    def change_key():
        key_btn.config(text='press the new shortcut... (Esc: keep)')
        _capture[0] = lambda name: root.after(0, lambda: key_btn.config(text=f'Shortcut: {name}'))
    key_btn.config(command=change_key)
    key_btn.pack(side='right')

    opts = tk.Frame(root)
    opts.pack(fill='x', padx=10, pady=(2, 6))
    skip_var = tk.BooleanVar(value=SKIP_NEAR[0])

    def set_skip():
        SKIP_NEAR[0] = skip_var.get()
    tk.Checkbutton(opts, text="run, don't jump, if target is within", variable=skip_var,
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
    path_var = tk.BooleanVar(value=PATH_FIND[0])

    def set_path():
        PATH_FIND[0] = path_var.get()
    tk.Checkbutton(opts, text='path find (round walls, far targets)', variable=path_var,
                   command=set_path, font=('Segoe UI', 9)).pack(side='left', padx=(12, 0))
    tk.Label(opts, text='cells (run up to it, then attack)', font=('Segoe UI', 9)).pack(side='left', padx=(2, 0))

    # the players list: right-clicked / attacked players added by themselves; Ctrl+F goes for the
    # locked one, else the most recent one around
    tk.Frame(root, height=1, bg='#ccc').pack(fill='x', padx=10, pady=(6, 4))
    top = tk.Frame(root)
    top.pack(fill='x', padx=10)
    on_var = tk.BooleanVar(value=_enemies['on'])
    lock_var = tk.BooleanVar(value=bool(_enemies['lock']))

    def set_on():
        _enemies['on'] = on_var.get()
        _attack_seen.clear()
        _save_enemies()
        print('[ENEMIES] the list ' + ('on: right-clicked players are added, Ctrl+F goes for them'
                                      if _enemies['on'] else 'off: Ctrl+F uses the chat name box'))
    tk.Checkbutton(top, text='players list (right-clicked / attacked players - Ctrl+F goes for them)',
                   variable=on_var, command=set_on, font=('Segoe UI', 9, 'bold')).pack(side='left')
    body = tk.Frame(root)
    body.pack(fill='x', padx=10, pady=(2, 8))
    box = tk.Listbox(body, height=6, width=28, font=('Segoe UI', 9), exportselection=False)
    box.pack(side='left')
    side = tk.Frame(body)
    side.pack(side='left', padx=8, anchor='n')

    def selected():
        sel = box.curselection()
        return box.get(sel[0]).replace(' (locked)', '') if sel else ''

    def set_lock():
        name = selected() if lock_var.get() else ''
        if lock_var.get() and not name:
            lock_var.set(False)
            print('[ENEMIES] pick a player in the list first, then Lock player')
            return
        _enemies['lock'] = name
        _save_enemies()
        print(f'[ENEMIES] ' + (f'{name} locked - Ctrl+F goes only for them' if name else 'unlocked'))

    def remove():
        name = selected()
        if name in _enemies['players']:
            _enemies['players'].remove(name)
            if _enemies['lock'] == name:
                _enemies['lock'] = ''
                lock_var.set(False)
            _save_enemies()

    def clear():
        from tkinter import messagebox
        if _enemies['players'] and not messagebox.askyesno(
                'Clear the list', f'Remove all {len(_enemies["players"])} players from the list?', parent=root):
            return
        _enemies['players'], _enemies['lock'] = [], ''
        lock_var.set(False)
        _save_enemies()
    tk.Checkbutton(side, text='Lock player (the one picked)', variable=lock_var, command=set_lock,
                   font=('Segoe UI', 9)).pack(anchor='w')
    tk.Button(side, text='Remove', width=10, command=remove).pack(anchor='w', pady=(4, 0))
    tk.Button(side, text='Clear', width=10, command=clear).pack(anchor='w', pady=(4, 0))

    def picked(_event):
        """Lock off: a name clicked goes to the top - the Ctrl+F target from now on."""
        name = selected()
        if not lock_var.get() and name in _enemies['players'] and _enemies['players'][0] != name:
            _enemies['players'].remove(name)
            _enemies['players'].insert(0, name)
            _save_enemies()
            print(f'[ENEMIES] {name} picked - the Ctrl+F target')
    box.bind('<<ListboxSelect>>', picked)

    def refresh_list():
        names = [n + (' (locked)' if n == _enemies['lock'] else '') for n in _enemies['players']]
        keep = selected()
        if list(box.get(0, 'end')) != names:
            box.delete(0, 'end')
            for n in names:
                box.insert('end', n)
        if lock_var.get():                              # locked: the highlight stays on the locked one
            show = _enemies['lock']
        else:                                           # else it follows the target (the newest around)
            pid = _foreground_game()
            show = (_list_target(pid, game_actions.players(pid))[0] if pid else None) or                 (_enemies['players'][0] if _enemies['players'] else '')
        if show in _enemies['players'] and show != keep:
            box.selection_clear(0, 'end')
            box.selection_set(_enemies['players'].index(show))
            box.see(_enemies['players'].index(show))
        root.after(500, refresh_list)

    def update_label():
        name = ''
        pid = _foreground_game()
        if pid:
            around = game_actions.players(pid)
            name = (_list_target(pid, around)[0] or '') if _enemies['on'] else _label_name(pid, around)
        target_lbl.config(text=f'Target: {name}', fg='black')
        root.after(300, update_label)

    refresh_list()

    refresh()
    update_label()
    root.mainloop()


HOTKEY = {'mods': ['ctrl'], 'vk': 0x46, 'label': 'Ctrl+F'}   # the shortcut (set in the window)
_capture = [None]           # while waiting for a new shortcut: called with its label once pressed
JUMP_VK      = 0x46         # Ctrl+F in the game in front: jump next to the player whose name is in the
RCLICK_TIMES = 2            # chat's name box (Shift+click them), then use the right-click skill
RCLICK_EVERY = 0.5          # on them RCLICK_TIMES times, RCLICK_EVERY seconds apart
JUMP_LAND    = 0            # seconds after the jump before the first skill: 0 = right away, in the air
SKIP_NEAR = [True]          # run, don't jump, when the target is within NEAR_CELLS - then attack (window)
NEAR_CELLS = [3]            # how close "near" is, in map cells
PATH_FIND = [True]          # a far target / a wall in the way: the way round on the map (window)


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


ENEMIES_PATH = os.path.join(override_dir(), 'heal_enemies.json')   # the players list, kept across runs
ATTACK_TARGET = 0x1024      # hero + this: the id of the role being attacked (a skill like
                            # TwofoldBlade, or a hit) - seen in the game's memory while attacking
PLAYER_IDS = 1000000        # role ids from here up are players
_enemies = {'on': False, 'lock': '', 'players': []}   # the list: most recent first
_attack_seen = {}           # pid -> the ATTACK_TARGET id last read


def _load_enemies():
    try:
        with open(ENEMIES_PATH, encoding='utf-8') as f:
            _enemies.update(json.load(f))
    except (OSError, ValueError):
        pass


def _save_enemies():
    try:
        os.makedirs(os.path.dirname(ENEMIES_PATH), exist_ok=True)
        with open(ENEMIES_PATH, 'w', encoding='utf-8') as f:
            json.dump(_enemies, f, indent=1)
    except OSError as e:
        print(f'[ENEMIES] could not save {ENEMIES_PATH} ({e})')


def _note_targets(pid):
    """With the list on: a player I attack (ATTACK_TARGET - a skill like TwofoldBlade, or a hit)
    goes to the top of the list. Called from the heal loop, every round."""
    if not _enemies['on']:
        return
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
    if not handle:
        return
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
        attacked = game_memory._u32(handle, hero + ATTACK_TARGET)
    except OSError:
        return
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    if attacked != _attack_seen.get(pid) and attacked >= PLAYER_IDS:   # just attacked a player
        name = _ids_around(pid, game_actions.players(pid)).get(attacked)
        if name:
            _add_enemy(name, 'attacked')
    _attack_seen[pid] = attacked


def _add_enemy(name, why):
    """`name` to the top of the list (once)."""
    if not _enemies['players'] or _enemies['players'][0] != name:
        if name in _enemies['players']:
            _enemies['players'].remove(name)
        _enemies['players'].insert(0, name)
        _save_enemies()
        print(f'[ENEMIES] {name} added to the list ({why})')


def _kills_loop():
    """The games' chat (kill_watch's read-only chat_reader.ps1): with the list on, a player one of
    ours killed - or one who killed one of ours - goes on the list. Monsters never: only names seen
    as players around."""
    import base64
    import subprocess
    import kill_watch
    while True:
        try:
            proc = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', kill_watch.READER],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            print(f'[ENEMIES] could not start the chat reader ({e})')
            time.sleep(30)
            continue
        kill_watch._tie_to_us(proc)
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
                text = base64.b64decode(msg.get('text') or '').decode('cp1252', 'replace')
                me = base64.b64decode(msg.get('character') or '').decode('cp1252', 'replace')
            except ValueError:
                continue
            kill = kill_watch.parse_kill(text)
            if not kill or not _enemies['on']:
                continue
            victim, killer = (me if n.lower() == 'you' else n for n in kill)
            ours = {(game_memory._name_pid(p) or '').lower() for p in game_memory.game_pids()}
            players = {n.lower(): n for n in _id_names.values()}
            for p in game_memory.game_pids():
                players.update({n.lower(): n for n in game_actions.players(p)})
            if killer.lower() in ours and victim.lower() in players:
                _add_enemy(players[victim.lower()], f'killed by {killer}')
            elif victim.lower() in ours and killer.lower() in players:
                _add_enemy(players[killer.lower()], f'killed {victim}')
        proc.wait()
        time.sleep(5)


def _list_target(pid, around):
    """(name, role) of the Ctrl+F target from the list: the locked player, else the most recent of
    the list that's around - or (None, why)."""
    lock = _enemies.get('lock')
    if lock:
        return (lock, around[lock]) if lock in around else (None, f'{lock} (locked) is not around')
    for name in _enemies['players']:
        if name in around:
            return name, around[name]
    return None, 'nobody of the list is around'


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


def _run_beside(pid, at):
    """Runs to the cell one step from `at` on this side of it - never onto it (standing on the
    target's very cell spoils the skill); already that close: stays put."""
    me = game_actions.my_pos(pid)
    if not me:
        return
    sign = lambda v: (v > 0) - (v < 0)
    cell = (at[0] + sign(me[0] - at[0]), at[1] + sign(me[1] - at[1]))
    if tuple(me) == tuple(at):                          # on its very cell: a step off it
        try:
            width, _, walkable = travel.load_map(travel.map_file(travel.game_root(pid), travel.map_id(pid)))
            cell = next(((at[0] + dx, at[1] + dy) for dx, dy in travel.NEIGHBOURS
                         if walkable[(at[1] + dy) * width + at[0] + dx]), None)
        except (OSError, IndexError):
            cell = (at[0] + 1, at[1])
    if cell and cell != tuple(at) and cell != tuple(me):
        game_actions.run_to(pid, *cell)


def _jump_or_go_round(pid, me, role, target, target_id, stale):
    """Next to the target: one jump if it's within a jump and the game takes it - else (farther, or
    a wall the jump is refused over) the way round, worked out on the map (travel.go: jumps, and
    runs for the short legs), till within NEAR_CELLS of where it is now, then a run / jump the rest.
    False if it was given up (a newer Ctrl+F, the target gone)."""
    me_pos = game_actions.my_pos(pid)
    where = lambda: game_actions.positions(pid, [target_id]).get(target_id)
    t_pos = where()
    if me_pos and t_pos and travel._far(me_pos, t_pos) <= travel.JUMP_CELLS:
        with _inject:
            if stale():
                return False
            game_actions.jump_next_to(pid, role)
        if travel._airborne(pid):                      # taken: in the air (the skill comes at once)
            print(f'[JUMP] {me}: jumped next to {target}')
            return True
        print(f'[JUMP] {me}: the jump to {target} was refused (a wall?) - going round')
    elif not t_pos:
        print(f'[JUMP] {me}: {target} is out of sight')
        return False
    else:
        print(f'[JUMP] {me}: {target} is {travel._far(me_pos, t_pos):.0f} cells away - going there')

    def there():
        now, at = game_actions.my_pos(pid), where()
        return stale() or at is None or (now and travel._far(now, at) <= NEAR_CELLS[0] + 1)
    travel.go(pid, me, *t_pos, stop=there)
    at = where()
    if stale() or at is None:
        return False
    with _inject:
        if travel._far(game_actions.my_pos(pid) or at, at) <= travel.RUN_BELOW:
            _run_beside(pid, at)                        # the last bit: run up to a step from it
        else:
            game_actions.jump_next_to_id(pid, target_id)
    print(f'[JUMP] {me}: got round to {target}')
    return True


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
        around = game_actions.players(pid)
        target, role = _list_target(pid, around) if _enemies['on'] else _chat_box_target(pid, around)
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
            with _inject:
                if stale():
                    return
                _run_beside(pid, t_pos)                 # the game's own run, a step from it
            print(f'[JUMP] {me}: {target} is within {NEAR_CELLS[0]} cells - ran to it, no jump')
        elif PATH_FIND[0]:
            if not _jump_or_go_round(pid, me, role, target, target_id, stale):
                return
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
    """The shortcut (HOTKEY, Ctrl+F at first) -> _jump_and_hit, on a thread of its own so the
    keyboard stays free. Matched by the physical key (virtual-key code), so it works whatever the
    keyboard language is (with Arabic, F types a different letter and a character-based hotkey never
    matches). While the window waits for a new shortcut (_capture), the next key pressed - with the
    Ctrl / Alt / Shift held - becomes it (Esc: never mind)."""
    from pynput import keyboard
    K = keyboard.Key
    mods_of = {K.ctrl: 'ctrl', K.ctrl_l: 'ctrl', K.ctrl_r: 'ctrl', K.alt: 'alt', K.alt_l: 'alt',
               K.alt_r: 'alt', K.alt_gr: 'alt', K.shift: 'shift', K.shift_l: 'shift', K.shift_r: 'shift'}
    held, down = set(), set()                     # modifiers held; keys down (auto-repeat isn't a press)

    def vk_of(key):
        vk = getattr(key, 'vk', None)
        return vk if vk is not None else getattr(getattr(key, 'value', None), 'vk', None)

    def label(mods, key, vk):
        if 0x30 <= vk <= 0x5A:
            name = chr(vk)
        elif 0x70 <= vk <= 0x87:
            name = f'F{vk - 0x6F}'
        else:
            name = getattr(key, 'name', None) or hex(vk)
        return '+'.join([m.capitalize() for m in ('ctrl', 'alt', 'shift') if m in mods] + [name])

    def on_press(key):
        global _press
        if key in mods_of:
            held.add(mods_of[key])
            return
        vk = vk_of(key)
        if vk is None:
            return
        if _capture[0]:                           # the window waits for a new shortcut
            done, _capture[0] = _capture[0], None
            if key != K.esc:
                HOTKEY.update(mods=sorted(held), vk=vk, label=label(held, key, vk))
                _save_checked()
                print(f'[JUMP] the shortcut is now {HOTKEY["label"]}')
            done(HOTKEY['label'])
            down.add(vk)
            return
        if vk == HOTKEY['vk'] and held == set(HOTKEY['mods']) and vk not in down:
            _press += 1
            threading.Thread(target=_jump_and_hit, args=(_press,), daemon=True).start()
        down.add(vk)

    def on_release(key):
        if key in mods_of:
            held.discard(mods_of[key])
        else:
            down.discard(vk_of(key))

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()


def main():
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = sys.stderr = _Tee(sys.__stdout__, os.path.join(log_dir, 'heal.log'))
    _load_checked()
    _load_enemies()
    print(f'[HEAL] an ointment when HP is more than {HP_MISSING} below the max, a pill when MP is below '
          f'{MP_MIN_PCT}%, pouches bought from the mall - only for the accounts ticked in the window')
    threading.Thread(target=_heal_loop, daemon=True, name='heal').start()
    threading.Thread(target=_kills_loop, daemon=True, name='kills').start()
    _hotkeys()
    print(f'[JUMP] {HOTKEY["label"]} in a game: jump next to the player in the chat name box, then use the '
          f'right-click skill on them x{RCLICK_TIMES}')
    _window()                      # closing the window stops healing
    print('[HEAL] window closed - stopped')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[HEAL] stopped')
