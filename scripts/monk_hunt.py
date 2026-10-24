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
MAX_CELLS   = 15                      # only monsters this close (a jump is 16 cells at most)
JUMP_DELAY  = 0.1                     # extra seconds after the jump has landed, before hitting
HIT_DELAY   = 0.5                     # seconds after the hit before jumping to the next monster
HIT_AGAIN   = 3.0                     # a monster still alive this long after its hit gets hit again
CMD_DIE     = 46                      # a monster's command once dead (a corpse, ~4s before it fades)
CMD_ATTACKING = {30, 25}              # my command once the game has taken the hit (go attack, attacking)
HIT_TRIES   = 3                       # sends of the hit until the game starts attacking
JUMP_TIMEOUT = 1.5                    # longest wait for a jump to land


def _distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def monsters(pid, me, skip_ids):
    """[(distance, name, id)] of the live monsters around, nearest first. Only reads memory here;
    the game itself is only ever handed monster ids (a monster's object is freed when it dies)."""
    names = {}
    for name, role in game_actions.roles(pid):
        try:
            mob_id = game_actions.role_id(pid, role)
            if mob_id not in MONSTER_IDS or mob_id in skip_ids or name in SKIP:
                continue
            if game_actions.role_dead(pid, role) or game_actions.role_command(pid, role) == CMD_DIE:
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
    if distance > 1:
        game_actions.jump_next_to_id(pid, mob_id)
        _wait_for(pid, lambda c: c == game_actions.CMD_JUMP, 0.3)            # the jump starts
        _wait_for(pid, lambda c: c != game_actions.CMD_JUMP, JUMP_TIMEOUT)   # ... and lands
        time.sleep(JUMP_DELAY)
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
    print(f'{name}: hunting')
    while True:
        with _lock:
            if name not in _checked:
                break
        now = time.time()
        hit_ids = {i: t for i, t in hit_ids.items() if t > now}
        try:
            me = game_actions.my_pos(pid)
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
        _status[name] = f'{hits} hits - last: {mob}'
    _status[name] = f'stopped - {hits} hits'
    print(f'{name}: stopped - {hits} hits')


def _accounts():
    """{character: pid} of the games that are logged in."""
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    accounts.pop(None, None)
    return accounts


def _window():
    """The checklist: one row per logged-in account - tick it to hunt with it."""
    import tkinter as tk

    root = tk.Tk()
    root.title('Monk hunt')
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tk.Label(root, text='Ticked accounts hunt the monsters around them',
             font=('Segoe UI', 10, 'bold')).pack(anchor='w', padx=10, pady=(8, 4))
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
            _hunting[name] = threading.Thread(target=_hunt, args=(accounts[name], name), daemon=True)
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
