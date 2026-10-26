"""Counts, for each character and day, how it hunted - with auto_hunt.py (its claim is fresh:
travel.claimed) or with static.py alone - and what that gave: the time spent, the monsters that
died around it, and the +1 items / dragonballs the game said it dropped ("You killed a monster
and it dropped a +1 X"). static.py runs the counting (watch, on_drop); this script draws it:
    python hunt_stats.py            the report (daily charts, auto hunt vs static) in the browser

Monsters: every one seen taking its killing blow in the character's view (read-only memory, once
per id) - the character's Scatters kill nearly all of them, but one another player kills right
there counts too. Not counted: time dead, in the Market, or away from the game.
Kept in Documents/DragonCO2/hunt_stats.json: {day: {character: {mode: {seconds, kills, plus,
dragonballs}}}}."""
import datetime
import html
import json
import os
import re
import threading
import time

import game_actions
import game_memory
import travel

PATH       = os.path.join(os.path.expanduser('~'), 'Documents', 'DragonCO2', 'hunt_stats.json')
REPORT     = os.path.join(os.path.expanduser('~'), 'Documents', 'DragonCO2', 'hunt_stats.html')
POLL       = 0.3            # seconds between looks at the monsters around
SAVE_EVERY = 60             # seconds between writes of the file
KILL_KEEP  = 60             # seconds a dead monster's id is remembered (counted once)
DAYS       = 14             # days in the report
MODES      = ('auto', 'static')
MODE_NAMES = {'auto': 'Auto hunt', 'static': 'Static'}
MONSTER_IDS = range(400000, 500000)
DROP_SAID  = re.compile(r'You killed a monster and it dropped an? (?P<item>\+\d+ [^.\[]+?|DragonBall)\.?\s*'
                        r'(?:\[[\d\-, :]*\])?\s*$', re.I)

_lock = threading.Lock()
_data = None                # the file's contents, once read
_mode = {}                  # character -> the mode it's hunting in now (None: not hunting)
_watched = set()            # game pids with a counting thread
_saver = False


def _load():
    global _data
    if _data is None:
        try:
            with open(PATH, encoding='utf-8') as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
    return _data


def _save():
    with _lock:
        text = json.dumps(_load(), indent=1)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH + '.tmp', 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(PATH + '.tmp', PATH)


def _add(name, mode, field, amount=1):
    day = datetime.date.today().isoformat()
    with _lock:
        row = _load().setdefault(day, {}).setdefault(name, {}).setdefault(mode, {})
        row[field] = row.get(field, 0) + amount


def _hunting(pid, name):
    """'auto' / 'static', or None while it isn't hunting (dead, in the Market)."""
    if game_actions.i_am_dead(pid) or travel.map_id(pid) in travel.NO_SCATTER_MAPS:
        return None
    return 'auto' if travel.claimed(name) else 'static'


def _dying(pid):
    """Ids of the monsters around that took their killing blow (one read of the game)."""
    found = game_actions.roles(pid)
    handle = game_memory._kernel32.OpenProcess(0x0410, False, pid)
    if not handle:
        return set()
    try:
        out = set()
        for _, role in found:
            try:
                mob_id = game_memory._u32(handle, role + game_actions.HERO_ID)
                if mob_id in MONSTER_IDS and game_memory._u32(handle, role + game_actions.MONSTER_DYING):
                    out.add(mob_id)
            except OSError:                             # freed while being read
                continue
        return out
    finally:
        game_memory._kernel32.CloseHandle(handle)


def watch(pid, name):
    """Starts counting for this character's game (once per game) - until it closes."""
    global _saver
    with _lock:
        if pid in _watched or not name:
            return
        _watched.add(pid)
        if not _saver:
            _saver = True
            threading.Thread(target=_save_loop, daemon=True, name='hunt-stats-save').start()
    threading.Thread(target=_count, args=(pid, name), daemon=True, name=f'hunt-stats-{name}').start()


