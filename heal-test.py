"""heal.py's auto heal, HP only and one pouch at a time - run it on its own (python heal-test.py).

Same drinking as heal.py (same functions, imported from it), with these differences:
  - MP is left alone: no SerenityPill drunk, no SerenityPouch opened or bought
  - a 7StarPouch is opened only once the 7StarOintments are all gone
  - one 7StarPouch is bought from the mall only once the last one is used up (no ointments and
    no pouch left) - so the bag never holds more than one pouch
Logs to logs/heal-test.log. Like heal.py it WRITES to the game (scripts/game_actions.py)."""
import os
import sys
import time

import heal
from heal import game_actions, game_memory

# MP: never drink, never open, never buy
heal.MP_MIN_PCT = 0
heal.KEEP_MP_POTIONS = -1
heal.MP_POUCHES = 0
# HP: open the pouch only when the ointments are gone
heal.KEEP_HP_POTIONS = 0


def _restock(pid, name, v):
    """One 7StarPouch from the mall once the last one is used up: no 7StarOintment and no
    7StarPouch left (and room in the bag to open it)."""
    pots = v['potions']
    if pots['7StarOintment'] or pots['7StarPouch']:
        return
    if time.time() - heal._last_buy.get(name, 0) < heal.BUY_WAIT:
        return
    if game_actions.BAG_SLOTS - v['bag'] < 1 + heal.POUCH_OPENS_INTO:
        print(f'[HEAL-TEST] {name}: no 7StarPouch left and no room in the bag to buy and open one')
        heal._last_buy[name] = time.time()
        return
    heal._last_buy[name] = time.time()
    try:
        game_actions.buy(pid, game_actions.POUCHES['7StarPouch'], 1)
        print(f'[HEAL-TEST] {name}: bought 1 7StarPouch from the mall ({v["cp"]} CPs)')
    except Exception as e:
        print(f'[HEAL-TEST] {name}: buying a 7StarPouch failed ({type(e).__name__}: {e})')


def main():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = sys.stderr = heal._Tee(sys.__stdout__, os.path.join(log_dir, 'heal-test.log'))
    print(f'[HEAL-TEST] watching every game: an ointment when HP is more than {heal.HP_MISSING} below '
          f'the max, one 7StarPouch at a time, MP left alone - Ctrl+C to stop')
    while True:
        busy = False
        for pid in game_memory.game_pids():
            name = game_memory._name_pid(pid)
            v = game_memory.vitals_pid(pid) if name else None
            if not v or v['hp'] <= 0:          # not logged in / dead: nothing to drink
                continue
            _restock(pid, name, v)
            busy = heal._tend(pid, name, v) or busy
        time.sleep(heal.DRINK_WAIT if busy else heal.CHECK_EVERY)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[HEAL-TEST] stopped')
