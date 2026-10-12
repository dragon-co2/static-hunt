"""Watches the game's chat for "<our character> was killed by <killer>" and remembers who did it.

The chat is read live from the game's memory by chat_reader.ps1 (read-only: ReadProcessMemory,
never writes to the game). It runs as a child process for the whole run and prints one JSON
line per chat record; a background thread here parses them. The kill messages come on the
[System] channel in a few shapes:
    [System]Victim was killed by Killer!
    [System]Victim has been killed by Killer!
    [System]Killer has killed Victim.
(and "You were killed by Killer", meaning the character of the game it came from). Lines a
player typed ("... speaks to ...") never count, so nobody can fake a kill from the chat.
Every other kill in the server is logged as [CHAT] too (proof the chat is being read), and any
game line with "kill" in it that doesn't match a known shape is logged as it is, so a new
message shape shows up in the log. (A death to a monster has no message at all.)"""
import base64
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
DEDUPE_FOR   = 120   # seconds: every open game prints the same [System] line — count it once

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
        if 'kill' in low and not low.startswith('[gm]') and not _PLAYER_TEXT.search(text):
            print(f'[CHAT] {text.strip()}')   # a kill-ish game line of a shape not known yet
        return
    victim, killer = kill
    ours = _names.get(victim.lower())
    if not ours:
        print(f'[CHAT] {victim} was killed by {killer} (not ours)')   # proves the chat is being read
        return
    with _lock:
        _pending.setdefault(victim.lower(), []).append(killer)
    print(f'[KILL] {ours[0]} (desktop {ours[1]}) was killed by {killer} — handled on the next visit to desktop {ours[1]}')


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


def start(characters):
    """Starts watching (once) for kills of our characters: {character name: desktop number}."""
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


def take(name):
    """Kills of character `name` since the last call, oldest first, as
    [(killer, same_killer_as_their_previous_death), ...]."""
    key = (name or '').strip().lower()
    with _lock:
        killers = _pending.pop(key, [])
        out = []
        for killer in killers:
            out.append((killer, _last_killer.get(key, '').lower() == killer.lower()))
            _last_killer[key] = killer
    return out
