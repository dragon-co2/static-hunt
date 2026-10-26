"""Hunts in the background, through the game itself (injection) - no window in front, no keys, no
virtual desktops: casts Scatter 4 times around the character, one per direction, over and over -
what the game's own auto right click does with its 4 points around the screen - and puts the +
items that land in the bag into the VIP compose warehouse (Deposit).
(game_actions.py cast_at / compose_deposit, game_memory.py bag_plus_items.)
    python background_hunt.py                every logged-in character with Scatter, all at once
    python background_hunt.py <character>    only that one"""
import sys
import threading
import time

import game_actions
import game_memory

SCATTER      = 8001             # the skill cast around the character (8001 = Scatter)
SCATTER_CELLS = 5               # how far from the character each cast is aimed
DIRECTIONS   = ((1, 0), (0, 1), (-1, 0), (0, -1))   # map directions = the screen's 4 corners
CMD_STANDING = 5                # my command once a cast is over
CAST_TIMEOUT = 2.0              # longest wait for one cast to end before the next
CAST_DELAY   = 0.1              # extra seconds after a cast, before the next
DEPOSIT_PLUS = True             # + items in the bag go to the VIP compose warehouse (Deposit)
DEPOSIT_EVERY = 10.0            # seconds between looks at the bag and the equipped arrows
ARROWS_MIN   = 100              # default for the settings' ARROWS_MIN: fewer equipped -> a new pack
BANK_WAIT    = 4.0              # seconds for the server to answer a bank / equip request
DRAGONBALLS_MIN = 10            # dragonballs before one is used (10 make a dragonball scroll)
REGULAR_BANKS = [(8, 10), (10011, 10), (10012, 10), (10027, 10), (10028, 10)]   # remote bank tabs
                                # for the scrolls - not the arrows tab (4101) nor (44)
ARROW_TABS = [game_memory.ARROW_BANK] + REGULAR_BANKS + [(44, 10)]   # all 7 remote bank tabs: where
                                # an arrow pack is looked for, in order


def _wait_cast(pid):
    """Waits for the cast to start and end (my command leaves 'standing', then is back to it)."""
    end = time.time() + 0.3
    while time.time() < end and game_actions.my_command(pid) == CMD_STANDING:
        time.sleep(0.02)
    end = time.time() + CAST_TIMEOUT
    while time.time() < end and game_actions.my_command(pid) != CMD_STANDING:
        time.sleep(0.02)


def scatter_around(pid):
    """One round: Scatter aimed SCATTER_CELLS away in each of the 4 directions."""
    for dx, dy in DIRECTIONS:
        me = game_actions.my_pos(pid)
        if not me:
            return
        game_actions.cast_at(pid, SCATTER, me[0] + dx * SCATTER_CELLS, me[1] + dy * SCATTER_CELLS)
        _wait_cast(pid)
        time.sleep(CAST_DELAY)


def _arrows_min():
    """General settings' ARROWS_MIN (the same one grab_arrows.py uses)."""
    from dragon_settings import get_settings
    return int(get_settings('static', {'ARROWS_MIN': ARROWS_MIN})['ARROWS_MIN'])


def _wait(check, seconds):
    end = time.time() + seconds
    while time.time() < end:
        found = check()
        if found:
            return found
        time.sleep(0.2)
    return None


def ensure_arrows(pid, name):
    """Below ARROWS_MIN arrows equipped: equips a fuller pack from the bag, else takes the biggest
    pack out of the bank (opened from afar, loaded first if this client hasn't yet) and equips
    that - the arrows tab first, then every other tab. True if fine, None if no tab holds a pack,
    False if it failed."""
    equipped = game_memory._read_equipped_pid(pid)[0]
    if equipped is None or equipped >= _arrows_min():
        return True
    packs = [p for p in game_memory.bag_arrow_packs(pid) if p[1] > equipped]   # only a fuller one
    if packs:
        return _equip(pid, name, packs, equipped)
    try:
        return _arrows_from_bank(pid, name, equipped)
    finally:
        game_actions.close_bank_windows(pid)            # the bank and the bag it brought up


