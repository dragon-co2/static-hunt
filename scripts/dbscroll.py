"""Exchanges every DBScroll in the bag for CPs at the Exchange~DB NPC, one after another - the
same as clicking the NPC, "Exchange DBScroll (2150 CPs)" and "Yes, continue" (game_actions.py
talk_to_npc / npc_answer). Stand next to Exchange~DB first.
It keeps running until Ctrl+C: new DBScrolls, an account walking up to the NPC, an account that
logs in later - all picked up.
    python dbscroll.py                every logged-in account, all at once
    python dbscroll.py <character>    only that one"""
import ctypes
import sys
import threading
import time

import game_actions
import game_memory

NPC        = 'Exchange~DB'
DBSCROLL   = 720028        # item type of a DBScroll
OPTION     = 2             # "Exchange DBScroll (2150 CPs)" (numbers given by the server)
YES        = 20            # "Yes, continue"
STEP_DELAY = 0.5           # seconds between the clicks - wait for the server's dialog
WAIT       = 3.0           # seconds to wait for the scroll to leave the bag before giving up
CHECK_EVERY = 2.0          # seconds between looks at an account that is waiting (no NPC / scrolls)
RETRY_AFTER = 5.0          # seconds to wait after an exchange that did not go through


def scrolls(pid):
    """How many DBScrolls are in this character's bag."""
    handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
    try:
        hero = game_memory._u32(handle, game_memory.HERO_PTR)
        return sum(1 for item in game_memory._bag(handle, hero)
                   if item and game_memory._u32(handle, item) == game_memory.ITEM_VTABLE
                   and game_memory._u32(handle, item + game_memory.ITEM_TYPE) == DBSCROLL)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def exchange_one(pid, npc_id):
    """One DBScroll -> CPs. True if the scroll left the bag."""
    before = scrolls(pid)
    game_actions.talk_to_npc(pid, npc_id)
    time.sleep(STEP_DELAY)
    game_actions.npc_answer(pid, OPTION)
    time.sleep(STEP_DELAY)
    game_actions.npc_answer(pid, YES)
    end = time.time() + WAIT
    while time.time() < end:
        if scrolls(pid) < before:
            return True
        time.sleep(0.1)
    return False


def _state(pid):
    """(npc id or None, DBScrolls in the bag) of this account right now."""
    around = game_actions.players(pid)
    return (game_actions.role_id(pid, around[NPC]) if NPC in around else None), scrolls(pid)


def watch(pid, name):
    """Runs until the game closes: whenever this account is next to the NPC with DBScrolls in its
    bag, exchanges them. Says only what changed."""
    said = None
    while True:
        try:
            npc_id, count = _state(pid)
        except (OSError, ValueError):
            if not game_memory._name_pid(pid):
                print(f'{name}: the game closed / logged out - not watching it anymore')
                return
            time.sleep(CHECK_EVERY)
            continue
        if npc_id is None or not count:
            news = f'{NPC} is not around' if npc_id is None else 'no DBScrolls in the bag'
            if news != said:
                print(f'{name}: {news} - waiting')
                said = news
            time.sleep(CHECK_EVERY)
            continue
        said = None
        if exchange_one(pid, npc_id):
            print(f'{name}: 1 DBScroll exchanged, {game_memory.vitals_pid(pid)["cp"]:,} CPs, '
                  f'{count - 1} left')
        else:
            print(f'{name}: the scroll did not leave the bag - trying again in {RETRY_AFTER}s')
            time.sleep(RETRY_AFTER)


def main():
    """python dbscroll.py              every account that is logged in (and any that logs in later)
       python dbscroll.py <character>  only that one"""
    only = sys.argv[1] if len(sys.argv) > 1 else None
    watching = {}
    print('Exchanging DBScrolls - Ctrl+C to stop')
    try:
        while True:
            for pid in game_memory.game_pids():
                name = game_memory._name_pid(pid)
                if name and (only in (None, name)) and not (pid in watching and watching[pid].is_alive()):
                    watching[pid] = threading.Thread(target=watch, args=(pid, name), daemon=True)
                    watching[pid].start()
            time.sleep(CHECK_EVERY)
    except KeyboardInterrupt:
        print('stopped')


if __name__ == '__main__':
    main()
