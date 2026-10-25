"""Moves a character across its map by itself, in the background (injection): finds a way through
the map's walkable cells - the client's own map file (map/map/*.DMap) - and jumps along it, each
jump 15 cells or less (game_actions.jump_to, the game's own Jump).
Also knows the hunting areas of each map - the ones the game's Path Finding lists (its encrypted
ini/MapDestination.dat) - and goes to the nearest one, then on to the nearest monster.
    python travel.py <character>              to the nearest hunting area, then to a monster
    python travel.py <character> <x> <y>      to that cell of its map
    python travel.py <character> --areas      the hunting areas of its map
    python travel.py <character> --tour       for good: every hunting area of the map in turn (30s
                                              jumping among its monsters), then a VIP teleport
                                              to the next city"""
import ctypes
import ctypes.wintypes
import heapq
import math
import os
import random
import re
import struct
import sys
import time

import background_hunt
import game_actions
import game_memory

MAP_ID     = 0xE0          # [game map] + this: the map's id (1002 Twin City, 1011 Phoenix Castle...)
JUMP_CELLS  = 15            # longest jump, straight-line (the game refuses more than 16)
ARRIVED     = 2             # this close to the goal counts as there
JUMP_WAIT   = 2.5           # longest wait for one jump to land
REFUSED_WAIT = 0.4          # not moved after a jump: wait this long more before calling it refused
GIVE_UP     = 8             # refused jumps in a row before a trip gives up (the tour moves on)
CAST_SENT   = 0.15          # bounce: seconds after a Scatter before jumping on (not its animation)
SCATTER_MIN = 3             # auto hunt: a Scatter only with this many monsters or more...
SCATTER_REACH = 6           # ...this close (also a monster's crowd, which guides where to jump next)
SCATTER_EVERY = 0.5         # ...and one this often at most (once landed), aimed the way it went
MIN_JUMP    = 3             # bounce: monsters closer than this aren't jumped to (the game refuses a
                            # jump of a cell or two; the Scatter hits them anyway)
CMD_IN_AIR  = {20, 25}      # my command during a jump
NEIGHBOURS  = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
AREA_GROUPS = re.compile(r'(?i)hunt|level ?up|monster')   # Path Finding groups that list hunting areas
MONSTER_CELLS = 18          # at the area: a monster this close ends the trip
SKIP        = ('guard',)    # monsters whose name starts so don't count (the guards' ids are monsters')
LOOT_CELLS  = 40            # + items lying this close are gone for (farther than a jump: the way
                            # there first) - the jumps carry it away from where a monster died
BEEN_SECONDS = 10          # bounce: a monster just jumped next to isn't the next one for this long
LANDINGS    = 6             # bounce: its last landing spots kept...
LANDED_CELLS = 5            # ...and monsters this close to one of them not picked (unless no other)
AREA_SECONDS = 500           # tour: time among each hunting area's monsters before the next area
EMPTY_SECONDS = 8           # tour: an area with no monsters this long (none came back) is left
STATUS_EVERY = 5            # seconds between the console lines saying where the hunt is at
AREA_RADIUS = 40           # tour: an area's monsters are the ones this close to its spot
TOUR_SECONDS = 500           # tour: time around a map with no hunting areas listed (exploring it)
EXPLORE_NEAR, EXPLORE_FAR = 60, 150   # explore: how far the random spots are
TELEPORT_WAIT = 10          # seconds for a teleport to land on the new map
TELEPORTS   = [(21, 1002), (22, 1011), (23, 1020), (24, 1000), (25, 1015)]   # VIP Remote Travel:
TELEPORT_NAMES = {21: 'Twin City', 22: 'Phoenix Castle', 23: 'Ape City', 24: 'Desert',   # (code, map)
                  25: 'Bird Island'}

CLAIM_DIR   = os.path.join(os.path.expanduser('~'), 'Documents', 'DragonCO2', 'auto_hunt')
CLAIM_EVERY = 2.0           # seconds between touches of a character's claim file while it tours
CLAIM_FRESH = 10.0          # static.py leaves the casting to the tour while the file is this fresh

