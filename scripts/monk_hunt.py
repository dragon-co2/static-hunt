"""Hunts the monsters around a character to level its weapon: reads them from memory, jumps next
to the nearest one, hits it once with the weapon (like left-clicking it - one hit kills) and jumps
straight on to the next. (game_actions.py roles / positions / jump_next_to_id / attack.)
A window lists the logged-in accounts: the ticked ones hunt, each on its own; closing it stops.
    python monk_hunt.py"""
import struct
import threading
import time

import game_actions
import game_memory

MONSTER_IDS = range(400000, 500000)   # role ids of monsters (players are 1,000,000 and up)
SKIP        = set()                   # monster names never to attack, e.g. {'Guard'}
MAX_CELLS   = 25                      # only monsters this close (one jump is 16 cells at most)
JUMP_DELAY  = 0.1                     # extra seconds after the jump has landed, before hitting
HIT_DELAY   = 0.5                     # seconds after the hit before jumping to the next monster
HIT_AGAIN   = 3.0                     # a monster still alive this long after its hit gets hit again
CMD_DIE     = 46                      # a monster's command once dead (a corpse, ~4s before it fades)
CMD_ATTACKING = {30}                  # my command once the game has taken the hit
CMD_IN_AIR  = {20, 25}                # my command during a jump (25: the end of a long one)
HIT_TRIES   = 3                       # sends of the hit until the game starts attacking
JUMP_TRIES  = 2                       # jumps to get next to it (a monster walks meanwhile)
REJUMP_CELLS = 2                      # after landing, jump again only if it's farther than this (the
                                      # game refuses a jump of a cell or two anyway)
JUMP_TIMEOUT = 2.5                    # longest wait for a jump to land (a 15-cell one takes ~1.5s)
XP_SLOT     = 9                       # hotkey slot used while the XP bar is full: 9 = F10 (0 = F1);
                                      # None = never. Pressed from inside the game, not as a key
XP_EVERY    = 2.0                     # seconds between presses while the XP bar stays full
LOOT_PLUS   = 1                       # pick up items of +1 or better from the ground; 0 = not by plus
LOOT        = set()                   # and these item types too (e.g. {1091000} = GoldBullion).
                                      # Picked up before the next monster
LOOT_CELLS  = 15                      # only loot this close
LOOT_RUN_CELLS = 2                    # this close: just run to it; farther: jump next to it first
LOOT_TIMEOUT = 3.0                    # seconds to get one item before leaving it for LOOT_SKIP
LOOT_SKIP   = 20.0
LOOT_WAIT   = 0.4                     # seconds after a pick-up try, for the server to answer
DEPOSIT_PLUS = True                   # + items in the bag go to the VIP compose warehouse (Deposit)
DEPOSIT_EVERY = 10.0                  # seconds between looks at the bag (and right after a pick-up)


def _distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _wanted(kind, plus):
    """True for an item worth picking up: LOOT_PLUS or better, or one of the LOOT types."""
    return (LOOT_PLUS and plus >= LOOT_PLUS) or kind in LOOT


def _loot_near(pid, me, skip_ids):
    """[(item id, type, plus, x, y)] of the wanted items on the ground within LOOT_CELLS, nearest
    first."""
    items = [i for i in game_memory.ground_items(pid)
             if _wanted(i[1], i[2]) and i[0] not in skip_ids and _distance(me, i[3:]) <= LOOT_CELLS]
    return sorted(items, key=lambda i: _distance(me, i[3:]))


def _sign(n):
    return (n > 0) - (n < 0)


def collect(pid, item_id, x, y):
    """Picks the item up like a player would: jumps next to it (a cell short, on our side) when it's
    far, then runs onto it with the game's own pick-up (left-clicking it). True once it's gone from
    the ground. A monster's hit cancels the run, so it's tried again until LOOT_TIMEOUT."""
    end = time.time() + LOOT_TIMEOUT
    while time.time() < end:
        if not any(i[0] == item_id for i in game_memory.ground_items(pid)):
            return True
        me = game_actions.my_pos(pid)
        if not me:
            return False
        if _distance(me, (x, y)) > LOOT_RUN_CELLS:          # far: jump next to it first
            game_actions.jump_to(pid, x + _sign(me[0] - x), y + _sign(me[1] - y))
            _wait_for(pid, lambda c: c in CMD_IN_AIR, 0.3)
            _wait_for(pid, lambda c: c not in CMD_IN_AIR, JUMP_TIMEOUT)
            continue
        game_actions.go_pick_up(pid, item_id, x, y)              # run onto it and pick it up
        time.sleep(LOOT_WAIT)
    return not any(i[0] == item_id for i in game_memory.ground_items(pid))


