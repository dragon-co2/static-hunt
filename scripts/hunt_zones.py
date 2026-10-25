"""Draw, on each map, the zone where the auto hunt plays (travel.py patrol): the map from the game's
own file (walkable cells light, walls dark), its Path Finding hunting areas (red) and the logged-in
characters (blue) on it. Left mouse paints the zone, right mouse rubs it out; Save keeps it in
~/Documents/DragonCO2/hunt_zones.json. In a map with a zone, the auto hunt goes there and roams it
whatever the monsters in sight, with a Scatter every few seconds.
    python hunt_zones.py"""
import os
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

import accounts
import game_actions
import game_memory
import travel

VIEW = 820                      # the map is drawn this many pixels across (its longer side)
REFRESH_MS = 2000               # how often the characters' places are drawn again
MAP_NAMES = {1002: 'Twin City', 1011: 'Phoenix Castle', 1020: 'Ape City', 1000: 'Desert',
             1015: 'Bird Island'}
GROUND, WALL, ZONE = (190, 180, 150), (40, 40, 45), (60, 200, 80)


def _root():
    """The game's folder: from a running game, else the launcher in the settings."""
    for pid in game_memory.game_pids():
        try:
            return travel.game_root(pid)
        except OSError:
            continue
    launcher = accounts.game_path()
    return os.path.dirname(launcher) if launcher else None


def _characters():
    """{character: (map, (x, y))} of the logged-in games."""
    out = {}
    for pid in game_memory.game_pids():
        name = game_memory._name_pid(pid)
        pos = name and game_actions.my_pos(pid)
        if pos:
            out[name] = (travel.map_id(pid), pos)
    return out


class Editor:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.win = tk.Tk()
        self.win.title('Hunt zones')
        self.win.resizable(False, False)
        bar = tk.Frame(self.win)
        bar.pack(fill='x', padx=8, pady=6)
        maps = sorted(set(MAP_NAMES) | {m for m, _ in _characters().values() if m})
        self.labels = {f'{m} {MAP_NAMES.get(m, "")}'.strip(): m for m in maps}
        self.choice = tk.StringVar(value=next(iter(self.labels)))
        ttk.Combobox(bar, textvariable=self.choice, values=list(self.labels), state='readonly',
                     width=22).pack(side='left')
        self.choice.trace_add('write', lambda *_: self.load())
        tk.Label(bar, text='  Brush').pack(side='left')
        self.brush = tk.IntVar(value=2)
        tk.Scale(bar, from_=1, to=6, orient='horizontal', variable=self.brush, length=90,
                 showvalue=True).pack(side='left')
        tk.Button(bar, text='Save', width=8, command=self.save).pack(side='left', padx=6)
        tk.Button(bar, text='Clear', width=8, command=self.clear).pack(side='left')
        self.info = tk.Label(bar, text='', font=('Consolas', 9), anchor='w', width=40)
        self.info.pack(side='left', padx=8)
        self.canvas = tk.Canvas(self.win, width=VIEW, height=VIEW, bg='black', highlightthickness=0)
        self.canvas.pack(padx=8, pady=(0, 8))
        for button, paint in (('<B1-Motion>', True), ('<Button-1>', True),
                              ('<B3-Motion>', False), ('<Button-3>', False)):
            self.canvas.bind(button, lambda e, p=paint: self.paint(e, p))
        self.canvas.bind('<ButtonRelease-1>', lambda e: self.draw())
        self.canvas.bind('<ButtonRelease-3>', lambda e: self.draw())
        self.canvas.bind('<Motion>', self.hover)
        self.load()
        self.win.after(REFRESH_MS, self.refresh)

    # -- the map
    def load(self):
        self.the_map = self.labels[self.choice.get()]
        try:
            self.width, self.height, walkable = travel.load_map(travel.map_file(self.root_dir, self.the_map))
        except (OSError, LookupError) as e:
            self.info.config(text=f'cannot load map {self.the_map}: {e}')
            return
        self.scale = VIEW / max(self.width, self.height)
        ground = Image.frombytes('L', (self.width, self.height), bytes(walkable))
        self.base = Image.merge('RGB', [ground.point(lambda v, c=c: GROUND[c] if v else WALL[c])
                                        for c in range(3)])
        self.size = (round(self.width * self.scale), round(self.height * self.scale))
        self.base_view = self.base.resize(self.size, Image.NEAREST)
        self.squares = travel.zone(self.the_map)
        self.areas = travel.hunting_areas(self.root_dir, self.the_map)
        self.saved = set(self.squares)
        self.draw()

    def draw(self):
        cols = self.width // travel.ZONE_CELLS + 1
        rows = self.height // travel.ZONE_CELLS + 1
        mask = Image.new('L', (cols, rows), 0)
        for sq in self.squares:
            if 0 <= sq[0] < cols and 0 <= sq[1] < rows:
                mask.putpixel(sq, 140)
        mask = mask.resize((round(cols * travel.ZONE_CELLS * self.scale),
                            round(rows * travel.ZONE_CELLS * self.scale)), Image.NEAREST).crop((0, 0) + self.size)
        tint = Image.new('RGB', self.size, ZONE)
        self.photo = ImageTk.PhotoImage(Image.composite(tint, self.base_view, mask))
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self.photo)
        for title, x, y, _ in self.areas:
            px, py = x * self.scale, y * self.scale
            self.canvas.create_oval(px - 4, py - 4, px + 4, py + 4, outline='red', width=2)
            self.canvas.create_text(px + 6, py, text=title, anchor='w', fill='red', font=('Segoe UI', 8))
        self.draw_characters()
        self.status()

    def draw_characters(self):
        self.canvas.delete('char')
        for name, (the_map, (x, y)) in _characters().items():
            if the_map == self.the_map:
                px, py = x * self.scale, y * self.scale
                self.canvas.create_oval(px - 4, py - 4, px + 4, py + 4, fill='#3080ff', outline='white', tags='char')
                self.canvas.create_text(px + 6, py - 8, text=name, anchor='w', fill='#3080ff',
                                        font=('Segoe UI', 8, 'bold'), tags='char')

    def refresh(self):
        self.draw_characters()
        self.win.after(REFRESH_MS, self.refresh)

    # -- painting
    def cell(self, event):
        return int(event.x / self.scale), int(event.y / self.scale)

    def paint(self, event, add):
        x, y = self.cell(event)
        r = self.brush.get() - 1
        sx, sy = x // travel.ZONE_CELLS, y // travel.ZONE_CELLS
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                (self.squares.add if add else self.squares.discard)((sx + dx, sy + dy))
        z = travel.ZONE_CELLS * self.scale            # quick feedback; the full picture on release
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                px, py = (sx + dx) * z, (sy + dy) * z
                self.canvas.create_rectangle(px, py, px + z, py + z, outline='',
                                             fill='#3cc850' if add else '#888070')
        self.status()

    def hover(self, event):
        x, y = self.cell(event)
        self.info.config(text=f'cell ({x}, {y})  ' + self.state())

    def state(self):
        changed = ' - not saved' if self.squares != self.saved else ''
        return f'{len(self.squares)} squares{changed}'

    def status(self):
        self.info.config(text=self.state())

    def save(self):
        travel.save_zone(self.the_map, self.squares)
        self.saved = set(self.squares)
        self.status()
        print(f'map {self.the_map}: zone saved ({len(self.squares)} squares)')

    def clear(self):
        self.squares = set()
        self.draw()


def main():
    root_dir = _root()
    if not root_dir:
        raise SystemExit('no game running and no launcher in the settings - cannot find the maps')
    Editor(root_dir).win.mainloop()


if __name__ == '__main__':
    main()