_maps = {}                  # DMap path -> (width, height, walkable bytearray)
_claimed = {}               # character -> when its claim file was last touched
_last_cast = {}             # game pid -> when the steady Scatter was last cast


# ---------------------------------------------------------------- who casts

def claim(name):
    """Marks `name` as hunting with the tour (its own Scatter casts) - static.py, seeing it fresh,
    leaves the casting to it and keeps the rest (deposits, repair, revive...)."""
    if time.time() - _claimed.get(name, 0) < CLAIM_EVERY:
        return
    _claimed[name] = time.time()
    os.makedirs(CLAIM_DIR, exist_ok=True)
    with open(os.path.join(CLAIM_DIR, f'{name.lower()}.claim'), 'w') as f:
        f.write(str(os.getpid()))


def claimed(name):
    """True while a tour hunts with `name` (its claim file touched within CLAIM_FRESH seconds)."""
    try:
        return time.time() - os.path.getmtime(os.path.join(CLAIM_DIR, f'{name.lower()}.claim')) < CLAIM_FRESH
    except OSError:
        return False


# ---------------------------------------------------------------- the client's files

def game_root(pid):
    """The game's folder (the one holding ini/ and map/) - from the running client's path."""
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        raise OSError(f'cannot open game process {pid}')
    try:
        buf, size = ctypes.create_unicode_buffer(520), ctypes.wintypes.DWORD(520)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            raise OSError(f'cannot read the path of game process {pid}')
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    folder = os.path.dirname(buf.value)                  # ...\data2 (the client runs from there)
    return folder if os.path.isdir(os.path.join(folder, 'ini')) else os.path.dirname(folder)


def map_id(pid):
    """The id of the map this character is on, or None."""
    handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
    if not handle:
        return None
    try:
        game_map = game_memory._u32(handle, game_memory.GAME_MAP)
        return game_map and game_memory._u32(handle, game_map + MAP_ID)
    except OSError:
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def map_file(root, the_map):
    """The DMap file of map `the_map` - ini/GameMap.dat: count, then (id, path length, path, ?)."""
    data = open(os.path.join(root, 'ini', 'GameMap.dat'), 'rb').read()
    count, at = struct.unpack_from('<I', data)[0], 4
    for _ in range(count):
        found, length = struct.unpack_from('<II', data, at)
        path = data[at + 8:at + 8 + length].decode('latin1')
        at += 8 + length + 4
        if found == the_map:
            return os.path.join(root, os.path.splitext(path)[0] + '.DMap')
    raise LookupError(f'map {the_map} is not in GameMap.dat')


def load_map(path):
    """(width, height, walkable) of a DMap - walkable[y * width + x] is 1 where one can stand.
    DMap: version, data, puzzle file (260), width, height, then per row: width x (mask, terrain,
    altitude) shorts and a checksum dword; mask 0 = walkable."""
    if path not in _maps:
        data = open(path, 'rb').read()
        width, height = struct.unpack_from('<II', data, 268)
        walkable, at = bytearray(width * height), 276
        for y in range(height):
            masks = struct.unpack_from(f'<{width * 3}H', data, at)[0::3]
            walkable[y * width:(y + 1) * width] = bytes(1 if m == 0 else 0 for m in masks)
            at += width * 6 + 4
        _maps[path] = (width, height, walkable)
    return _maps[path]


def _decrypt(data, seed=0x2537):
    """The client's encrypted ini files (its CMyFile): byte i xor key[i % 128] (rand() % 256 after
    srand(seed)), then rotated right by i % 8."""
    state, key = seed, []
    for _ in range(128):
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        key.append(((state >> 16) & 0x7FFF) % 256)
    out = bytearray(len(data))
    for i, c in enumerate(data):
        b, r = key[i % 128] ^ c, i % 8
        out[i] = ((b << (8 - r)) | (b >> r)) & 0xFF
    return bytes(out)