def _arrows_from_bank(pid, name, equipped):
    """The biggest arrow pack out of the bank (opened from afar), equipped: the arrows tab first,
    then the other 6 tabs (ARROW_TABS). None if no tab holds one."""
    for key in ARROW_TABS:
        if not _open_bank(pid, key):                    # from afar, reloaded (else no withdraw)
            continue
        time.sleep(1)
        bank = _wait(lambda: game_memory.bank_items(pid, key), BANK_WAIT) or []
        in_bank = sorted((i for i in bank if i[1] in game_memory.ARROW_TYPES), key=lambda i: -i[2])
        if in_bank:
            break
    else:
        print(f'{name}: only {equipped} arrows equipped and no arrow pack in any bank tab')
        return None
    pack_id = in_bank[0][0]
    game_actions.bank_withdraw(pid, pack_id, key)
    packs = _wait(lambda: [p for p in game_memory.bag_arrow_packs(pid) if p[0] == pack_id], BANK_WAIT)
    if not packs:
        print(f'{name}: the arrow pack never left the bank')
        return False
    return _equip(pid, name, packs, equipped)


def _equip(pid, name, packs, equipped):
    """Equips the fullest of `packs` (bag arrow packs). True once the equipped count went up."""
    pack_id, amount = max(packs, key=lambda p: p[1])
    game_actions.use(pid, pack_id)
    now = _wait(lambda: (game_memory._read_equipped_pid(pid)[0] or 0) > equipped and
                game_memory._read_equipped_pid(pid)[0], BANK_WAIT)
    print(f'{name}: only {equipped} arrows were left - equipped a pack of {amount}'
          + ('' if now else ' (not confirmed yet)'))
    return bool(now)


def _open_bank(pid, key):
    """Opens bank tab `key` from afar and (re)loads it - the server takes a deposit / withdraw only
    after that. True once its items are known."""
    game_actions.remote_bank_open(pid)
    time.sleep(0.5)
    if game_memory.bank_items(pid, key) is None:
        game_actions.bank_open(pid, key)
    else:
        game_actions.bank_refresh(pid, key)
    return _wait(lambda: game_memory.bank_items(pid, key) is not None, BANK_WAIT) is not None


def dragonball_scrolls(pid, name):
    """DRAGONBALLS_MIN or more dragonballs -> one is used (the game makes them a dragonball scroll);
    any scroll in the bag goes into a regular bank tab (the next one if a tab is full)."""
    balls = game_memory.bag_items_of(pid, game_memory.DRAGONBALL)
    if len(balls) >= DRAGONBALLS_MIN:
        game_actions.use(pid, balls[0])
        _wait(lambda: game_memory.bag_items_of(pid, game_memory.DB_SCROLL), BANK_WAIT)
        print(f'{name}: {len(balls)} dragonballs made into a scroll')
    if not game_memory.bag_items_of(pid, game_memory.DB_SCROLL):
        return
    try:
        _bank_scrolls(pid, name)
    finally:
        game_actions.close_bank_windows(pid)            # the bank and the bag it brought up


def _bank_scrolls(pid, name):
    """Every dragonball scroll in the bag into a regular bank tab (the next one if a tab is full)."""
    _bank_all(pid, name, game_memory.DB_SCROLL, 'dragonball scroll(s)')


def _bank_all(pid, name, item_type, what):
    """Every bag item of `item_type` into a regular bank tab (the next one if a tab is full)."""
    for key in REGULAR_BANKS:
        items = game_memory.bag_items_of(pid, item_type)
        if not items:
            return
        if not _open_bank(pid, key):
            continue
        for item in items:
            game_actions.bank_deposit(pid, item, key)
            time.sleep(0.5)
        if _wait(lambda: not game_memory.bag_items_of(pid, item_type), BANK_WAIT) is not None:
            print(f'{name}: {len(items)} {what} put in bank tab {key}')
            return
    print(f'{name}: {what} still in the bag - every bank tab refused them (full?)')