def _count(pid, name):
    seen, last = {}, time.time()
    try:
        while game_memory._name_pid(pid) == name:
            now = time.time()
            try:
                mode = _hunting(pid, name)
                _mode[name] = mode
                if mode:
                    _add(name, mode, 'seconds', now - last)
                    for mob_id in _dying(pid) - set(seen):
                        _add(name, mode, 'kills')
                        seen[mob_id] = now
            except (OSError, ValueError) as e:
                print(f'[STATS] {name}: {type(e).__name__}: {e}')
            seen = {i: t for i, t in seen.items() if now - t < KILL_KEEP}
            last = now
            time.sleep(POLL)
    finally:
        _mode.pop(name, None)
        with _lock:
            _watched.discard(pid)


def _save_loop():
    while True:
        time.sleep(SAVE_EVERY)
        try:
            _save()
        except OSError as e:
            print(f'[STATS] could not save {PATH} ({e})')


def on_drop(character, text):
    """A chat line of `character`'s game: a +1 item / dragonball it dropped counts."""
    said = DROP_SAID.search(text)
    mode = _mode.get(character)
    if not said or not mode:
        return
    field = 'dragonballs' if said.group('item').lower() == 'dragonball' else 'plus'
    _add(character, mode, field)


# ---------------------------------------------------------------- the report

def _totals(day_rows, names=None):
    """{mode: {seconds, kills, plus, dragonballs}} summed over the characters (all, or `names`)."""
    out = {m: {'seconds': 0, 'kills': 0, 'plus': 0, 'dragonballs': 0} for m in MODES}
    for name, modes in day_rows.items():
        if names and name not in names:
            continue
        for mode, row in modes.items():
            for field in out.get(mode, {}):
                out[mode][field] += row.get(field, 0)
    return out


def _rates(t):
    hours = t['seconds'] / 3600
    return {'hours': hours,
            'kills_h': t['kills'] / hours if hours else 0,
            'plus_h': t['plus'] / hours if hours else 0,
            'plus_k': t['plus'] * 1000 / t['kills'] if t['kills'] else 0}


def _bars(title, unit, days, values):
    """An SVG of grouped bars: one group a day, one bar a mode. values[day][mode] -> number."""
    w, h, left, bottom, top = 760, 260, 44, 40, 16
    top_value = max([v for d in days for v in values[d].values()] + [1]) * 1.15
    group = (w - left - 10) / max(len(days), 1)
    bar = min(18, group / 3)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}">']
    for i in range(5):
        y = top + (h - top - bottom) * i / 4
        v = top_value * (1 - i / 4)
        parts.append(f'<line x1="{left}" x2="{w - 10}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{v:.0f}</text>')
    for n, day in enumerate(days):
        x0 = left + group * n + group / 2 - bar
        for k, mode in enumerate(MODES):
            v = values[day].get(mode, 0)
            bh = (h - top - bottom) * v / top_value
            x = x0 + k * bar
            parts.append(f'<rect x="{x:.1f}" y="{h - bottom - bh:.1f}" width="{bar - 2:.1f}" '
                         f'height="{bh:.1f}" rx="2" class="m-{mode}"><title>{MODE_NAMES[mode]} '
                         f'{day}: {v:.1f} {unit}</title></rect>')
        parts.append(f'<text x="{left + group * n + group / 2:.1f}" y="{h - bottom + 16}" '
                     f'class="axis" text-anchor="middle">{day[5:]}</text>')
    parts.append('</svg>')
    return f'<section><h2>{html.escape(title)}</h2>{"".join(parts)}</section>'