def hunting_areas(root, the_map):
    """[(name, x, y, what it says)] of the hunting areas Path Finding lists for map `the_map`."""
    text = _decrypt(open(os.path.join(root, 'ini', 'MapDestination.dat'), 'rb').read()).decode('latin1')
    sections, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        head = re.match(r'^\[(.+)\]$', line)
        if head:
            current = sections.setdefault(head.group(1), {})
        elif current is not None and '=' in line and not line.startswith('//'):
            k, v = line.split('=', 1)
            current[k.strip().lower()] = v.strip().replace('~', ' ')
    groups = {(s['mapid'], s['id'].lstrip('0')) for s in sections.values()
              if 'id' in s and 'mapid' in s and AREA_GROUPS.search(s.get('title', ''))}
    out = []
    for name, s in sections.items():
        if (name.split('-')[0], s.get('parentid', '').lstrip('0')) in groups and \
                name.split('-')[0] == str(the_map) and 'posx' in s:
            out.append((s.get('title', name), int(s['posx']), int(s['posy']), s.get('mark', '')))
    return out


# ---------------------------------------------------------------- the way

def _clear(walkable, width, a, b):
    """True if every cell on the straight line from a to b is walkable."""
    (x0, y0), (x1, y1) = a, b
    steps = max(abs(x1 - x0), abs(y1 - y0))
    for i in range(1, steps + 1):
        x = x0 + round((x1 - x0) * i / steps)
        y = y0 + round((y1 - y0) * i / steps)
        if not walkable[y * width + x]:
            return False
    return True


def find_path(the_map, start, goal):
    """[cells] from start to goal through walkable cells (A*, 8 directions), or [] if there's no
    way. A goal that isn't walkable is replaced by the nearest walkable cell."""
    width, height, walkable = the_map
    if not walkable[goal[1] * width + goal[0]]:
        goal = _nearest_walkable(the_map, goal)
    came = {start: None}
    cost = {start: 0}
    todo = [(0, start)]
    while todo:
        _, cell = heapq.heappop(todo)
        if cell == goal:
            path = []
            while cell:
                path.append(cell)
                cell = came[cell]
            return path[::-1]
        x, y = cell
        for dx, dy in NEIGHBOURS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height) or not walkable[ny * width + nx]:
                continue
            step = cost[cell] + (14 if dx and dy else 10)
            if step < cost.get((nx, ny), 1 << 30):
                cost[(nx, ny)] = step
                came[(nx, ny)] = cell
                h = 10 * max(abs(goal[0] - nx), abs(goal[1] - ny))
                heapq.heappush(todo, (step + h, (nx, ny)))
    return []


def _nearest_walkable(the_map, cell):
    width, height, walkable = the_map
    for r in range(1, 50):
        for dx in range(-r, r + 1):
            for dy in (-r, r):
                for x, y in ((cell[0] + dx, cell[1] + dy), (cell[0] + dy, cell[1] + dx)):
                    if 0 <= x < width and 0 <= y < height and walkable[y * width + x]:
                        return x, y
    return cell


