"""Auto heal for every running account - run it on its own (python scripts/heal.py).

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
import os
import sys
import time

import game_actions
import game_memory

HP_MISSING  = 100   # drink once HP is this much below the max
MP_MIN_PCT  = 90     # drink a pill once MP is below this % of the highest MP seen
MP_MAX      = 800    # max MP to assume until a higher MP is seen
CHECK_EVERY = 0.1    # seconds between reads of every game
DRINK_WAIT  = 0.05   # seconds between checks while an account is being healed
DRINK_AGAIN = 1.0    # after a potion, the next one of its kind waits until HP / MP moved, or this long
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


def main():
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = sys.stderr = _Tee(sys.__stdout__, os.path.join(log_dir, 'heal.log'))
    print(f'[HEAL] watching every game: an ointment when HP is more than {HP_MISSING} below the max, '
          f'a pill when MP is below {MP_MIN_PCT}%, pouches bought from the mall - Ctrl+C to stop')
    while True:
        busy = False
        for pid in game_memory.game_pids():
            name = game_memory._name_pid(pid)
            v = game_memory.vitals_pid(pid) if name else None
            if not v or v['hp'] <= 0:          # not logged in / dead: nothing to drink
                continue
            _restock(pid, name, v)
            busy = _tend(pid, name, v) or busy
        time.sleep(DRINK_WAIT if busy else CHECK_EVERY)   # someone is healing: check again right away


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[HEAL] stopped')
