"""Watches the game's chat for "<our character> was killed by <killer>" and remembers who did it.

Two read-only sources, both needing the game's chat log option on (see ensure_chat_log):
  1. Live: chat_reader.ps1 reads the game's not-yet-saved chat records from its memory
     (ReadProcessMemory, never writes to the game); it runs as a child process for the whole
     run and prints one JSON line per record.
  2. The saved files: every ~31 messages the game moves its records to
     log/<server>/<id>/CHAT/ChatLog.log and clears them from memory. A message that arrives
     just as that happens is never seen live — so the new records in those files are read too.
The same line from both sources (or from several game windows) counts once.

The kill messages come on the [System] channel in a few shapes:
    [System]Victim was killed by Killer!
    [System]Victim has been killed by Killer!
    [System]Killer has killed Victim.
(and "You were killed by Killer", meaning the character of the game it came from). Lines a
player typed ("... speaks to ...") never count, so nobody can fake a kill from the chat.
Every other kill in the server is logged as [CHAT] too (proof the chat is being read), and any
game line with "kill" in it that doesn't match a known shape is logged as it is, so a new
message shape shows up in the log. (A death to a monster has no message at all.)"""
import base64
import fnmatch
import json
import os
import re
import subprocess
import threading
import time

from _paths import app_root

READER = next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_reader.ps1'),
                           os.path.join(app_root(), 'scripts', 'chat_reader.ps1')) if os.path.exists(p)),
              'chat_reader.ps1')
RESTART_WAIT = 5     # seconds before restarting the reader if it stops
DEDUPE_FOR   = 3600  # seconds a line counts as already seen: every game window shows the same
                     # [System] line, and the saved file repeats it minutes later. Lines carry
                     # their minute, so a real second kill is never mistaken for a repeat
FILE_POLL    = 1     # seconds between checks of the saved chat files

