"""Hunts the monsters around a character to level its weapon: reads them from memory, jumps next
to the nearest one, hits it once with the weapon (like left-clicking it - one hit kills) and jumps
straight on to the next. Runs until Ctrl+C. (game_actions.py roles / jump_next_to / attack.)
    python monk_hunt.py <character>
    python monk_hunt.py                (the only logged-in character)"""
import struct
import sys
import time

import game_actions
import game_memory

MONSTER_IDS = range(400000, 500000)   # role ids of monsters (players are 1,000,000 and up)
SKIP        = set()                   # monster names never to attack, e.g. {'Guard'}
MAX_CELLS   = 15                      # only monsters this close (a jump is 16 cells at most)
JUMP_DELAY  = 0.2                     # seconds for a jump to land before hitting
HIT_DELAY   = 0.2                     # seconds after the hit before jumping to the next monster
HIT_AGAIN   = 3.0                     # a monster still alive this long after its hit gets hit again
CMD_DIE     = 6                       # a monster's command while it dies


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


def hit(pid, mob_id, distance):
    """Jumps next to the monster and hits it once - no waiting for it to die."""
    if distance > 1:
        game_actions.jump_next_to_id(pid, mob_id)
        time.sleep(JUMP_DELAY)
    game_actions.attack(pid, mob_id)
    time.sleep(HIT_DELAY)


def main():
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    accounts.pop(None, None)
    if len(sys.argv) > 1:
        name = sys.argv[1]
    elif len(accounts) == 1:
        name = next(iter(accounts))
    else:
        sys.exit(f'which character? logged in: {", ".join(accounts) or "none"}')
    pid = accounts.get(name)
    if not pid:
        sys.exit(f'{name} is not logged in')
    print(f'{name}: hunting - Ctrl+C to stop')
    hits, hit_ids, said = 0, {}, None             # hit_ids: monster id -> when it may be hit again
    try:
        while True:
            now = time.time()
            hit_ids = {i: t for i, t in hit_ids.items() if t > now}
            try:
                me = game_actions.my_pos(pid)
                found = monsters(pid, me, hit_ids) if me else []
            except (OSError, TimeoutError, struct.error):
                if not game_memory._name_pid(pid):
                    sys.exit(f'{name}: the game closed / logged out')
                time.sleep(0.5)
                continue
            if not found:
                if said != 'none':
                    print(f'{name}: no monsters within {MAX_CELLS} cells - waiting')
                    said = 'none'
                time.sleep(0.5)
                continue
            said = None
            distance, mob, mob_id = found[0]
            try:
                hit(pid, mob_id, distance)
            except (OSError, TimeoutError):
                time.sleep(0.5)
                continue
            hit_ids[mob_id] = time.time() + HIT_AGAIN
            hits += 1
            print(f'{name}: hit {mob} ({hits})')
    except KeyboardInterrupt:
        print(f'{name}: stopped - {hits} hits')


if __name__ == '__main__':
    main()
