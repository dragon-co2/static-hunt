"""Hunts on the move, in the background (injection): every hunting area of the map in turn,
jumping from monster to monster with a Scatter at each and picking up the + items, then a VIP
teleport to the next city (travel.py tour). While it runs, static.py leaves the casting to it and
keeps the rest (deposits, repair, revive, arrows...). A window lists the logged-in accounts: the
ticked ones hunt, each on its own; unticking or closing it stops.
    python auto_hunt.py"""
import time

import game_memory
import monk_hunt
import travel


def _hunt(pid, name):
    """One character's tour, on a thread of its own - until it's unticked or its game closes."""
    print(f'{name}: auto hunt')
    monk_hunt._status[name] = 'touring'
    while monk_hunt.ticked(name) and game_memory._name_pid(pid) == name:
        try:
            travel.tour(pid, name, stop=lambda: not monk_hunt.ticked(name))
        except (OSError, TimeoutError, LookupError) as e:
            print(f'{name}: {type(e).__name__}: {e}')
            monk_hunt._status[name] = f'{type(e).__name__} - trying again'
            time.sleep(2)
    travel._where.pop(name, None)                       # its place no longer kept from the others
    monk_hunt._status[name] = 'stopped'
    print(f'{name}: auto hunt stopped')


def main():
    print('Auto hunt: tick the accounts to hunt with in the window - closing it stops them all')
    monk_hunt._window(_hunt, 'Auto hunt', 'Ticked accounts tour the hunting areas and cities',
                      held=travel.HELD)          # Stop / Resume a row
    print('window closed - stopped')


if __name__ == '__main__':
    main()