def _far(a, b):
    """The distance the game's Jump checks: straight-line (a 15 x 7 jump is refused, 16.6 cells)."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def jumps(the_map, path, reach=JUMP_CELLS):
    """The path cut into jumps: from each stop, the farthest cell of the path still within `reach`
    cells (straight-line) with a clear straight line to it."""
    width, _, walkable = the_map
    stops, i = [], 0
    while i < len(path) - 1:
        j = min(len(path) - 1, i + reach)
        while j > i + 1 and (_far(path[j], path[i]) > reach
                             or not _clear(walkable, width, path[i], path[j])):
            j -= 1
        stops.append(path[j])
        i = j
    return stops


# ---------------------------------------------------------------- going

def _wait_landed(pid):
    end = time.time() + 0.3
    while time.time() < end and game_actions.my_command(pid) not in CMD_IN_AIR:
        time.sleep(0.02)
    end = time.time() + JUMP_WAIT
    while time.time() < end and game_actions.my_command(pid) in CMD_IN_AIR:
        time.sleep(0.02)


def _scatter_ahead(pid, came_from):
    """A Scatter only where it pays - SCATTER_MIN or more monsters within SCATTER_REACH (each cast
    halves the jumping: the game takes no jump till its animation ends), at most one every
    SCATTER_EVERY seconds, SCATTER_CELLS ahead - the way the last jump went (from `came_from`);
    not moved: towards the monsters near. True if cast."""
    if time.time() - _last_cast.get(pid, 0) < SCATTER_EVERY:
        return False
    me = game_actions.my_pos(pid)
    if not me:
        return False
    near = [m for m in _monster_cells(pid, ()) if m[0] <= SCATTER_REACH]
    if len(near) < SCATTER_MIN:
        return False
    dx, dy = me[0] - came_from[0], me[1] - came_from[1]
    if not dx and not dy:                               # didn't move: towards the monsters
        dx = sum(m[3][0] for m in near) / len(near) - me[0]
        dy = sum(m[3][1] for m in near) / len(near) - me[1]
    length = math.hypot(dx, dy) or 1
    x = round(me[0] + dx / length * background_hunt.SCATTER_CELLS)
    y = round(me[1] + dy / length * background_hunt.SCATTER_CELLS)
    game_actions.cast_at(pid, background_hunt.SCATTER, x, y)
    _last_cast[pid] = time.time()
    time.sleep(CAST_SENT)                               # sent - its animation isn't waited for
    return True


def go(pid, name, x, y, stop=None, claiming=False):
    """Jumps the character to (x, y) of its map. A refused jump is tried again shorter; the way is
    worked out again from where it stands then. `stop`() True ends the trip early; `claiming`: a
    tour's trip (keeps its claim fresh, a Scatter ahead where 3+ monsters crowd). True if there."""
    root, the_map = game_root(pid), map_id(pid)
    grid = load_map(map_file(root, the_map))
    reach, refused, stops, loot_skip = JUMP_CELLS, 0, [], {}
    cast = claiming and background_hunt.SCATTER in game_actions.skills(pid)
    for _ in range(200):
        if claiming:
            claim(name)
        me = game_actions.my_pos(pid)
        if not me:
            return False
        if max(abs(me[0] - x), abs(me[1] - y)) <= ARRIVED:
            return True
        if stop and stop():
            return True
        if not stops:                                   # the way, worked out once - again only
            path = find_path(grid, me, (x, y))          # after a refused jump
            if not path:
                print(f'{name}: no way from {me} to {(x, y)} on map {the_map}')
                return False
            stops = jumps(grid, path, reach)
            if not stops:                               # already on the walkable cell nearest it
                return True
        target = stops.pop(0)
        game_actions.jump_to(pid, *target)
        _wait_landed(pid)
        now = game_actions.my_pos(pid)
        if now == me:                                   # not moved yet: a late start?
            time.sleep(REFUSED_WAIT)
            _wait_landed(pid)
            now = game_actions.my_pos(pid)
        if now == me:                                   # refused: a shorter one next, and that
            refused += 1                                # cell learnt as blocked (a tree, a rock -
            _block(grid, target)                        # scene objects the DMap's cells don't show)
            if refused >= GIVE_UP:
                print(f'{name}: {refused} jumps refused in a row near {me} - giving up on {(x, y)}')
                return False
            reach, stops = max(3, reach // 2), []
            print(f'{name}: the jump to {target} was refused - blocked it, trying {reach} cells')
        else:
            if reach != JUMP_CELLS:                     # past it: full jumps again
                stops = []
            reach, refused = JUMP_CELLS, 0
            if cast:                                    # a crowd on the way: a Scatter ahead
                _scatter_ahead(pid, me)
            if claiming and loot(pid, name, loot_skip, JUMP_CELLS):   # a + item on the way:
                stops = []                              # picked up - the way again from there
    return False


def loot(pid, name, skip, reach=LOOT_CELLS):
    """Picks up the nearest +1 or better item lying within `reach` cells: farther than a jump, the
    way there first (go), then the game's own pick-up (monk_hunt.collect). `skip`: {item id: until
    when} - ones that couldn't be picked up. True if there was one to go for."""
    import monk_hunt
    me = game_actions.my_pos(pid)
    if not me:
        return False
    now = time.time()
    items = sorted((i for i in game_memory.ground_items(pid) if monk_hunt._wanted(i[1], i[2])
                    and skip.get(i[0], 0) <= now and _far(me, i[3:]) <= reach),
                   key=lambda i: _far(me, i[3:]))
    if not items:
        return False
    item_id, kind, plus, x, y = items[0]
    gone = lambda: not any(i[0] == item_id for i in game_memory.ground_items(pid))
    if _far(me, (x, y)) > JUMP_CELLS:
        print(f'{name}: a +{plus} item lies {_far(me, (x, y)):.0f} cells away - going for it')
        go(pid, name, x, y, stop=lambda: gone() or _far(game_actions.my_pos(pid) or me, (x, y)) <= JUMP_CELLS)
    if monk_hunt.collect(pid, item_id, x, y):
        print(f'{name}: picked up item {kind} +{plus}')
    else:
        skip[item_id] = time.time() + monk_hunt.LOOT_SKIP
    return True


def _block(grid, cell):
    """Marks `cell` and the cells around it not walkable (in this session's copy of the map)."""
    width, height, walkable = grid
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            x, y = cell[0] + dx, cell[1] + dy
            if 0 <= x < width and 0 <= y < height:
                walkable[y * width + x] = 0


def monsters_near(pid):
    """[(distance, name, id)] of the monsters (not guards) within MONSTER_CELLS."""
    import monk_hunt
    me = game_actions.my_pos(pid)
    return [m for m in monk_hunt.monsters(pid, me, ()) if m[0] <= MONSTER_CELLS
            and not m[1].lower().startswith(SKIP)] if me else []


def to_monsters(pid, name):
    """Out to the nearest hunting area of the map (stops on the way as soon as monsters are near).
    True once there are monsters around."""
    if monsters_near(pid):
        return True
    root, the_map = game_root(pid), map_id(pid)
    me = game_actions.my_pos(pid)
    areas = sorted(hunting_areas(root, the_map), key=lambda a: max(abs(a[1] - me[0]), abs(a[2] - me[1])))
    if not areas:
        print(f'{name}: map {the_map} has no hunting areas listed')
        return False
    area, x, y, says = areas[0]
    print(f'{name}: going to {area} ({x}, {y}) - {says}')
    go(pid, name, x, y, stop=lambda: bool(monsters_near(pid)))
    found = monsters_near(pid)
    print(f'{name}: ' + (f'{len(found)} monster(s) around - {found[0][1]} nearest' if found
                         else 'at the area, no monsters in sight'))
    return bool(found)


def _monster_cells(pid, skip, center=None):
    """[(straight-line distance, name, id, (x, y))] of the monsters around (not guards, not the ids
    in `skip`; with `center`, only those within AREA_RADIUS of it), nearest first."""
    import monk_hunt
    me = game_actions.my_pos(pid)
    if not me:
        return []
    found = [m for m in monk_hunt.monsters(pid, me, skip) if not m[1].lower().startswith(SKIP)]
    where = game_actions.positions(pid, [m[2] for m in found])
    return sorted((_far(me, where[m[2]]), m[1], m[2], where[m[2]]) for m in found if m[2] in where
                  and (center is None or _far(center, where[m[2]]) <= AREA_RADIUS))


def _pick(found, landed, everyone):
    """The monster to jump to next, out of `found` (_monster_cells): guided by the crowds - the one
    within a jump (MIN_JUMP..JUMP_CELLS) with the most monsters (`everyone`) within SCATTER_REACH
    of it, the farther on a tie, and not by a spot it landed on lately (so it ranges around
    instead of circling one crowd). None such: any within a jump the same way, else the nearest."""
    reachable = [m for m in found if MIN_JUMP <= m[0] <= JUMP_CELLS]
    if not reachable:
        return found[0]
    fresh = [m for m in reachable if all(_far(m[3], spot) > LANDED_CELLS for spot in landed)]
    crowd = lambda m: sum(1 for o in everyone if _far(o[3], m[3]) <= SCATTER_REACH)
    return max(fresh or reachable, key=lambda m: (crowd(m), m[0]))


def bounce(pid, name, seconds, stop=None, wander=True, center=None, label=''):
    """Keeps jumping from monster to monster for `seconds` - always moving, towards the crowds: next
    to the monster _pick chooses, a Scatter ahead where SCATTER_MIN+ crowd (_scatter_ahead),
    picking up the + items that drop on the way. With none around, on to the map's next hunting
    area (wander), or - after EMPTY_SECONDS with none, for them to come back - done. With `center`,
    only that area's monsters (within AREA_RADIUS of it). Prints where it's at every STATUS_EVERY
    seconds. Returns how many jumps."""
    start = time.time()
    end, been, loot_skip, count, area, landed = start + seconds, {}, {}, 0, 0, []
    cast = background_hunt.SCATTER in game_actions.skills(pid)
    empty_since, told, label = None, 0.0, label or 'here'
    while time.time() < end and not (stop and stop()):
        claim(name)
        now = time.time()
        been = {i: t for i, t in been.items() if t > now}
        loot_skip = {i: t for i, t in loot_skip.items() if t > now}
        me = game_actions.my_pos(pid)
        if not me:
            return count
        if loot(pid, name, loot_skip):                  # a + item within LOOT_CELLS first
            continue
        everyone = _monster_cells(pid, (), center)
        found = [m for m in everyone if m[2] not in been] or everyone   # all just visited: again
        if now - told >= STATUS_EVERY:
            told = now
            target = found and _pick(found, landed, everyone)
            nxt = (f'next: jump to {target[1]} {target[0]:.0f} cells away' if found else
                   f'no monsters - waiting {EMPTY_SECONDS - (now - (empty_since or now)):.0f}s more for them'
                   if not wander else 'no monsters - on to the next area')
            print(f'{name}: [{label}] {now - start:.0f}s of {seconds:.0f}s, {count} jumps, '
                  f'{len(everyone)} monster(s) around - {nxt}')
        if not found and not wander:
            empty_since = empty_since or now
            if now - empty_since >= EMPTY_SECONDS:
                print(f'{name}: [{label}] no monsters for {EMPTY_SECONDS}s - leaving after '
                      f'{now - start:.0f}s of {seconds:.0f}s, {count} jumps')
                return count
            time.sleep(0.5)
            continue
        empty_since = None
        if not found:
            areas = hunting_areas(game_root(pid), map_id(pid))
            if not areas:
                time.sleep(0.5)
                continue
            area = (area + 1) % len(areas)
            title, x, y, _ = areas[area]
            print(f'{name}: no monsters here - on to {title} ({x}, {y})')
            go(pid, name, x, y, stop=lambda: bool(_monster_cells(pid, been)), claiming=True)
            continue
        distance, _, mob_id, (x, y) = _pick(found, landed, everyone)
        if distance <= JUMP_CELLS:
            game_actions.jump_next_to_id(pid, mob_id)
            _wait_landed(pid)
            landed = (landed + [game_actions.my_pos(pid) or me])[-LANDINGS:]
            if cast:
                _scatter_ahead(pid, me)
        else:
            go(pid, name, x, y, stop=lambda: any(m[0] <= JUMP_CELLS for m in _monster_cells(pid, been, center)),
               claiming=True)
        been[mob_id] = time.time() + BEEN_SECONDS
        count += 1
    return count


def teleport(pid, name, code):
    """VIP > Remote Travel to the city with this code (TELEPORTS). True once the map changed."""
    before = map_id(pid)
    game_actions.vip_command(pid, code)
    end = time.time() + TELEPORT_WAIT
    while time.time() < end:
        time.sleep(0.5)
        now = map_id(pid)
        if now and now != before and game_actions.my_pos(pid):
            time.sleep(1)                              # the new map loading
            return True
    print(f'{name}: the teleport ({code}) did not happen - no VIP?')
    return False


def explore(pid, name, seconds, stop=None):
    """A map with no hunting areas listed (Bird Island): jumps to random walkable spots
    EXPLORE_NEAR..EXPLORE_FAR cells away until monsters show up, jumps among them till they're
    gone, then on exploring - for `seconds`. Returns how many jumps among monsters."""
    grid = load_map(map_file(game_root(pid), map_id(pid)))
    width, height, walkable = grid
    end, count = time.time() + seconds, 0
    while time.time() < end and not (stop and stop()):
        if _monster_cells(pid, ()):
            count += bounce(pid, name, end - time.time(), stop, wander=False, label='exploring')
            continue
        me = game_actions.my_pos(pid)
        if not me:
            return count
        for _ in range(200):                            # a walkable spot at the right distance
            angle = random.uniform(0, 6.283)
            far = random.uniform(EXPLORE_NEAR, EXPLORE_FAR)
            x, y = int(me[0] + far * math.cos(angle)), int(me[1] + far * math.sin(angle))
            if 0 <= x < width and 0 <= y < height and walkable[y * width + x]:
                break
        else:
            return count
        print(f'{name}: no monsters in sight - exploring to ({x}, {y})')
        go(pid, name, x, y, stop=lambda: bool(_monster_cells(pid, ())) or time.time() > end,
           claiming=True)
    return count


def _route(areas, start):
    """The areas in visiting order: always the nearest one not visited yet."""
    left, at, out = list(areas), start, []
    while left:
        nxt = min(left, key=lambda a: _far(at, a[1:3]))
        left.remove(nxt)
        out.append(nxt)
        at = nxt[1:3]
    return out


def tour(pid, name, seconds=AREA_SECONDS, stop=None):
    """For good (or until `stop`()): every hunting area of the map in turn, nearest first -
    `seconds` of jumping among each one's monsters (less if it runs out of them) - then a teleport
    to the next city, round and round TELEPORTS. A map with no areas listed: a minute around."""
    codes = [c for c, _ in TELEPORTS]
    here = next((i for i, (_, m) in enumerate(TELEPORTS) if m == map_id(pid)), -1)
    while not (stop and stop()):
        city, me = map_id(pid), game_actions.my_pos(pid)
        areas = _route(hunting_areas(game_root(pid), city), me) if me else []
        print(f'{name}: map {city} - {len(areas)} hunting area(s)')
        count = 0
        for title, x, y, says in areas:
            if stop and stop():
                return
            print(f'{name}: going to {title} ({x}, {y}) - {says}')
            near = lambda: (_far(game_actions.my_pos(pid) or (0, 0), (x, y)) <= AREA_RADIUS
                            and bool(_monster_cells(pid, (), (x, y))))
            if not go(pid, name, x, y, stop=near, claiming=True):
                print(f'{name}: could not get to {title}')
                continue
            jumped = bounce(pid, name, seconds, stop, wander=False, center=(x, y), label=title)
            print(f'{name}: {jumped} jumps at {title}')
            count += jumped
        if not areas:
            count += explore(pid, name, TOUR_SECONDS, stop)
        print(f'{name}: {count} jumps on map {city}')
        here = (here + 1) % len(codes)
        print(f'{name}: teleport to {TELEPORT_NAMES[codes[here]]}')
        teleport(pid, name, codes[here])


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    if args[0] not in accounts:
        sys.exit(f'{args[0]} is not logged in')
    pid, name = accounts[args[0]], args[0]
    if '--tour' in args:
        tour(pid, name)
    elif '--areas' in args:
        for area in hunting_areas(game_root(pid), map_id(pid)):
            print(area)
    elif len(args) == 3:
        print(f'{name}: ' + ('there' if go(pid, name, int(args[1]), int(args[2])) else 'did not get there'))
    else:
        to_monsters(pid, name)


if __name__ == '__main__':
    main()