def bank_dragonballs(pid, name):
    """The dragonballs in the bag into a regular bank tab (from afar), the windows closed after."""
    if not game_memory.bag_items_of(pid, game_memory.DRAGONBALL):
        return
    try:
        _bank_all(pid, name, game_memory.DRAGONBALL, 'dragonball(s)')
    finally:
        game_actions.close_bank_windows(pid)


def withdraw_dragonballs(pid, name, room):
    """Up to `room` dragonballs out of the regular bank tabs (from afar) into the bag, the windows
    closed after. Returns their ids in the bag."""
    taken = []
    try:
        for key in REGULAR_BANKS:
            if len(taken) >= room:
                break
            if not _open_bank(pid, key):
                continue
            time.sleep(1)
            balls = [i[0] for i in game_memory.bank_items(pid, key) or [] if i[1] == game_memory.DRAGONBALL]
            for ball in balls[:room - len(taken)]:
                game_actions.bank_withdraw(pid, ball, key)
                taken.append(ball)
                time.sleep(0.3)
        _wait(lambda: set(taken) <= set(game_memory.bag_items_of(pid, game_memory.DRAGONBALL)), BANK_WAIT)
    finally:
        game_actions.close_bank_windows(pid)
    got = [b for b in taken if b in game_memory.bag_items_of(pid, game_memory.DRAGONBALL)]
    if got:
        print(f'{name}: {len(got)} dragonball(s) out of the bank')
    return got


def repair(pid, name):
    """VIP Repair All: everything the character wears, for 1000 gold. (The plain MsgItem repair of
    one item is ignored by this server.)"""
    game_actions.repair_all(pid)
    print(f'{name}: Repair All (VIP) sent')


def deposit_plus(pid, name):
    """+ items in the bag -> the VIP compose warehouse (Deposit). Returns how many went."""
    plus_items = game_memory.bag_plus_items(pid)
    if not plus_items:
        return 0
    game_actions.compose_deposit(pid)
    print(f'{name}: {len(plus_items)} + item(s) in the bag {plus_items} - deposited to the compose warehouse')
    return len(plus_items)


def hunt(pid, name):
    """Scatter rounds for one character until its game closes; + items deposited on the way."""
    print(f'{name}: scatter around')
    rounds, checked_bag = 0, 0.0
    while game_memory._name_pid(pid) == name:
        try:
            if time.time() - checked_bag >= DEPOSIT_EVERY:
                checked_bag = time.time()
                if DEPOSIT_PLUS:
                    deposit_plus(pid, name)
                ensure_arrows(pid, name)
            scatter_around(pid)
            rounds += 1
        except (OSError, TimeoutError):
            time.sleep(1)
    print(f'{name}: its game closed / logged out - stopped after {rounds} rounds')


def main():
    """python background_hunt.py              every logged-in character with Scatter, each on its own
       python background_hunt.py <character>  only that one"""
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    accounts.pop(None, None)
    if len(sys.argv) > 1:
        if sys.argv[1] not in accounts:
            sys.exit(f'{sys.argv[1]} is not logged in')
        accounts = {sys.argv[1]: accounts[sys.argv[1]]}
    picked = {n: p for n, p in accounts.items() if SCATTER in game_actions.skills(p)}
    for name in accounts.keys() - picked.keys():
        print(f'{name}: does not have skill {SCATTER} - skipped')
    if not picked:
        sys.exit('no logged-in character with Scatter')
    workers = [threading.Thread(target=hunt, args=(p, n), daemon=True) for n, p in picked.items()]
    for worker in workers:
        worker.start()
    print(f'hunting with {", ".join(picked)} - Ctrl+C to stop')
    try:
        while any(w.is_alive() for w in workers):
            time.sleep(1)
    except KeyboardInterrupt:
        print('stopped')


if __name__ == '__main__':
    main()
