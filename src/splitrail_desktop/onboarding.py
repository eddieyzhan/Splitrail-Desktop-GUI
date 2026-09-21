"""Local-first welcome, appearance settings, and explicit GitHub connection flow."""
from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import preferences, sync


class WelcomeWindow(tk.Toplevel):
    def __init__(self, app):
        from . import app as ui
        super().__init__(app)
        self.app = app
        self.title('Welcome to Splitrail')
        self.geometry('620x490')
        self.resizable(False, False)
        self.transient(app)
        self.configure(bg=ui.CARD, padx=38, pady=32)
        tk.Label(self, text='Your usage. In one place.', bg=ui.CARD, fg=ui.INK,
                 font=(ui.FONT, 24, 'bold')).pack(anchor='w', pady=(12, 10))
        tk.Label(self, text='A quiet home for your AI activity.', bg=ui.CARD, fg=ui.MUTED,
                 font=(ui.FONT, 12)).pack(anchor='w', pady=(0, 30))
        for title, detail in (
            ('See the full picture', 'Tokens, estimated costs and trends across your tools.'),
            ('Keep your data yours', 'Start locally. GitHub sync is entirely optional.'),
            ('Bring your devices together', 'Connect a private repository that you control.')):
            tk.Label(self, text=title, bg=ui.CARD, fg=ui.INK, font=(ui.FONT, 11, 'bold')).pack(anchor='w', pady=(0, 4))
            tk.Label(self, text=detail, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 10)).pack(anchor='w', pady=(0, 18))
        footer = tk.Frame(self, bg=ui.CARD)
        footer.pack(fill='x', side='bottom', pady=(15, 0))
        ttk.Button(footer, text='Use on this device', command=self.local, style='Quiet.TButton').pack(side='left')
        ttk.Button(footer, text='Connect GitHub', command=self.connect, style='Primary.TButton').pack(side='right')
        from .themes import match_button_surfaces
        match_button_surfaces(self, ui.CARD)
        self.protocol('WM_DELETE_WINDOW', self.local)
        self.bind('<Escape>', lambda _event: self.local())

    def local(self):
        preferences.save(onboarded=True)
        self.destroy()

    def connect(self):
        self.local()
        self.app._open_settings(tab='sync')


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, tab='general'):
        from . import app as ui
        super().__init__(app)
        self.app = app
        self.title('Settings')
        self.geometry('760x740')
        self.minsize(720, 700)
        self.transient(app)
        self.configure(bg=ui.BG)
        self.events = queue.SimpleQueue()
        self.cancel = threading.Event()
        self.busy = False
        self.alive = True
        self.login = None
        self._last_action = None
        tk.Label(self, text='Settings', bg=ui.BG, fg=ui.INK, font=(ui.FONT, 23, 'bold')).pack(anchor='w', padx=28, pady=(24, 18))
        footer = tk.Frame(self, bg=ui.BG)
        footer.pack(fill='x', side='bottom', padx=28, pady=(0, 18))
        ttk.Button(footer, text='Done', command=self.close, style='Quiet.TButton').pack(side='right')
        self.book = ttk.Notebook(self)
        self.book.pack(fill='both', expand=True, padx=28, pady=(0, 12))
        self.general = tk.Frame(self.book, bg=ui.CARD, padx=24, pady=24)
        self.sync_container = tk.Frame(self.book, bg=ui.CARD)
        self.scrollbar = ttk.Scrollbar(self.sync_container, orient='vertical')
        self.scrollbar.pack(side='right', fill='y')
        self.scroll = tk.Canvas(self.sync_container, bg=ui.CARD, highlightthickness=0, height=1,
                                yscrollcommand=self.scrollbar.set)
        self.scroll.pack(fill='both', expand=True)
        self.scrollbar.configure(command=self.scroll.yview)
        self.sync_page = tk.Frame(self.scroll, bg=ui.CARD, padx=24, pady=20)
        window = self.scroll.create_window(0, 0, window=self.sync_page, anchor='nw')
        self.scroll.bind('<Configure>', lambda event: self.scroll.itemconfigure(window, width=event.width))
        self.sync_page.bind('<Configure>', lambda _event: self.scroll.configure(scrollregion=self.scroll.bbox('all')))
        self.book.add(self.general, text='General')
        self.book.add(self.sync_container, text='GitHub sync')
        self._build_general(ui)
        self._build_sync(ui)
        self.bind('<Destroy>', self._destroyed, add='+')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        from .themes import match_button_surfaces
        match_button_surfaces(self, ui.CARD)
        self.select(tab)
        self._poll_id = self.after(100, self.poll)

    def select(self, tab):
        self.book.select(self.sync_container if tab == 'sync' else self.general)

    def _build_general(self, ui):
        self._heading(self.general, 'Appearance', 'Choose a space that feels like you.', ui)
        self.theme = tk.StringVar(value=self.app.theme_name)
        cards = tk.Frame(self.general, bg=ui.CARD)
        cards.pack(fill='x', pady=(12, 20))
        for name, colors, detail in (
            ('Pearl', ('#F5F5F7', '#FFFFFF', '#0066CC'), 'Light, calm and spacious'),
            ('Nord', ('#2E3440', '#3B4252', '#88C0D0'), 'Soft contrast after dark')):
            card = tk.Frame(cards, bg=ui.CARD)
            card.pack(side='left', fill='x', expand=True, padx=(0, 16))
            preview = tk.Canvas(card, height=80, width=250, bg=colors[0], highlightthickness=0)
            preview._theme_preview = True
            preview.pack(fill='x', pady=(0, 10))
            preview.create_rectangle(16, 12, 234, 68, fill=colors[1], outline='')
            preview.create_rectangle(30, 24, 101, 30, fill=colors[2], outline='')
            preview.create_rectangle(30, 41, 74, 59, fill=colors[2], outline='')
            ttk.Radiobutton(card, text=name, value=name, variable=self.theme, command=self.change_theme).pack(anchor='w')
            tk.Label(card, text=detail, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9)).pack(anchor='w', pady=(6, 0))
        self._heading(self.general, 'Model pricing', 'Built-in prices, with room for your own.', ui)
        ttk.Button(self.general, text='Manage model prices', command=self.app._open_pricing, style='Quiet.TButton').pack(anchor='w', pady=(14, 20))
        self.source_button = ttk.Button(self.general, text='Get Splitrail collector ↗', command=lambda: webbrowser.open('https://github.com/Piebald-AI/splitrail#installation'), style='Quiet.TButton')
        self.source_button.pack(anchor='w', pady=(14, 0))

    @staticmethod
    def _heading(parent, title, subtitle, ui):
        tk.Label(parent, text=title, bg=ui.CARD, fg=ui.INK, font=(ui.FONT, 13, 'bold')).pack(anchor='w')
        tk.Label(parent, text=subtitle, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 10), wraplength=590, justify='left').pack(anchor='w', pady=(5, 0))

    def _build_sync(self, ui):
        self.invalid_config = False
        try:
            config = sync.settings()
        except (ValueError, OSError, TypeError, sync.SyncError):
            config = None
            self.invalid_config = True
        self.config = config
        self._heading(self.sync_page, 'Your devices, together', 'Connect GitHub and keep usage in a private repository you control.', ui)
        self.account_var = tk.StringVar(value='GitHub CLI is ready' if shutil.which('gh') else 'Install GitHub CLI to connect')
        tk.Label(self.sync_page, textvariable=self.account_var, bg=ui.CARD, fg=ui.INK, font=(ui.FONT, 10, 'bold')).pack(anchor='w', pady=(18, 8))
        actions = tk.Frame(self.sync_page, bg=ui.CARD)
        actions.pack(fill='x')
        self.auth_button = ttk.Button(actions, text='Sign in with browser', command=self.authenticate, style='Quiet.TButton')
        self.auth_button.pack(side='left')
        self.check_button = ttk.Button(actions, text='Check connection', command=lambda: self.run('account', sync.account), style='Quiet.TButton')
        self.check_button.pack(side='left', padx=8)
        self.install_button = ttk.Button(actions, text='Get GitHub CLI ↗', command=lambda: webbrowser.open('https://cli.github.com/'), style='Quiet.TButton')
        self.install_button.pack(side='left')
        self.code_var = tk.StringVar()
        self.code_label = tk.Label(self.sync_page, textvariable=self.code_var, bg=ui.CARD, fg=ui.BLUE, font=(ui.FONT, 10, 'bold'))

        self.open_auth = ttk.Button(self.sync_page, text='Open GitHub activation ↗', command=lambda: webbrowser.open('https://github.com/login/device'), style='Quiet.TButton')
        self.destination = tk.StringVar(value='Use existing repository' if config else 'Create private repository')
        self.repo_var = tk.StringVar(value=config['repository'] if config else 'splitrail-usage')
        repo_head = tk.Frame(self.sync_page, bg=ui.CARD)
        repo_head.pack(fill='x', pady=(14, 8))
        self.destination_box = ttk.Combobox(repo_head, values=('Create private repository', 'Use existing repository'),
                                          textvariable=self.destination, state='readonly', width=30)
        self.destination_box.pack(side='left')
        self.destination_box.bind('<<ComboboxSelected>>', self.destination_changed)
        self.repo_entry = ttk.Entry(self.sync_page, textvariable=self.repo_var)
        self.repo_entry.pack(fill='x')
        self.repo_hint = tk.StringVar(value='owner/repository' if config else 'A new private repository in your GitHub account')
        tk.Label(self.sync_page, textvariable=self.repo_hint, bg=ui.CARD, fg=ui.MUTED, font=(ui.FONT, 9)).pack(anchor='w', pady=(5, 12))
        source = tk.Frame(self.sync_page, bg=ui.CARD)
        source.pack(fill='x')
        tk.Label(source, text='Share from this device', bg=ui.CARD, fg=ui.INK, font=(ui.FONT, 10)).pack(side='left')
        self.scope = tk.StringVar(value='All tools' if (config and config['scope'] == 'all') or (not config and self.app._usage_mode != 'codex') else 'Codex only')
        self.scope_box = ttk.Combobox(source, values=('All tools', 'Codex only'), textvariable=self.scope, state='readonly', width=16)
        self.scope_box.pack(side='right')
        self.automatic = tk.BooleanVar(value=config['automatic'] if config else False)
        self.receive_only = tk.BooleanVar(value=config['receive_only'] if config else False)
        self.auto_check = ttk.Checkbutton(self.sync_page, text='Sync automatically when usage refreshes', variable=self.automatic)
        self.auto_check.pack(anchor='w', pady=(14, 8))
        self.receive_check = ttk.Checkbutton(self.sync_page, text='Download only on this device', variable=self.receive_only)
        self.receive_check.pack(anchor='w')
        self.consent = tk.BooleanVar(value=bool(config))
        self.consent_check = ttk.Checkbutton(self.sync_page, text='Allow usage totals and estimated costs to sync to this private repository', variable=self.consent)
        self.consent_check.pack(anchor='w', pady=(14, 7))
        tk.Label(self.sync_page, text='Includes dates and known model/tool names. No prompts, responses, paths or credentials.\nCustom names are anonymized. Each device must have its own local usage history.',
                 bg=ui.CARD, fg=ui.MUTED, justify='left', wraplength=600, font=(ui.FONT, 9)).pack(anchor='w')
        self.feedback = tk.StringVar(value='Sync settings are damaged. Choose Disconnect, then connect again.' if self.invalid_config else sync.status_text() if config else 'You can keep using Splitrail locally at any time.')
        footer = tk.Frame(self.sync_container, bg=ui.CARD, padx=24, pady=12)
        footer.pack(fill='x', side='bottom')
        tk.Label(footer, textvariable=self.feedback, bg=ui.CARD, fg=ui.BLUE, wraplength=590,
                 justify='left', font=(ui.FONT, 9)).pack(anchor='w', pady=(0, 10))
        bottom = tk.Frame(footer, bg=ui.CARD)
        bottom.pack(fill='x')
        self.connect_button = ttk.Button(bottom, text='Save connection' if config else 'Connect', command=self.connect, style='Primary.TButton')
        self.connect_button.pack(side='right')
        self.sync_button = ttk.Button(bottom, text='Sync now', command=self.sync_now, style='Quiet.TButton')
        self.sync_button.pack(side='right', padx=8)
        self.disconnect_button = ttk.Button(bottom, text='Disconnect', command=self.disconnect, style='Quiet.TButton')
        self.disconnect_button.pack(side='left')
        def bind_scroll(widget):
            widget.bind('<MouseWheel>', lambda event: self.scroll.yview_scroll(-1 if event.delta > 0 else 1, 'units'), add='+')
            widget.bind('<Button-4>', lambda _event: self.scroll.yview_scroll(-1, 'units'), add='+')
            widget.bind('<Button-5>', lambda _event: self.scroll.yview_scroll(1, 'units'), add='+')
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(self.sync_page)
        self.update_controls()

    def change_theme(self):
        self.app.set_theme(self.theme.get())

    def destination_changed(self, _event=None):
        existing = self.destination.get() == 'Use existing repository'
        self.repo_hint.set('owner/repository · Use the same repository on each device' if existing else 'A new private repository in your GitHub account')
        self.repo_var.set(f'{self.login or "owner"}/splitrail-usage' if existing else 'splitrail-usage')

    def update_controls(self):
        for button in (self.auth_button, self.check_button, self.connect_button):
            button.configure(state='disabled' if self.busy else 'normal')
        for widget in (self.repo_entry, self.auto_check, self.receive_check, self.consent_check):
            widget.configure(state='disabled' if self.busy else 'normal')
        self.destination_box.configure(state='disabled' if self.busy or self.config else 'readonly')
        self.scope_box.configure(state='disabled' if self.busy else 'readonly')
        self.disconnect_button.configure(state='disabled' if self.busy or (not self.config and not self.invalid_config) else 'normal')
        self.sync_button.configure(state='disabled' if self.busy or not self.config else 'normal')

    def run(self, action, callback):
        if self.app._demo_mode:
            self.feedback.set('GitHub connections are unavailable in demo mode.')
            return
        if self.busy:
            return
        self.busy = True
        self._last_action = action
        self.feedback.set({'account': 'Checking GitHub…', 'auth': 'Complete sign-in in your browser…',
                           'connect': 'Connecting your private repository…', 'sync': 'Syncing devices…',
                           'disconnect': 'Disconnecting…'}[action])
        self.update_controls()
        def worker():
            try:
                self.events.put((action, callback(), None))
            except Exception as exc:
                self.events.put((action, None, exc))
        threading.Thread(target=worker, daemon=True).start()

    def authenticate(self):
        self.cancel.clear()
        self.run('auth', lambda: sync.sign_in(lambda code: self.events.put(('code', code, None)), self.cancel))

    def connect(self):
        if not self.consent.get():
            self.feedback.set('Allow usage sync above, or use Splitrail locally.')
            return
        # Capture widget state on the Tk thread before any background work.
        name = self.repo_var.get().strip()
        create = self.destination.get() == 'Create private repository' and not self.config
        scope = 'all' if self.scope.get() == 'All tools' else 'codex'
        automatic, receive = self.automatic.get(), self.receive_only.get()
        def connect():
            repository = sync.create_private_repo(name) if create else name
            # If repository creation succeeds but configuration fails, retain the destination
            # so Retry reuses it instead of creating another repository.
            if create:
                self.events.put(('created', repository, None))
            return sync.configure(repository, scope=scope, automatic=automatic, receive_only=receive)
        self.run('connect', connect)

    def sync_now(self):
        if self.app._sync_busy or self.app._usage_refreshing:
            self.feedback.set('Wait for the current refresh to finish, then sync.')
            return
        self.run('sync', sync.sync_now)

    def disconnect(self):
        self.run('disconnect', sync.disconnect)

    def poll(self):
        if not self.alive:
            return
        try:
            while True:
                action, value, error = self.events.get_nowait()
                if action == 'code':
                    self.code_label.pack(anchor='w', after=self.auth_button.master, pady=(9, 0))
                    self.code_var.set(f'Enter this one-time code on GitHub:  {value}')
                    self.open_auth.pack(anchor='w', after=self.code_label, pady=(5, 0))
                    continue
                if action == 'created':
                    self.repo_var.set(value)
                    self.destination.set('Use existing repository')
                    self.repo_hint.set('Private repository created · Ready to connect')
                    continue
                self.busy = False
                if error:
                    if action == 'auth':
                        self.code_label.pack_forget()
                        self.open_auth.pack_forget()
                    self.feedback.set(str(error) if isinstance(error, sync.SyncError) else 'Could not finish. Check your connection, source tools and settings, then retry.')
                elif action in ('auth', 'account'):
                    self.login = value
                    self.account_var.set(f'Connected as {value}')
                    self.code_var.set('')
                    self.code_label.pack_forget()
                    self.open_auth.pack_forget()
                    self.feedback.set('Choose a new private repository or reuse one from another device.')
                elif action == 'connect':
                    self.config = value
                    self.repo_var.set(value['repository'])
                    self.destination.set('Use existing repository')
                    self.connect_button.configure(text='Save connection')
                    self.feedback.set('Connected. Choose Sync now, then connect the same repository on your other devices.')
                    preferences.save(onboarded=True)
                    self.app.sync_config_changed()
                elif action == 'sync':
                    self.feedback.set(f"Up to date · {value['devices']} devices")
                    self.app.sync_config_changed()
                elif action == 'disconnect':
                    self.config = None
                    self.invalid_config = False
                    self.consent.set(False)
                    self.connect_button.configure(text='Connect')
                    self.feedback.set('Disconnected. Downloaded totals removed. Your private repository is unchanged.')
                    self.app.sync_config_changed()
                self.update_controls()
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self.poll)

    def _destroyed(self, event):
        if event.widget is self:
            self.cancel.set()
            self.alive = False
            if getattr(self, '_poll_id', None):
                self.after_cancel(self._poll_id)
                self._poll_id = None

    def close(self):
        if self.busy and self._last_action != 'auth':
            self.feedback.set('Finishing the current operation…')
            return
        self.cancel.set()
        self.alive = False
        self.destroy()