def report():
    """Writes REPORT (the last DAYS days) and returns its path."""
    data = _load()
    days = sorted(data)[-DAYS:]
    rates = {d: {m: _rates(t) for m, t in _totals(data[d]).items()} for d in days}
    whole = {m: {'seconds': 0, 'kills': 0, 'plus': 0, 'dragonballs': 0} for m in MODES}
    for d in days:
        for m, t in _totals(data[d]).items():
            for f in t:
                whole[m][f] += t[f]
    cards = ''
    for m in MODES:
        r = _rates(whole[m])
        cards += (f'<div class="card m-{m}-edge"><h3>{MODE_NAMES[m]}</h3>'
                  f'<p><b>{r["kills_h"]:.0f}</b> monsters / hour</p>'
                  f'<p><b>{r["plus_h"]:.1f}</b> +1 / hour</p>'
                  f'<p><b>{r["plus_k"]:.1f}</b> +1 per 1000 monsters</p>'
                  f'<p class="muted">{r["hours"]:.1f} h · {whole[m]["kills"]} monsters · '
                  f'{whole[m]["plus"]} +1 · {whole[m]["dragonballs"]} DB</p></div>')
    rows = ''
    for d in reversed(days):
        for name in sorted(data[d]):
            for m in MODES:
                t = data[d][name].get(m)
                if not t:
                    continue
                r = _rates({k: t.get(k, 0) for k in ('seconds', 'kills', 'plus', 'dragonballs')})
                rows += (f'<tr><td>{d}</td><td>{html.escape(name)}</td><td>{MODE_NAMES[m]}</td>'
                         f'<td>{r["hours"]:.1f}</td><td>{t.get("kills", 0)}</td><td>{t.get("plus", 0)}</td>'
                         f'<td>{t.get("dragonballs", 0)}</td><td>{r["kills_h"]:.0f}</td><td>{r["plus_h"]:.1f}</td></tr>')
    charts = (_bars('Monsters per hour', 'monsters / hour', days,
                    {d: {m: rates[d][m]['kills_h'] for m in MODES} for d in days})
              + _bars('+1 items per hour', '+1 / hour', days,
                      {d: {m: rates[d][m]['plus_h'] for m in MODES} for d in days})
              + _bars('+1 items per 1000 monsters', '+1 per 1000', days,
                      {d: {m: rates[d][m]['plus_k'] for m in MODES} for d in days}))
    page = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Hunt Stats</title>
<style>
:root {{ --bg:#f6f7f9; --panel:#fff; --text:#1d2330; --muted:#6b7385; --grid:#e3e6ec;
        --auto:#2f6fdf; --static:#e0802b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14171d; --panel:#1d2129; --text:#e7e9ee;
        --muted:#9097a6; --grid:#2c313c; --auto:#5b93f0; --static:#f0a050; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--text);
       font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:800px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:0 0 8px; }}
.muted {{ color:var(--muted); font-size:13px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin:16px 0; }}
.card, section {{ background:var(--panel); border-radius:10px; padding:14px 16px; margin-bottom:12px; }}
.card h3 {{ margin:0 0 6px; }} .card p {{ margin:2px 0; }}
.m-auto-edge {{ border-top:4px solid var(--auto); }} .m-static-edge {{ border-top:4px solid var(--static); }}
.m-auto {{ fill:var(--auto); }} .m-static {{ fill:var(--static); }}
.grid {{ stroke:var(--grid); }} .axis {{ fill:var(--muted); font-size:11px; }}
.legend span {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 12px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th, td {{ text-align:left; padding:5px 6px; border-bottom:1px solid var(--grid); }}
.scroll {{ overflow-x:auto; }}
</style></head><body><main>
<h1>Hunt stats</h1>
<div class="muted">Last {len(days)} day(s), all characters · updated {datetime.datetime.now():%Y-%m-%d %H:%M}
<span class="legend"><span style="background:var(--auto)"></span>Auto hunt
<span style="background:var(--static)"></span>Static</span></div>
<div class="cards">{cards}</div>
{charts}
<section><h2>Per character</h2><div class="scroll"><table>
<tr><th>Day</th><th>Character</th><th>Mode</th><th>Hours</th><th>Monsters</th><th>+1</th><th>DB</th>
<th>Monsters/h</th><th>+1/h</th></tr>{rows}</table></div></section>
<p class="muted">Monsters: every one seen dying in the character's view (a kill by another player
right there counts too). +1 / DB: the game's "it dropped a ..." lines. Not counted: time dead or in the Market.</p>
</main></body></html>'''
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write(page)
    return REPORT


if __name__ == '__main__':
    path = report()
    print(f'report: {path}')
    os.startfile(path)
