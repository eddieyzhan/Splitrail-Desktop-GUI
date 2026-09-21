"""Model pricing editor. User rates are stored locally and survive upgrades."""
from __future__ import annotations

import tkinter as tk
import webbrowser
from datetime import date
from tkinter import ttk

from .pricing import (CATALOG, RATE_FIELDS, PricingError, custom_rates, load_overrides,
                      model_key, normalize_model, resolve_rates, save_override)


class PricingSettings(tk.Toplevel):
    def __init__(self, parent, unknown: tuple[str, ...], on_change) -> None:
        from .app import BG, CARD, INK, MUTED, BLUE, RED, FONT
        super().__init__(parent)
        self.title('Settings · Model pricing')
        self.geometry('960x670')
        self.minsize(860, 620)
        self.configure(bg=BG)
        self.transient(parent)
        self.unknown = unknown
        self.on_change = on_change
        self.overrides = {}
        self._load_error = None
        try:
            self.overrides = load_overrides()
        except (PricingError, OSError) as exc:
            self._load_error = str(exc)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        head = tk.Frame(self, bg=BG)
        head.grid(row=0, column=0, sticky='ew', padx=24, pady=(22, 14))
        tk.Label(head, text='Model pricing', bg=BG, fg=INK, font=(FONT, 17, 'bold')).pack(anchor='w')
        tk.Label(head, text='USD per 1 million tokens · Custom rates override estimates for matching models.',
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor='w', pady=(5, 0))
        toolbar = tk.Frame(self, bg=BG)
        toolbar.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 12))
        toolbar.columnconfigure(1, weight=1)
        tk.Label(toolbar, text='Search', bg=BG, fg=MUTED).grid(row=0, column=0, padx=(0, 10))
        self.search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky='ew')
        self.filter_var = tk.StringVar(value='Needs pricing' if any(resolve_rates(n, overrides=self.overrides) is None for n in unknown) else 'All models')
        filter_box = ttk.Combobox(toolbar, textvariable=self.filter_var,
                                  values=('All models', 'Needs pricing', 'Custom rates'), width=17, state='readonly')
        filter_box.grid(row=0, column=2, padx=(10, 0))
        filter_box.bind('<<ComboboxSelected>>', lambda _event: self.populate())
        ttk.Button(toolbar, text='+ Add model', command=self.new_model, style='Quiet.TButton').grid(row=0, column=3, padx=(10, 0))
        body = tk.Frame(self, bg=BG)
        body.grid(row=2, column=0, sticky='nsew', padx=24)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ('model', 'provider', 'input', 'output', 'cache_read', 'cache_write', 'status')
        self.tree = ttk.Treeview(body, columns=columns, show='headings', selectmode='browse')
        for key, title, width in zip(columns, ('Model', 'Provider', 'Input', 'Output', 'Read', 'Write', 'Rate'), (240, 95, 75, 75, 90, 95, 90)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width if key == 'model' else 65, anchor='w' if key in ('model', 'provider', 'status') else 'e', stretch=key == 'model')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_model)
        editor = tk.Frame(self, bg=CARD, padx=16, pady=14)
        editor.grid(row=3, column=0, sticky='ew', padx=24, pady=(14, 0))
        editor.columnconfigure(0, weight=2)
        self.model_var = tk.StringVar()
        self.rate_vars = {field: tk.StringVar() for field in RATE_FIELDS}
        tk.Label(editor, text='Model ID', bg=CARD, fg=MUTED).grid(row=0, column=0, sticky='w')
        self.model_entry = ttk.Entry(editor, textvariable=self.model_var, width=28)
        self.model_entry.grid(row=1, column=0, sticky='ew', padx=(0, 12), pady=(5, 0))
        for index, (field, label) in enumerate(zip(RATE_FIELDS, ('Input', 'Output', 'Cache read', 'Cache write')), 1):
            editor.columnconfigure(index, weight=1)
            tk.Label(editor, text=label, bg=CARD, fg=MUTED).grid(row=0, column=index, sticky='w')
            ttk.Entry(editor, textvariable=self.rate_vars[field], width=10).grid(row=1, column=index, sticky='ew', padx=(0, 10), pady=(5, 0))
        tk.Label(editor, text='Leave unused cache rates blank. Enter 0 for free tokens.', bg=CARD, fg=MUTED,
                 font=(FONT, 8)).grid(row=2, column=0, columnspan=5, sticky='w', pady=(10, 0))
        self.feedback_var = tk.StringVar(value=self._load_error or '')
        self.feedback_label = tk.Label(self, textvariable=self.feedback_var, bg=BG, fg=RED, anchor='w', wraplength=880)
        self.feedback_label.grid(row=4, column=0, sticky='ew', padx=24, pady=(8, 0))
        footer = tk.Frame(self, bg=BG)
        footer.grid(row=5, column=0, sticky='ew', padx=24, pady=(8, 20))
        self.source_button = ttk.Button(footer, text='Pricing source ↗', command=self.open_source, style='Quiet.TButton')
        self.source_button.pack(side='left')
        self.remove_button = ttk.Button(footer, text='Remove custom rate', command=self.remove_rate, style='Quiet.TButton', state='disabled')
        self.remove_button.pack(side='left', padx=8)
        ttk.Button(footer, text='Save rate', command=self.save_rate, style='Primary.TButton').pack(side='right')
        ttk.Button(footer, text='Done', command=self.destroy, style='Quiet.TButton').pack(side='right', padx=8)
        tk.Label(self, text=f"{len(CATALOG['models'])} built-in models · Verified {CATALOG['verified']} · Standard text rates; estimates exclude paid tools and subscription charges.",
                 bg=BG, fg=MUTED, font=(FONT, 8)).grid(row=6, column=0, sticky='w', padx=24, pady=(0, 14))
        self.search_var.trace_add('write', lambda *_args: self.populate())
        self.bind('<Escape>', lambda _event: self.destroy())
        self.populate()
        if self.filter_var.get() == "Needs pricing" and self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])
        from .themes import match_button_surfaces
        match_button_surfaces(self, CARD)
        search.focus_set()

    def populate(self, selected: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower()
        names = set(CATALOG['models']) | set(self.overrides) | {normalize_model(n) for n in self.unknown}
        for name in sorted(names):
            custom = custom_rates(name, self.overrides)
            rate = resolve_rates(name, date.today().isoformat(), self.overrides)
            provider = CATALOG['models'].get(model_key(name), {}).get('provider', 'Custom' if custom else '—')
            aliases = ' '.join(alias for alias, target in CATALOG['aliases'].items() if target == name)
            if query not in f'{name} {provider} {aliases}'.lower():
                continue
            if self.filter_var.get() == 'Custom rates' and custom is None:
                continue
            if self.filter_var.get() == 'Needs pricing' and rate is not None:
                continue
            values = [name, provider] + [('—' if rate is None or rate.get(f) is None else f"${rate[f]:g}") for f in RATE_FIELDS]
            self.tree.insert('', 'end', iid=name, values=(*values, 'Custom' if custom is not None else ('Built-in' if rate else 'Missing')))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def select_model(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        name = selected[0]
        self.model_var.set(name)
        rate = resolve_rates(name, date.today().isoformat(), self.overrides)
        for field, variable in self.rate_vars.items():
            variable.set('' if rate is None or rate.get(field) is None else f'{rate[field]:g}')
        self.remove_button.configure(state='normal' if name in self.overrides else 'disabled')
        self.source_button.configure(state='normal' if model_key(name) in CATALOG['models'] else 'disabled')

    def new_model(self) -> None:
        self.tree.selection_remove(*self.tree.selection())
        self.model_var.set('')
        for variable in self.rate_vars.values():
            variable.set('')
        self.remove_button.configure(state='disabled')
        self.feedback_var.set('')
        self.model_entry.focus_set()

    def save_rate(self) -> None:
        from .app import RED, TEAL
        rates = {f: (v.get().strip() or None) for f, v in self.rate_vars.items()}
        try:
            save_override(self.model_var.get(), rates)
            self.overrides = load_overrides()
        except (PricingError, OSError) as exc:
            self.feedback_label.configure(fg=RED)
            self.feedback_var.set(str(exc))
            return
        self.feedback_label.configure(fg=TEAL)
        self.feedback_var.set('Saved. Usage estimates are refreshing.')
        self.populate(normalize_model(self.model_var.get()))
        self.on_change()

    def remove_rate(self) -> None:
        from .app import RED, TEAL
        name = normalize_model(self.model_var.get())
        try:
            save_override(name, None)
            self.overrides = load_overrides()
        except (PricingError, OSError) as exc:
            self.feedback_label.configure(fg=RED)
            self.feedback_var.set(str(exc))
            return
        self.feedback_label.configure(fg=TEAL)
        self.feedback_var.set('Custom rate removed. Default pricing restored.')
        self.populate(name)
        self.on_change()

    def open_source(self) -> None:
        model = CATALOG['models'].get(model_key(self.model_var.get()))
        if model:
            webbrowser.open(model.get('source', CATALOG['sources'][model['provider']]))
