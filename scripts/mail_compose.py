"""Moves the VIP compose warehouse's items to another character by mail, in the background
(injection): takes the best items out (highest plus first, then the most of them) into the bag -
VIP > Compose > Compose Warehouse > Withdraw - and mails them, MAIL_ITEMS_MAX a mail, to the
character - Mail > Write: Search the name, the items in the boxes, Send. Until the warehouse is empty.
(game_actions.py compose_withdraw / mail_search / mail_send, game_memory.py compose_items.)
    python mail_compose.py <from character> --send <to character>   everything
    python mail_compose.py <from character> <to character>          the same
    python mail_compose.py <from character> <to character> --once   one mail only
    python mail_compose.py <character> --receive    Claim All, Deposit, every mail marked read"""
import sys
import time

import game_actions
import game_memory

WAIT = 4.0                      # seconds for the server to answer a list / withdraw / search / send


def _wait(check, seconds=WAIT):
    end = time.time() + seconds
    while time.time() < end:
        found = check()
        if found:
            return found
        time.sleep(0.2)
    return None


def _bag(pid):
    """Ids of the bag's + items, once the bag reads fine (it may not while items are arriving)."""
    end = time.time() + WAIT
    while True:
        items = game_memory.bag_plus_ids(pid)
        if items is not None:
            return {i[0] for i in items}
        if time.time() > end:
            raise OSError('cannot read the bag')
        time.sleep(0.2)


def _receiver(pid, name):
    """The id of the character `name`: read from its game if it's logged in here, else the mail's
    Search (answered only while the mail window is open). None if not found."""
    for other in game_memory.game_pids():
        if (game_memory._name_pid(other) or '').lower() == name.lower():
            found = game_memory.hero_id(other)
            if found:
                return found
    game_actions.mail_search(pid, name)
    found = _wait(lambda: (game_memory.mail_receiver(pid) or (0, ''))[1].lower() == name.lower()
                  and game_memory.mail_receiver(pid))
    return found and found[0]


def _withdraw(pid, name):
    """Takes the best items out of the compose warehouse into the bag, MAIL_ITEMS_MAX at most.
    Returns the bag ids of the ones that came out ([] when the warehouse is empty or refused)."""
    game_actions.compose_list(pid)
    time.sleep(1)
    items = sorted(game_memory.compose_items(pid), key=lambda i: (-i[2], -i[3]))
    before = _bag(pid)
    room = game_actions.MAIL_ITEMS_MAX
    for item_type, item_name, plus, count in items:
        if not room:
            break
        amount = min(count, room)
        game_actions.compose_withdraw(pid, item_type, amount, plus)
        print(f'{name}: withdraw {amount} x {item_name} +{plus}')
        room -= amount
        time.sleep(0.5)
    wanted = game_actions.MAIL_ITEMS_MAX - room
    if not wanted:
        return []
    _wait(lambda: len(_bag(pid) - before) >= wanted)
    return sorted(_bag(pid) - before)[:game_actions.MAIL_ITEMS_MAX]


def transfer(pid, name, to, once=False):
    """Mails the compose warehouse's items from `name` to the character `to`. Returns how many went."""
    to_id = _receiver(pid, to)
    if not to_id:
        here = [game_memory._name_pid(p) for p in game_memory.game_pids()]
        print(f'{name}: {to} is not among the games read here {here}, and the mail Search did not find it')
        return 0
    sent = 0
    while True:
        ids = _withdraw(pid, name)
        if not ids:
            print(f'{name}: nothing (more) came out of the compose warehouse')
            break
        game_actions.mail_send(pid, to_id, ids)
        if _wait(lambda: not _bag(pid) & set(ids)) is None:
            print(f'{name}: the mail to {to} was not taken - the items are still in the bag')
            break
        sent += len(ids)
        print(f'{name}: mailed {len(ids)} item(s) to {to}')
        if once:
            break
    return sent


def receive(pid, name):
    """The receiving side: Claim All in the mail, then the + items that came into the bag go to the
    compose warehouse (Deposit), and every unread mail is marked read (as if clicked - no red dots
    left). Returns how many items came."""
    before = _bag(pid)
    game_actions.mail_claim_all(pid)
    _wait(lambda: _bag(pid) - before)
    time.sleep(1)                       # the rest of them
    came = len(_bag(pid) - before)
    if came:
        print(f'{name}: {came} + item(s) claimed from the mail')
        game_actions.compose_deposit(pid)
        print(f'{name}: deposited to the compose warehouse')
    mark_read(pid, name)
    return came


def unread(pid):
    """Ids of the unread mails in this character's mailbox (as last loaded)."""
    return [m[0] for m in game_memory.mails(pid) if m[2]]


def mark_read(pid, name):
    """Every unread mail marked read - each clicked, as it were. Returns how many."""
    game_actions.mail_list(pid)
    time.sleep(1)
    ids = unread(pid)
    for mail_id in ids:
        game_actions.mail_read(pid, mail_id)
        time.sleep(0.1)
    if ids:
        print(f'{name}: {len(ids)} mail(s) marked read')
    return len(ids)


def main():
    args = [a for a in sys.argv[1:] if a not in ('--once', '--receive', '--send')]
    accounts = {game_memory._name_pid(p): p for p in game_memory.game_pids()}
    if args and args[0] not in accounts:
        sys.exit(f'{args[0]} is not logged in')
    if '--receive' in sys.argv and len(args) == 1:
        receive(accounts[args[0]], args[0])
        return
    if len(args) != 2:
        sys.exit(__doc__)
    sent = transfer(accounts[args[0]], args[0], args[1], once='--once' in sys.argv)
    print(f'{sent} item(s) mailed to {args[1]}')


if __name__ == '__main__':
    main()