# ChatLog.log record (Conquer.exe ChatLog.cpp): 0x260 bytes = sender[16] | target[16] |
# date[64] | message[512]. Message byte i is XORed with
#   name[i % (len(name) - 1)] + server[i % (len(server) - 1)] + (0x5BAACFB // (i + 1)) mod 256
# where name is '' or the character's name and server is the log/<server> folder name.
_RECORD, _MSG_AT, _MSG_LEN = 0x260, 0x60, 0x200
_XOR_TAIL = bytes((0x5BAACFB // (i + 1)) & 0xFF for i in range(_MSG_LEN))

_NAME = r'(?P<{}>[^\s\[\]]+?)'
_END  = r'[!.]*\s*(?:\[\d{4}-|$)'   # the line ends with "!" / "." and the "[2026-10-10, 23:24]" time
_TAG  = r'^\[[A-Za-z]+\]'             # the channel, e.g. [System]
_PATTERNS = [
    re.compile(_TAG + _NAME.format('victim') + r' (?:was|were|has been|have been) killed by '
               + _NAME.format('killer') + _END, re.I),
    re.compile(_TAG + _NAME.format('killer') + r' (?:has killed|killed) ' + _NAME.format('victim') + _END, re.I),
]
_PLAYER_TEXT = re.compile(r' speaks to |^\[(?:Talk|World|Whisper|Team|Guild|Friend|Broadcast|Service)\]', re.I)

_lock = threading.Lock()
_names = {}         # our characters: name (lower) -> (name as set, desktop number)
_pending = {}        # victim (lower) -> [killer, ...] not yet handled by the main loop
_last_killer = {}    # victim (lower) -> killer of their previous death
_seen_streak = {}    # victim (lower) -> (killer lower, kills in a row) as seen in the chat
on_kill = None       # optional listener: on_kill(character, desktop, killer, streak), called at once
monsters = []        # killer names that are monsters (set by static.py; * = any characters)


def is_monster(name):
    low = (name or '').lower()
    return any(fnmatch.fnmatchcase(low, m.lower()) for m in monsters)
_recent = {}         # chat text -> time.time() first seen (dedupe across game windows)
_started = False
_job = None          # Windows job object: the reader dies with this script, even on a crash


def _tie_to_us(proc):
    """Puts the reader in a job that Windows closes when this process ends — so a reader is
    never left running after the script stops (or is killed / crashes)."""
    global _job
    try:
        import win32api
        import win32con
        import win32job
        if _job is None:
            _job = win32job.CreateJobObject(None, '')
            info = win32job.QueryInformationJobObject(_job, win32job.JobObjectExtendedLimitInformation)
            info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(_job, win32job.JobObjectExtendedLimitInformation, info)
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
        win32job.AssignProcessToJobObject(_job, handle)
    except Exception as e:
        print(f'[KILL] could not tie the chat reader to the script ({type(e).__name__}: {e})')


def parse_kill(text):
    """(victim, killer) if `text` is a game kill message, else None. A victim of "You" is
    returned as "You" — the caller knows whose game it came from."""
    if _PLAYER_TEXT.search(text):
        return None
    for pat in _PATTERNS:
        m = pat.search(text.strip())
        if m:
            return m.group('victim'), m.group('killer')
    return None


def _on_text(text, character=''):
    """One chat line from the game window of `character` (its in-game name, '' if unknown)."""
    kill = parse_kill(text)
    if kill and kill[0].lower() == 'you':          # "You were killed by X": whose game said it
        kill = (character, kill[1]) if character else None
    now = time.time()
    with _lock:
        key = f'{kill[0].lower()}>{kill[1].lower()}>{text[-20:]}' if kill else text
        if key in _recent:
            return
        for t in [t for t, at in _recent.items() if now - at > DEDUPE_FOR]:
            del _recent[t]
        _recent[key] = now
    low = text.lower()
    if not kill:
        if ('kill' in low and not low.startswith('[gm]') and 'killed a monster' not in low
                and not _PLAYER_TEXT.search(text)):
            print(f'[CHAT] {text.strip()}')   # a kill-ish game line of a shape not known yet
        return
    victim, killer = kill
    ours = _names.get(victim.lower())
    if not ours:
        print(f'[CHAT] {victim} was killed by {killer} (not ours)')   # proves the chat is being read
        return
    if is_monster(killer):   # no streak, no listener (no notification) — just revive at once
        with _lock:
            _pending.setdefault(victim.lower(), []).append(killer)
        print(f'[KILL] {ours[0]} (desktop {ours[1]}) was killed by the monster {killer} — '
              'revive right away on the next visit, no message')
        return
    with _lock:
        _pending.setdefault(victim.lower(), []).append(killer)
        last, n = _seen_streak.get(victim.lower(), ('', 0))
        streak = n + 1 if last == killer.lower() else 1
        _seen_streak[victim.lower()] = (killer.lower(), streak)
    print(f'[KILL] {ours[0]} (desktop {ours[1]}) was killed by {killer} — handled on the next visit to desktop {ours[1]}')
    if on_kill:
        try:
            on_kill(ours[0], ours[1], killer, streak)
        except Exception as e:   # a listener problem must never stop the chat reading
            print(f'[KILL] kill listener failed ({type(e).__name__}: {e})')


def _read_loop():
    while True:
        try:
            proc = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', READER],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            print(f'[KILL] could not start the chat reader ({e})')
            time.sleep(RESTART_WAIT * 6)
            continue
        _tie_to_us(proc)
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if 'status' in msg:
                print(f'[KILL] chat reader (game pid {msg.get("pid")}): {msg["status"]}')
            elif 'text' in msg:
                character = base64.b64decode(msg.get('character') or '').decode('cp1252', 'replace')
                _on_text(base64.b64decode(msg['text']).decode('cp1252', 'replace'), character)
        print(f'[KILL] chat reader stopped (exit {proc.wait()}) — restarting in {RESTART_WAIT}s')
        time.sleep(RESTART_WAIT)


def _xor_message(enc, name, server):
    nb = name.encode('latin-1', 'replace')[:16].ljust(16, b'\0')
    nmod = max(len(name.encode('latin-1', 'replace')), 2) - 1
    sb = server.encode('latin-1', 'replace') or b'\0'
    smod = max(len(sb) - 1, 1)
    return bytes(c ^ ((nb[i % nmod] + sb[i % smod] + _XOR_TAIL[i]) & 0xFF) for i, c in enumerate(enc))


def _clean(plain):
    """A right key leaves the text, a 0 terminator and only zero padding after it."""
    end = plain.find(0)
    return end > 0 and not any(plain[end:])


def _character_of(folder):
    """Character name of a log/<server>/<id> folder, from the [<Name>Record] in its Setup.ini."""
    try:
        with open(os.path.join(folder, 'Setup.ini'), encoding='latin-1') as f:
            m = re.search(r'(?m)^\[(.+?)Record\]', f.read())
            return m.group(1) if m else ''
    except OSError:
        return ''


def _chat_files(root):
    """{path of each ChatLog.log: (server, folder)} under the game's log folder."""
    out = {}
    for server in os.listdir(root) if os.path.isdir(root) else []:
        server_dir = os.path.join(root, server)
        for cid in os.listdir(server_dir) if os.path.isdir(server_dir) else []:
            path = os.path.join(server_dir, cid, 'CHAT', 'ChatLog.log')
            if os.path.isfile(path):
                out[path] = (server, os.path.join(server_dir, cid))
    return out


def _file_loop(game_path):
    """Reads the records added to each saved chat file since it was last looked at."""
    root = os.path.join(os.path.dirname(game_path), 'log')
    seen = {}      # path -> bytes already read (whole records)
    keys = {}      # path -> the name that decodes it ('' or the character)
    first = True   # files there at start: skip their history, only read what's added from now
    while True:
        try:
            for path, (server, folder) in _chat_files(root).items():
                size = os.path.getsize(path) // _RECORD * _RECORD
                if path not in seen:
                    seen[path] = size if first else 0
                if size < seen[path]:          # file started over
                    seen[path] = 0
                if size == seen[path]:
                    continue
                with open(path, 'rb') as f:
                    f.seek(seen[path])
                    data = f.read(size - seen[path])
                seen[path] = size
                character = _character_of(folder)
                for off in range(0, len(data) - _RECORD + 1, _RECORD):
                    enc = data[off + _MSG_AT: off + _MSG_AT + _MSG_LEN]
                    for name in [n for n in (keys.get(path), '', character) if n is not None]:
                        plain = _xor_message(enc, name, server)
                        if _clean(plain):
                            keys[path] = name
                            _on_text(plain[:plain.find(0)].decode('cp1252', 'replace'), character)
                            break
        except OSError as e:
            print(f'[KILL] could not read the chat files ({e})')
        first = False
        time.sleep(FILE_POLL)


def ensure_chat_log(game_path):
    """The game only keeps its chat (where the kill messages are read from) when its "save chat
    log" option is on: LogRecord=1 in each character's log/<server>/<id>/Setup.ini. With it off
    (LogRecord=0) nothing is ever seen. Turns it on for every character; the game reads it at
    login, so a game that is already logged in picks it up after its next login."""
    root = os.path.join(os.path.dirname(game_path), 'log') if game_path else ''
    if not os.path.isdir(root):
        return 0
    changed = 0
    for folder, _dirs, files in os.walk(root):
        if 'Setup.ini' not in files:
            continue
        path = os.path.join(folder, 'Setup.ini')
        try:
            with open(path, 'r', encoding='latin-1', newline='') as f:
                text = f.read()
            fixed = re.sub(r'(?m)^LogRecord=0(?=\r?$)', 'LogRecord=1', text)
            if fixed != text:
                with open(path, 'w', encoding='latin-1', newline='') as f:
                    f.write(fixed)
                changed += 1
        except OSError as e:
            print(f'[KILL] could not turn on the chat log in {path} ({e})')
    if changed:
        print(f"[KILL] turned on the game's chat log for {changed} character(s) — "
              'a game that is already in takes it after its next login')
    return changed


def start(characters, game_path=''):
    """Starts watching (once) for kills of our characters: {character name: desktop number}.
    `game_path` (the launcher) locates the saved chat files next to it."""
    global _started
    with _lock:
        for name, desktop in characters.items():
            if name and name.strip():
                _names[name.strip().lower()] = (name.strip(), desktop)
    if _started or not _names:
        return
    _started = True
    print('[KILL] watching the chat for kills of: '
          + ', '.join(f'{n} (desktop {d})' for n, d in sorted(_names.values(), key=lambda v: v[1])))
    threading.Thread(target=_read_loop, daemon=True).start()
    if game_path:
        threading.Thread(target=_file_loop, args=(game_path,), daemon=True).start()


def take(name):
    """Kills of character `name` since the last call, oldest first, as
    [(killer, same_killer_as_their_previous_player_death, is_monster), ...]. A monster's kill
    doesn't count as a "previous death": a player killing again after it is still a repeat."""
    key = (name or '').strip().lower()
    with _lock:
        killers = _pending.pop(key, [])
        out = []
        for killer in killers:
            if is_monster(killer):
                out.append((killer, False, True))
                continue
            out.append((killer, _last_killer.get(key, '').lower() == killer.lower(), False))
            _last_killer[key] = killer
    return out
