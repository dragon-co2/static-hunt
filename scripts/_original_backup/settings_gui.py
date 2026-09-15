# -*- coding: utf-8 -*-
"""
Settings GUI for the dragon-co2 automation scripts.

Lets you tune every numeric parameter used by the scripts in this folder
(confidence thresholds, wait times, retry counts, pixel offsets, grid
sizes, ...) with a slider or by typing the number directly, and save
them to settings_user.json -- the file every script actually reads at
startup (see dragon_settings.py).

- "Save changes"           writes your current edits to settings_user.json.
- "Reload from disk"       discards unsaved edits, re-reads settings_user.json.
- "Reset ALL to default"   restores every value (in every tab) back to the
                            factory defaults from settings_schema.json, and
                            saves immediately.
- Each row also has its own small "Reset" button for just that one value.

settings_schema.json (the factory defaults + slider ranges) is only ever
READ by this program -- it is never modified, so "reset to default" always
has something correct to go back to, no matter what gets typed into
settings_user.json.

Run: python settings_gui.py   (or double-click it, if .py files are
associated with pythonw.exe on this machine)
"""
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

_BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(_BASE, 'settings_schema.json')
USER_PATH = os.path.join(_BASE, 'settings_user.json')

WINDOW_TITLE = 'Dragon CO2 - إعدادات السكربتات'


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def load_schema():
    if not os.path.exists(SCHEMA_PATH):
        messagebox.showerror(
            'ملف ناقص',
            f'ملف الإعدادات الأساسي مش موجود:\n{SCHEMA_PATH}\n\n'
            'من غير الملف ده البرنامج مش هيعرف القيم الافتراضية ولا حدود كل slider.'
        )
        sys.exit(1)
    with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_user_values(schema):
    """Loads settings_user.json; if it's missing or broken, rebuilds it
    from the schema defaults (and writes it back out) so the app always
    has something valid to show."""
    values = None
    if os.path.exists(USER_PATH):
        try:
            with open(USER_PATH, 'r', encoding='utf-8') as f:
                values = json.load(f)
        except Exception:
            values = None
    if not isinstance(values, dict):
        values = {}

    changed = False
    for script, fields in schema.items():
        section = values.setdefault(script, {})
        if not isinstance(section, dict):
            section = {}
            values[script] = section
            changed = True
        for name, meta in fields.items():
            if name == '__title__':
                continue
            if name not in section:
                section[name] = meta['default']
                changed = True
    if changed:
        save_user_values(values)
    return values


def save_user_values(values):
    tmp_path = USER_PATH + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(values, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, USER_PATH)