def monsters(pid, me, skip_ids):
    """[(distance, name, id)] of the live monsters around, nearest first. Only reads memory here;
    the game itself is only ever handed monster ids (a monster's object is freed when it dies)."""
    names = {}
    for name, role in game_actions.roles(pid):
        try:
            mob_id = game_actions.role_id(pid, role)
            if mob_id not in MONSTER_IDS or mob_id in skip_ids or name in SKIP:
                continue
            if game_actions.monster_dying(pid, role) or game_actions.role_command(pid, role) == CMD_DIE:
                continue
        except OSError:                                # freed while we were reading it
            continue
        names[mob_id] = name
    found = [(_distance(pos, me), names[mob_id], mob_id)
             for mob_id, pos in game_actions.positions(pid, names).items()]
    return sorted(f for f in found if f[0] <= MAX_CELLS)


def _wait_for(pid, done, timeout):
    """Polls my own command until done(command) or timeout; True if it got there."""
    end = time.time() + timeout
    while time.time() < end:
        if done(game_actions.my_command(pid)):
            return True
        time.sleep(0.02)
    return False


def hit(pid, mob_id, distance):
    """Jumps next to the monster and hits it once - no waiting for it to die. The hit is sent once
    the jump has landed (the game ignores it in the air) and sent again if the game didn't start
    attacking."""
    for n in range(JUMP_TRIES):
        if distance <= (1 if n == 0 else REJUMP_CELLS):     # close enough: hit (the game's own attack
            break                                           # steps into reach if it must)
        game_actions.jump_next_to_id(pid, mob_id)
        _wait_for(pid, lambda c: c in CMD_IN_AIR, 0.3)                       # the jump starts
        _wait_for(pid, lambda c: c not in CMD_IN_AIR, JUMP_TIMEOUT)          # ... and lands
        time.sleep(JUMP_DELAY)
        me, there = game_actions.my_pos(pid), game_actions.positions(pid, [mob_id]).get(mob_id)
        if not me or not there:                             # it died / left meanwhile
            return
        distance = _distance(me, there)                     # it walks: landed on it / short of it?
    for _ in range(HIT_TRIES):
        game_actions.attack(pid, mob_id)
        if _wait_for(pid, lambda c: c in CMD_ATTACKING, 0.3):
            break
    time.sleep(HIT_DELAY)


_lock    = threading.Lock()
_checked = set()      # characters ticked in the window - only these hunt
_status  = {}         # character -> what its hunt is doing (shown in the window)
_hunting = {}         # character -> its hunt thread


def _hunt(pid, name):
    """One character's hunt, on a thread of its own - until it's unticked or its game closes."""
    hits, hit_ids = 0, {}                         # hit_ids: monster id -> when it may be hit again
    xp_pressed = 0.0                              # when the XP hotkey was last pressed
    picked, loot_skip = 0, {}                     # loot_skip: item id -> when to try it again
    deposited, checked_bag = 0, 0.0
    print(f'{name}: hunting')
    while True:
        if not ticked(name):
            break
        now = time.time()
        if XP_SLOT is not None and now - xp_pressed >= XP_EVERY and game_actions.xp_ready(pid):
            try:
                game_actions.press_hotkey(pid, XP_SLOT)
                xp_pressed = now
                print(f'{name}: XP bar full - F{XP_SLOT + 1} pressed')
            except (OSError, TimeoutError):
                pass
        if DEPOSIT_PLUS and now - checked_bag >= DEPOSIT_EVERY:
            checked_bag = now
            plus_items = game_memory.bag_plus_items(pid)
            if plus_items:
                try:
                    game_actions.compose_deposit(pid)
                    deposited += len(plus_items)
                    print(f'{name}: {len(plus_items)} + item(s) in the bag - deposited to the compose warehouse')
                except (OSError, TimeoutError) as e:
                    print(f'{name}: deposit failed - {e}')
        hit_ids = {i: t for i, t in hit_ids.items() if t > now}
        loot_skip = {i: t for i, t in loot_skip.items() if t > now}
        try:
            me = game_actions.my_pos(pid)
            loot = _loot_near(pid, me, loot_skip) if me and (LOOT or LOOT_PLUS) else []
            if loot:
                item_id, kind, plus, x, y = loot[0]
                if collect(pid, item_id, x, y):
                    picked += 1
                    checked_bag = 0.0                     # look at the bag (deposit) right away
                    print(f'{name}: picked up item {kind} +{plus} ({picked})')
                else:
                    loot_skip[item_id] = time.time() + LOOT_SKIP
                continue
            found = monsters(pid, me, hit_ids) if me else []
        except (OSError, TimeoutError, struct.error):
            if game_memory._name_pid(pid) != name:
                break
            time.sleep(0.5)
            continue
        if not found:
            _status[name] = f'{hits} hits - no monsters within {MAX_CELLS} cells'
            time.sleep(0.5)
            continue
        distance, mob, mob_id = found[0]
        try:
            hit(pid, mob_id, distance)
        except (OSError, TimeoutError):
            time.sleep(0.5)
            continue
        hit_ids[mob_id] = time.time() + HIT_AGAIN
        hits += 1
        _status[name] = f'{hits} hits, {picked} picked up, {deposited} deposited - last: {mob}'
    _status[name] = f'stopped - {hits} hits, {picked} picked up, {deposited} deposited'
    print(f'{name}: stopped - {hits} hits, {picked} picked up, {deposited} deposited')