# ---------------------------------------------------------------------------
# One numeric field (a plain slider, or an X/Y pixel pair)
# ---------------------------------------------------------------------------
class Field:
    def __init__(self, app, parent, script, name, meta):
        self.app = app
        self.script = script
        self.name = name
        self.meta = meta
        self.is_point = meta['type'] == 'point'
        self.is_int = meta['type'] == 'int'

        row = ttk.Frame(parent)
        row.pack(fill='x', padx=6, pady=3)
        self.row_widget = row
        self.search_text = (name + ' ' + meta.get('desc', '')).lower()

        head = ttk.Frame(row)
        head.pack(fill='x')
        ttk.Label(head, text=name, font=('TkDefaultFont', 9, 'bold'), width=20, anchor='w').pack(side='left')
        desc = meta.get('desc', '')
        if desc:
            ttk.Label(head, text=desc, foreground='#666666', wraplength=430, justify='right', anchor='e').pack(
                side='left', fill='x', expand=True, padx=(8, 8))

        body = ttk.Frame(row)
        body.pack(fill='x', pady=(2, 0))

        if self.is_point:
            dx, dy = meta['default']
            self.x = self._make_axis(body, 'X', dx)
            self.y = self._make_axis(body, 'Y', dy)
        else:
            self.scalar = self._make_axis(body, None, meta['default'])

        ttk.Button(row, text='استرجاع', width=9, command=self.reset).pack(side='right', padx=(4, 0))
        ttk.Separator(parent, orient='horizontal').pack(fill='x', padx=6)

    def _make_axis(self, parent, label, value):
        meta = self.meta
        holder = ttk.Frame(parent)
        holder.pack(side='left', fill='x', expand=True, padx=(0, 10))
        if label:
            ttk.Label(holder, text=label + ':', width=2).pack(side='left')

        num_var = tk.DoubleVar(value=value)
        str_var = tk.StringVar(value=self._fmt(value))

        lo, hi = meta['min'], meta['max']
        if value < lo:
            lo = value
        if value > hi:
            hi = value

        scale = ttk.Scale(holder, from_=lo, to=hi, orient='horizontal', variable=num_var,
                           command=lambda v, sv=str_var, nv=num_var: self._on_scale(v, sv, nv))
        scale.pack(side='left', fill='x', expand=True)

        entry = ttk.Entry(holder, textvariable=str_var, width=9, justify='center')
        entry.pack(side='left', padx=(6, 0))
        entry.bind('<Return>', lambda e, sv=str_var, nv=num_var, sc=scale: self._commit_entry(sv, nv, sc))
        entry.bind('<FocusOut>', lambda e, sv=str_var, nv=num_var, sc=scale: self._commit_entry(sv, nv, sc))

        return {'num': num_var, 'str': str_var, 'scale': scale}

    def _fmt(self, value):
        if self.is_int:
            return str(int(round(value)))
        return f'{float(value):g}'

    def _on_scale(self, value, str_var, num_var):
        v = float(value)
        if self.is_int:
            v = round(v)
            num_var.set(v)
        str_var.set(self._fmt(v))
        self.app.mark_dirty()

    def _commit_entry(self, str_var, num_var, scale):
        raw = str_var.get().strip().replace(',', '.')
        try:
            v = float(raw)
        except ValueError:
            str_var.set(self._fmt(num_var.get()))
            return
        if self.is_int:
            v = round(v)
        lo, hi = float(scale.cget('from')), float(scale.cget('to'))
        if v < lo:
            scale.configure(from_=v)
        if v > hi:
            scale.configure(to=v)
        num_var.set(v)
        str_var.set(self._fmt(v))
        self.app.mark_dirty()

    def get_value(self):
        if self.is_point:
            gx = self.x['num'].get()
            gy = self.y['num'].get()
            return [int(round(gx)), int(round(gy))]
        v = self.scalar['num'].get()
        return int(round(v)) if self.is_int else round(float(v), 6)

    def set_value(self, value, mark_dirty=True):
        if self.is_point:
            dx, dy = value
            for axis, v in ((self.x, dx), (self.y, dy)):
                axis['num'].set(v)
                axis['str'].set(self._fmt(v))
        else:
            self.scalar['num'].set(value)
            self.scalar['str'].set(self._fmt(value))
        if mark_dirty:
            self.app.mark_dirty()

    def reset(self):
        self.set_value(self.meta['default'])
        self.app.save_single(self.script, self.name, self.meta['default'])
        self.app.set_status(f'اترجع للافتراضي: {self.script}.{self.name}')

    def matches(self, query):
        return query in self.search_text


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry('900x620')
        self.root.minsize(700, 420)

        self.schema = load_schema()
        self.values = load_user_values(self.schema)
        self.dirty = False
        self.fields = []  # list of Field

        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()

        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    # -- layout -------------------------------------------------------
    def _build_toolbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill='x', padx=8, pady=6)

        ttk.Button(bar, text='حفظ التغييرات', command=self.save_all).pack(side='left')
        ttk.Button(bar, text='إعادة تحميل من الملف', command=self.reload_from_disk).pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='استرجاع الكل للافتراضي', command=self.reset_all).pack(side='left', padx=(6, 0))

        ttk.Label(bar, text='بحث:').pack(side='left', padx=(20, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self.apply_filter())
        ttk.Entry(bar, textvariable=self.search_var, width=24).pack(side='left')

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(0, 6))

        for script, fields_meta in self.schema.items():
            title = fields_meta.get('__title__', script)
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=title)

            canvas = tk.Canvas(tab, highlightthickness=0)
            vsb = ttk.Scrollbar(tab, orient='vertical', command=canvas.yview)
            inner = ttk.Frame(canvas)
            inner.bind('<Configure>', lambda e, c=canvas: c.configure(scrollregion=c.bbox('all')))
            canvas.create_window((0, 0), window=inner, anchor='nw')
            canvas.configure(yscrollcommand=vsb.set)
            canvas.pack(side='left', fill='both', expand=True)
            vsb.pack(side='right', fill='y')

            def _wheel(event, c=canvas):
                c.yview_scroll(int(-1 * (event.delta / 120)), 'units')
            canvas.bind('<Enter>', lambda e, c=canvas, w=_wheel: c.bind_all('<MouseWheel>', w))
            canvas.bind('<Leave>', lambda e, c=canvas: c.unbind_all('<MouseWheel>'))

            for name, meta in fields_meta.items():
                if name == '__title__':
                    continue
                current = self.values.get(script, {}).get(name, meta['default'])
                field = Field(self, inner, script, name, meta)
                field.set_value(current, mark_dirty=False)
                self.fields.append(field)

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value=f'الملف: {USER_PATH}')
        ttk.Label(self.root, textvariable=self.status_var, anchor='w', foreground='#555555').pack(
            fill='x', padx=8, pady=(0, 6))

    # -- state ----------------------------------------------------------
    def mark_dirty(self):
        self.dirty = True
        self.set_status('في تغييرات لسه ما اتحفظتش — اضغط "حفظ التغييرات"')

    def set_status(self, text):
        self.status_var.set(text)

    def apply_filter(self):
        query = self.search_var.get().strip().lower()
        for field in self.fields:
            visible = field.matches(query) if query else True
            if visible:
                field.row_widget.pack(fill='x', padx=6, pady=3)
            else:
                field.row_widget.pack_forget()

    # -- collecting current values --------------------------------------
    def _collect(self):
        out = {}
        for field in self.fields:
            out.setdefault(field.script, {})[field.name] = field.get_value()
        return out

    # -- actions ----------------------------------------------------------
    def save_all(self):
        self.values = self._collect()
        save_user_values(self.values)
        self.dirty = False
        self.set_status('اتحفظ.')

    def save_single(self, script, name, value):
        """Used by a per-field Reset button: persists just that one value
        immediately, without touching anything else that might be unsaved."""
        try:
            with open(USER_PATH, 'r', encoding='utf-8') as f:
                on_disk = json.load(f)
        except Exception:
            on_disk = {}
        on_disk.setdefault(script, {})[name] = value
        save_user_values(on_disk)
        self.values = on_disk

    def reload_from_disk(self):
        if self.dirty:
            if not messagebox.askyesno('تأكيد', 'في تغييرات لسه ما اتحفظتش، هتتلغي. تكمل؟'):
                return
        self.values = load_user_values(self.schema)
        for field in self.fields:
            current = self.values.get(field.script, {}).get(field.name, field.meta['default'])
            field.set_value(current, mark_dirty=False)
        self.dirty = False
        self.set_status('اتعمل إعادة تحميل من الملف.')

    def reset_all(self):
        if not messagebox.askyesno(
                'تأكيد',
                'هيرجع كل القيم في كل التابات للإعدادات الافتراضية (المصنع) ويحفظها فورًا.\n'
                'متأكد؟'):
            return
        for field in self.fields:
            field.set_value(field.meta['default'], mark_dirty=False)
        self.save_all()
        self.set_status('كل القيم رجعت للإعدادات الافتراضية واتحفظت.')

    def on_close(self):
        if self.dirty:
            resp = messagebox.askyesnocancel('تأكيد', 'في تغييرات لسه ما اتحفظتش. تحفظها قبل الخروج؟')
            if resp is None:
                return
            if resp:
                self.save_all()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if 'vista' in style.theme_names():
            style.theme_use('vista')
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