def _accounts():
    """{character: pid} of the games that are logged in."""
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    accounts.pop(None, None)
    return accounts


def ticked(name):
    """True while the character is ticked in the window - a hunt runs until this turns False."""
    with _lock:
        return name in _checked


def _window(hunt=None, title='Monk hunt', heading='Ticked accounts hunt the monsters around them'):
    """The checklist: one row per logged-in account - tick it to run `hunt`(pid, name) with it (on
    a thread of its own, until it's unticked: ticked(name); it shows its doing in _status[name])."""
    import tkinter as tk

    hunt = hunt or _hunt
    root = tk.Tk()
    root.title(title)
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tk.Label(root, text=heading, font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(8, 4))
    rows = tk.Frame(root)
    rows.pack(fill='x', padx=10)
    boxes = {}   # character -> (BooleanVar, Label)
    accounts = {}

    def toggle(name, var):
        with _lock:
            (_checked.add if var.get() else _checked.discard)(name)
        start(name)

    def start(name):
        """Starts the character's hunt if it's ticked, logged in and not hunting already."""
        with _lock:
            ticked = name in _checked
        thread = _hunting.get(name)
        if ticked and name in accounts and not (thread and thread.is_alive()):
            _hunting[name] = threading.Thread(target=hunt, args=(accounts[name], name), daemon=True)
            _hunting[name].start()

    def tick_all(on):
        for name, (var, _) in boxes.items():
            if var.get() != on:
                var.set(on)
                toggle(name, var)

    def refresh():
        accounts.clear()
        accounts.update(_accounts())
        for name in sorted(set(accounts) | set(boxes), key=str.lower):
            if name not in boxes:
                var = tk.BooleanVar(value=False)
                row = tk.Frame(rows)
                row.pack(fill='x', pady=1)
                tk.Checkbutton(row, text=name, variable=var, width=16, anchor='w',
                               font=('Segoe UI', 10), command=lambda n=name, v=var: toggle(n, v)).pack(side='left')
                info = tk.Label(row, font=('Consolas', 9), anchor='w', width=46)
                info.pack(side='left')
                boxes[name] = (var, info)
            var, info = boxes[name]
            if name not in accounts:
                info.config(text='not running', fg='gray')
                continue
            start(name)                              # e.g. a ticked account that logged in again
            thread = _hunting.get(name)
            if thread and thread.is_alive():
                info.config(text=_status.get(name, 'hunting'), fg='green')
            else:
                info.config(text=_status.get(name, 'not hunting'), fg='black')
        root.after(500, refresh)

    buttons = tk.Frame(root)
    buttons.pack(fill='x', padx=10, pady=8)
    tk.Button(buttons, text='All', width=8, command=lambda: tick_all(True)).pack(side='left')
    tk.Button(buttons, text='None', width=8, command=lambda: tick_all(False)).pack(side='left', padx=6)
    refresh()
    root.mainloop()


def main():
    print('Monk hunt: tick the accounts to hunt with in the window - closing it stops them all')
    _window()
    print('window closed - stopped')


if __name__ == '__main__':
    main()
