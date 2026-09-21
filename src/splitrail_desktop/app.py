from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import date
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Callable

from .domain import (
    PeriodAggregate,
    SeriesPoint,
    UsageDataset,
    UsageTotal,
    aggregate_period,
    daily_data_rows,
    daily_series,
    period_for_preset,
)
from .quota import (
    QuotaSnapshot,
    format_banked_resets,
    format_countdown,
    format_local_reset,
    format_refresh_age,
    is_snapshot_stale,
    preserve_banked_resets,
)
from .refresh import AdaptiveRefreshPolicy
from .runner import (
    CostDiagnostics,
    LocalCommandError,
    QuotaCommandResult,
    StatsCommandResult,
    run_quota_axi,
    run_splitrail,
)


from .themes import THEMES

FONT = "DejaVu Sans"
MONO_FONT = "DejaVu Sans Mono"
globals().update(THEMES['Pearl'])
PRIMARY_PERIODS = (
    ("Current day", "Day"),
    ("Current week", "Week"),
    ("Current month", "Month"),
    ("Current year", "Year"),
    ("All time", "All time"),
)
TOP_LEVEL_SECTIONS = ("Overview", "Analyzers", "Models", "Quotas", "Data")


@dataclass(frozen=True)
class ChartBucket:
    label: str
    total: UsageTotal


class BorderedFrame(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(
            master,
            bg=CARD,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        # Corner masks retain ordinary Tk geometry/layout while softening the cards.
        surface = master.cget('background')
        for anchor, relx, rely, box in (
            ('nw', 0, 0, (0, 0, 24, 24)), ('ne', 1, 0, (-12, 0, 12, 24)),
            ('sw', 0, 1, (0, -12, 24, 12)), ('se', 1, 1, (-12, -12, 12, 12))):
            corner = tk.Canvas(self, width=12, height=12, bg=surface, highlightthickness=0, bd=0)
            corner.create_oval(*box, fill=CARD, outline='')
            corner.place(relx=relx, rely=rely, anchor=anchor, bordermode='outside')


class PeriodSummaryCard(BorderedFrame):
    def __init__(self, master: tk.Misc, title: str) -> None:
        super().__init__(master, padx=18, pady=14)
        self.title_var = tk.StringVar(value=title)
        self.range_var = tk.StringVar(value="—")
        self.cost_var = tk.StringVar(value="—")
        self.cost_caption_var = tk.StringVar(value="Estimated cost")
        self.detail_var = tk.StringVar(value="Waiting for local data")

        tk.Label(self, textvariable=self.title_var, bg=CARD, fg=INK, font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(self, textvariable=self.range_var, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=1, column=0, sticky="w", pady=(2, 8)
        )
        cost_row = tk.Frame(self, bg=CARD)
        cost_row.grid(row=2, column=0, sticky="w")
        tk.Label(cost_row, textvariable=self.cost_var, bg=CARD, fg=INK, font=(FONT, 23, "bold")).pack(
            side="left"
        )
        tk.Label(
            cost_row,
            textvariable=self.cost_caption_var,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
        ).pack(side="left", padx=(8, 0), pady=(9, 0))
        tk.Label(self, textvariable=self.detail_var, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=3, column=0, sticky="w", pady=(5, 0)
        )

    def set_values(
        self,
        start: date,
        end: date,
        aggregate: PeriodAggregate,
        cost_label: str,
        range_label: str | None = None,
    ) -> None:
        self.range_var.set(range_label or format_date_range(start, end))
        self.cost_var.set(format_currency(aggregate.total.cost))
        self.cost_caption_var.set(cost_label)
        self.detail_var.set(
            f"{format_compact(aggregate.total.tokens.total)} tokens  ·  "
            f"{format_integer(aggregate.total.conversations)} conversations"
        )


class QuotaCard(BorderedFrame):
    def __init__(self, master: tk.Misc, refresh_command: Callable[[], None]) -> None:
        super().__init__(master, padx=18, pady=14)
        self._refreshing = False
        self._refresh_failed = False
        self.status_var = tk.StringVar(value="Loading")
        self.used_var = tk.StringVar(value="— used")
        self.remaining_var = tk.StringVar(value="— remaining")
        self.reset_var = tk.StringVar(value="Reset date unavailable")
        self.countdown_var = tk.StringVar(value="")
        self.extra_var = tk.StringVar(value="Model & feature limits: loading")
        self.banked_var = tk.StringVar(value="Banked resets: checking support")

        top = tk.Frame(self, bg=CARD)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        tk.Label(top, text="Weekly quota", bg=CARD, fg=INK, font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.status_label = tk.Label(
            top,
            textvariable=self.status_var,
            bg=BLUE_SOFT,
            fg=BLUE_DARK,
            padx=7,
            pady=2,
            font=(FONT, 8, "bold"),
        )
        self.status_label.grid(row=0, column=1, sticky="e", padx=(8, 6))
        self.refresh_button = ttk.Button(top, text="↻", width=2, command=refresh_command, style="Quiet.TButton")
        self.refresh_button.grid(row=0, column=2, sticky="e")

        quota_row = tk.Frame(self, bg=CARD)
        quota_row.grid(row=1, column=0, sticky="ew", pady=(10, 4))
        quota_row.columnconfigure(1, weight=1)
        tk.Label(quota_row, textvariable=self.used_var, bg=CARD, fg=INK, font=(FONT, 18, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            quota_row, textvariable=self.remaining_var, bg=CARD, fg=TEAL, font=(FONT, 10, "bold")
        ).grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(self, style="Quota.Horizontal.TProgressbar", maximum=100, value=0)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(1, 7))

        reset_row = tk.Frame(self, bg=CARD)
        reset_row.grid(row=3, column=0, sticky="ew")
        reset_row.columnconfigure(0, weight=1)
        tk.Label(reset_row, textvariable=self.reset_var, bg=CARD, fg=MUTED, font=(FONT, 8)).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            reset_row, textvariable=self.countdown_var, bg=CARD, fg=INK, font=(MONO_FONT, 8, "bold")
        ).grid(row=0, column=1, sticky="e")

        tk.Frame(self, bg=BORDER, height=1).grid(row=4, column=0, sticky="ew", pady=(8, 6))
        tk.Label(self, textvariable=self.banked_var, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w", justify="left", wraplength=340).grid(
            row=6, column=0, sticky="w", pady=(2, 0)
        )
        self.columnconfigure(0, weight=1)

    def set_refreshing(self, refreshing: bool) -> None:
        self._refreshing = refreshing
        self.refresh_button.configure(state="disabled" if refreshing else "normal")
        if refreshing:
            self.status_var.set("Refreshing…")

    def set_error(self, message: str) -> None:
        self._refresh_failed = True
        self.status_var.set("Unavailable")
        self.status_label.configure(bg=RED_BG, fg=RED)
        self.used_var.set("Quota unavailable")
        self.remaining_var.set("")
        self.progress.configure(value=0)
        self.reset_var.set("See notifications for details")
        self.countdown_var.set("")
        self.extra_var.set("Model & feature limits unavailable")
        self.banked_var.set("Banked resets unavailable")

    def set_snapshot(self, snapshot: QuotaSnapshot) -> None:
        self._refresh_failed = False
        if snapshot.status == "auth_required":
            self.status_var.set("Sign-in required")
            self.status_label.configure(bg=AMBER_BG, fg=AMBER)
            self.used_var.set("Quota unavailable")
            self.remaining_var.set("")
            self.progress.configure(value=0)
            self.reset_var.set("Authenticated Codex quota was not available locally")
            self.countdown_var.set("")
        elif snapshot.status not in ("fresh", "stale"):
            self.status_var.set(snapshot.status.replace("_", " ").title())
            self.status_label.configure(bg=RED_BG, fg=RED)
            self.used_var.set("Quota unavailable")
            self.remaining_var.set("")
            self.progress.configure(value=0)
            self.reset_var.set("quota-axi did not return a usable weekly window")
            self.countdown_var.set("")
        else:
            self._update_refresh_age(snapshot)
            weekly = snapshot.base_weekly
            if weekly:
                self.used_var.set(format_percent(weekly.percent_used, "used"))
                self.remaining_var.set(format_percent(weekly.percent_remaining, "remaining"))
                self.progress.configure(value=weekly.percent_used or 0)
                self.reset_var.set(format_local_reset(weekly.resets_at))
                self.countdown_var.set(format_countdown(weekly.resets_at))
            else:
                self.used_var.set("Weekly window unavailable")
                self.remaining_var.set("")
                self.progress.configure(value=0)
                self.reset_var.set("No base weekly window was returned")
                self.countdown_var.set("")

        named_count = len(snapshot.named_windows)
        self.extra_var.set(
            f"Model & feature limits: {named_count} separate window{'s' if named_count != 1 else ''}"
            if named_count
            else "Model & feature limits: none reported"
        )
        banked = snapshot.banked_resets
        self.banked_var.set(format_banked_resets(banked) if banked.status == "fresh" else "Banked resets unavailable")

    def set_refresh_failed(self) -> None:
        self._refresh_failed = True
        self.status_var.set("Refresh failed")
        self.status_label.configure(bg=RED_BG, fg=RED)

    def _update_refresh_age(self, snapshot: QuotaSnapshot) -> None:
        if self._refreshing or self._refresh_failed or snapshot.status not in ("fresh", "stale"):
            return
        self.status_var.set(format_refresh_age(snapshot.refreshed_at or snapshot.generated_at))
        stale = is_snapshot_stale(snapshot)
        self.status_label.configure(bg=AMBER_BG if stale else GREEN_BG, fg=AMBER if stale else GREEN)

    def update_countdown(self, snapshot: QuotaSnapshot | None) -> None:
        if snapshot and snapshot.base_weekly and snapshot.status in ("fresh", "stale"):
            self.countdown_var.set(format_countdown(snapshot.base_weekly.resets_at))
        if snapshot:
            self._update_refresh_age(snapshot)
            self.banked_var.set(format_banked_resets(snapshot.banked_resets) if snapshot.banked_resets.status == "fresh" else "Banked resets unavailable")


class MetricCard(BorderedFrame):
    def __init__(self, master: tk.Misc, label: str) -> None:
        super().__init__(master, padx=14, pady=10)
        self.value_var = tk.StringVar(value="—")
        self.label_var = tk.StringVar(value=label.upper())
        tk.Label(self, textvariable=self.label_var, bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(self, textvariable=self.value_var, bg=CARD, fg=INK, font=(FONT, 16, "bold")).pack(
            anchor="w", pady=(4, 0)
        )


USAGE_MODES = {
    "combined": "All devices",
    "local": "This device",
    "codex": "Codex only",
}


class UsageChart(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=CARD, bd=0, highlightthickness=0, height=235)
        self.points: list[SeriesPoint] = []
        self.metric = "Total tokens"
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_data(self, points: list[SeriesPoint], metric: str) -> None:
        self.points = points
        self.metric = metric
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 180)
        left, right, top, bottom = 58, 18, 18, 40
        chart_width = width - left - right
        chart_height = height - top - bottom
        if not self.points:
            self.create_text(width / 2, height / 2, text="No usage in this period", fill=MUTED, font=(FONT, 11))
            return

        buckets = build_chart_buckets(self.points)
        values = [chart_value(bucket.total, self.metric) for bucket in buckets]
        max_value = max(values, default=0)
        if max_value <= 0:
            self.create_text(width / 2, height / 2, text=f"No {self.metric.lower()} in this period", fill=MUTED, font=(FONT, 11))
            return
        ceiling = nice_ceiling(max_value)
        for tick in range(5):
            fraction = tick / 4
            y = top + chart_height * (1 - fraction)
            value = ceiling * fraction
            self.create_line(left, y, width - right, y, fill=BORDER, width=1)
            self.create_text(left - 8, y, text=format_axis(value, self.metric), fill=MUTED, anchor="e", font=(FONT, 8))

        slot = chart_width / len(buckets)
        bar_width = max(2, min(24, slot * 0.68))
        for index, (bucket, value) in enumerate(zip(buckets, values)):
            x = left + slot * index + slot / 2
            bar_height = chart_height * value / ceiling
            color = TEAL if value == max_value else BLUE
            self.create_rectangle(x - bar_width / 2, top + chart_height - bar_height, x + bar_width / 2, top + chart_height, fill=color, outline="")
        label_step = max(1, math.ceil(len(buckets) / 6))
        for index, bucket in enumerate(buckets):
            if index % label_step != 0 and index != len(buckets) - 1:
                continue
            x = left + slot * index + slot / 2
            self.create_text(x, height - 17, text=bucket.label, fill=MUTED, font=(FONT, 8))


class SplitrailApp(tk.Tk):
    def __init__(self, auto_refresh: bool = True, codex_usage: bool = False) -> None:
        super().__init__()
        self.title("Splitrail Desktop")
        self.geometry("1240x880")
        self.minsize(1050, 700)
        from .preferences import load
        from .themes import system_fonts
        global FONT, MONO_FONT
        FONT, MONO_FONT = system_fonts(self)
        prefs = load()
        self.theme_name = prefs.get('theme', 'Pearl')
        if self.theme_name not in THEMES:
            self.theme_name = 'Pearl'
        globals().update(THEMES[self.theme_name])
        self.configure(bg=BG)
        self._demo_mode = False
        self._dataset: UsageDataset | None = None
        self._usage_result: StatsCommandResult | None = None
        self._quota_snapshot: QuotaSnapshot | None = None
        self._usage_refreshing = False
        self._quota_refreshing = False
        self._adaptive_refresh = AdaptiveRefreshPolicy()
        self._auto_refresh_enabled = auto_refresh
        self._auto_refresh_after_id: str | None = None
        self._auto_refresh_generation = 0
        self._closed = False
        self._results: queue.SimpleQueue[tuple[str, object, Exception | None]] = queue.SimpleQueue()
        self._selected_aggregate: PeriodAggregate | None = None
        self._details: tuple[str, ...] = ()
        self._period_names = tuple(item[0] for item in PRIMARY_PERIODS)
        self._period_presets = tuple(item[1] for item in PRIMARY_PERIODS)
        self._period_index = 2
        self._notifications: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        self._settings_dialog = None
        self._custom_range: tuple[date, date] | None = None
        self._pricing_refresh_pending = False
        from .sync import safe_settings as settings
        try:
            config = settings()
        except (ValueError, OSError):
            config = None
        import shutil, os
        from .runner import SPLITRAIL_FALLBACK
        collector_available = bool(os.environ.get('SPLITRAIL_BIN') or shutil.which('splitrail') or SPLITRAIL_FALLBACK.exists())
        self._usage_mode = "codex" if codex_usage or not collector_available else "combined"
        self._displayed_usage_mode = self._usage_mode
        self._transfer_busy = False
        self._sync_busy = False
        self._configure_styles()
        self._build_ui()
        from .themes import match_button_surfaces
        match_button_surfaces(self, CARD)
        self.after(100, self._poll_results)
        self.after(1000, self._tick_countdowns)
        if auto_refresh:
            self._schedule_auto_refresh(120)
        self.protocol("WM_DELETE_WINDOW", self._close)
        if auto_refresh and not prefs.get('onboarded'):
            self.after(500, self._open_welcome)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, troughcolor=BG,
                        selectbackground=BLUE_DARK, selectforeground=ON_ACCENT)
        style.configure("TButton", font=(FONT, 9, "bold"), padding=(14, 9), borderwidth=0, relief="flat", background=BLUE_SOFT, foreground=INK, lightcolor=BLUE_SOFT, darkcolor=BLUE_SOFT, bordercolor=BLUE_SOFT, focusthickness=1, focuscolor=BLUE)
        style.configure("Primary.TButton", background=BLUE, foreground=ON_ACCENT)
        style.map("Primary.TButton", background=[("active", BLUE_DARK), ("focus", BLUE_DARK), ("disabled", BORDER)])
        style.configure("Quiet.TButton", background=BLUE_SOFT, foreground=BLUE, padding=(14, 9))
        style.map("Quiet.TButton", background=[("active", BORDER), ("focus", BORDER)], foreground=[("disabled", "#7D8798"), ("active", INK), ("focus", INK)])
        style.configure("Quiet.TMenubutton", font=(FONT, 9, "bold"), padding=(14, 9),
                        background=BLUE_SOFT, foreground=INK, relief="flat", borderwidth=0,
                        arrowcolor=MUTED)
        style.map("Quiet.TMenubutton", background=[("active", BORDER)])
        style.configure("Alert.TButton", background=BLUE_SOFT, foreground=AMBER)
        style.configure("Period.TCombobox", padding=7, font=(FONT, 10, "bold"))
        style.configure("TCombobox", padding=6, fieldbackground=BG, background=BG, foreground=INK, arrowcolor=MUTED, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", BG)], foreground=[("readonly", INK)], selectbackground=[("readonly", BG)], selectforeground=[("readonly", INK)])
        style.configure('TCheckbutton', background=CARD, foreground=INK, font=(FONT, 9), padding=(0, 2))
        style.configure('TRadiobutton', background=CARD, foreground=INK, font=(FONT, 10), padding=(0, 2))
        style.map('TCheckbutton', background=[('active', CARD)], foreground=[('disabled', MUTED)])
        style.map('TRadiobutton', background=[('active', CARD)])
        style.configure("TEntry", padding=6, fieldbackground=BG, foreground=INK, insertcolor=INK)
        style.configure("TScrollbar", background=CARD, troughcolor=BG, arrowcolor=MUTED, borderwidth=0)
        style.configure("Quota.Horizontal.TProgressbar", troughcolor=BG, background=TEAL, borderwidth=0, thickness=7, lightcolor=TEAL, darkcolor=TEAL, bordercolor=BG)
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK, rowheight=36, borderwidth=0, font=(FONT, 9))
        style.configure("Treeview.Heading", background=BLUE_SOFT, foreground=INK, font=(FONT, 8, "bold"), relief="flat", padding=(10, 10), borderwidth=0, lightcolor=BLUE_SOFT, darkcolor=BLUE_SOFT, bordercolor=BLUE_SOFT)
        style.map("Treeview", background=[("selected", BLUE_DARK)], foreground=[("selected", ON_ACCENT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(20, 11), borderwidth=0, lightcolor=BG, darkcolor=BG, bordercolor=BG, font=(FONT, 9, "bold"))
        style.layout("TNotebook.Tab", [("Notebook.padding", {"sticky": "nswe", "children":
                     [("Notebook.label", {"sticky": "nswe"})]})])
        style.map("TNotebook.Tab", padding=[("selected", (20, 11))], lightcolor=[("selected", BG)], background=[("selected", CARD), ("active", BLUE_SOFT)], foreground=[("selected", BLUE), ("active", INK)])

        from .themes import style_buttons
        style_buttons(self, style, THEMES[self.theme_name])
        style.configure('Quiet.TMenubutton', padding=(16, 10))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        shell = tk.Frame(self, bg=BG, padx=22, pady=15)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1)

        self._build_header(shell).grid(row=0, column=0, sticky="ew", pady=(0, 11))
        self._build_notice(shell)
        self._build_hero(shell).grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self._build_content(shell).grid(row=4, column=0, sticky="nsew")
        self.bind_all("<Alt-Left>", lambda _event: self._cycle_period(-1))
        self.bind_all("<Alt-Right>", lambda _event: self._cycle_period(1))

    def _build_header(self, master: tk.Misc) -> tk.Frame:
        frame = tk.Frame(master, bg=BG)
        frame.columnconfigure(1, weight=1)
        logo = tk.Canvas(frame, width=42, height=42, bg=BG, highlightthickness=0)
        logo.grid(row=0, column=0, padx=(0, 11))
        logo.create_oval(2, 2, 40, 40, fill=BLUE, outline="")
        logo.create_line(14, 10, 14, 32, fill=BG, width=3)
        logo.create_line(27, 10, 27, 32, fill=BG, width=3)
        logo.create_line(14, 15, 27, 15, fill=BG, width=2)
        logo.create_line(14, 27, 27, 27, fill=BG, width=2)
        tk.Label(frame, text="Splitrail", bg=BG, fg=INK, font=(FONT, 20, "bold")).grid(row=0, column=1, sticky="w")
        self.source_status_var = tk.StringVar(value="Loading usage…")
        self.refresh_all_button = ttk.Button(frame, text="↻  Refresh", command=self.refresh_all, style="Primary.TButton")
        self.refresh_all_button.grid(row=0, column=2, padx=(8, 0))
        menu = tk.Menu(self, tearoff=False, bg=CARD, fg=INK, activebackground=BLUE_SOFT, activeforeground=BLUE)
        self._usage_mode_var = tk.StringVar(value=self._usage_mode)
        for mode, label in USAGE_MODES.items():
            menu.add_radiobutton(label=label, value=mode, variable=self._usage_mode_var, command=self._select_usage_mode)
        menu.add_separator()
        menu.add_command(label="Export usage…", command=self._export_usage)
        menu.add_command(label="Import usage…", command=self._import_usage)
        menu.add_command(label="Remove imported usage", command=self._remove_imports)
        self.transfer_button = ttk.Menubutton(frame, text="Usage", menu=menu, style="Quiet.TMenubutton")
        self.transfer_button.grid(row=0, column=3, padx=(8, 0))
        from .sync import status_text, safe_settings as settings
        self.sync_button = ttk.Button(frame, text="Sync" if settings() else "Connect GitHub", command=self._sync_usage, style="Quiet.TButton")
        self.sync_button.grid(row=0, column=4, padx=(8, 0))
        self.sync_status_var = tk.StringVar(value=status_text())
        self.notification_button = ttk.Button(frame, text="Notifications", command=self._show_details, style="Quiet.TButton")
        self.notification_button.grid(row=0, column=5, padx=(8, 0))
        self.settings_button = ttk.Button(frame, text="Settings", command=self._open_settings, style="Quiet.TButton")
        self.settings_button.grid(row=0, column=6, padx=(8, 0))
        return frame

    def _sync_usage(self) -> None:
        if self._demo_mode:
            self._show_notice("Sync is unavailable in demo mode.", "info")
            return
        if self._sync_busy or self._usage_refreshing or self._transfer_busy:
            return
        from .sync import safe_settings as settings
        if not settings():
            self._open_settings(tab='sync')
            return
        self._sync_busy = True
        self.sync_status_var.set("Syncing devices…")
        self._sync_refresh_controls()
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _sync_worker(self) -> None:
        from .sync import sync_now
        try:
            self._results.put(("sync", sync_now(), None))
        except Exception as exc:
            self._results.put(("sync", None, exc))

    @property
    def _codex_mode(self) -> bool:
        return self._displayed_usage_mode == "codex"

    def _select_usage_mode(self) -> None:
        self._usage_mode = self._usage_mode_var.get()
        if not self._usage_refreshing:
            self.refresh_usage()

    def _export_usage(self) -> None:
        if self._transfer_busy:
            return
        filename = filedialog.asksaveasfilename(parent=self, title="Export this computer's Codex usage",
                                               initialfile="codex-usage.json.gz", defaultextension=".json.gz",
                                               filetypes=[("Codex usage", "*.json.gz"), ("JSON", "*.json")])
        if filename:
            self._transfer_busy = True
            self._show_notice("Scanning local Codex usage for export…", "info")
            threading.Thread(target=self._transfer_worker, args=("export", filename), daemon=True).start()

    def _import_usage(self) -> None:
        if self._transfer_busy:
            return
        filename = filedialog.askopenfilename(parent=self, title="Import Codex usage",
                                              filetypes=[("Codex usage", "*.json.gz *.json"), ("All files", "*")])
        if filename:
            self._transfer_busy = True
            self._show_notice("Merging Codex usage…", "info")
            threading.Thread(target=self._transfer_worker, args=("import", filename), daemon=True).start()

    def _transfer_worker(self, action: str, filename: str) -> None:
        if self._demo_mode:
            self._results.put(("transfer", None, ValueError("Transfers are unavailable in demo mode.")))
            return
        from .portable import import_export, scan_codex, write_export
        try:
            if action == "export":
                payload = scan_codex()
                write_export(Path(filename), payload)
                message = f"Exported {len(payload['events']):,} unique requests to {filename}"
            else:
                added = import_export(Path(filename))
                message = f"Imported {added:,} new requests. Repeated requests were counted once."
            self._results.put(("transfer", (action, message), None))
        except Exception as exc:
            self._results.put(("transfer", None, exc))

    def _remove_imports(self) -> None:
        from .portable import data_dir
        if self._transfer_busy:
            return
        if messagebox.askyesno("Remove imported usage", "Remove the local copy of imported usage? Your original exports and Codex logs are kept.", parent=self):
            (data_dir() / "imported-codex-usage.json.gz").unlink(missing_ok=True)
            self.refresh_usage()

    def _build_notice(self, master: tk.Misc) -> None:
        self.notice_var = tk.StringVar()

    def _open_settings(self, tab='general') -> None:
        from .onboarding import SettingsWindow
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            self._settings_dialog.select(tab)
            return
        self._settings_dialog = SettingsWindow(self, tab=tab)

    def _open_pricing(self) -> None:
        from .settings_ui import PricingSettings
        unknown = self._usage_result.cost_diagnostics.unknown_models if self._usage_result else ()
        PricingSettings(self, unknown, self._pricing_changed)

    def _open_welcome(self) -> None:
        from .onboarding import WelcomeWindow
        WelcomeWindow(self)

    def set_theme(self, name: str) -> None:
        from .preferences import save
        from .themes import recolor
        if name not in THEMES:
            return
        previous = THEMES[self.theme_name]
        self.theme_name = name
        save(theme=name)
        globals().update(THEMES[name])
        recolor(self, previous, THEMES[name])
        self._configure_styles()
        from .themes import match_button_surfaces
        match_button_surfaces(self, CARD)
        if self._quota_snapshot:
            self.quota_card.set_snapshot(self._quota_snapshot)

    def sync_config_changed(self) -> None:
        from .sync import safe_settings as settings, status_text
        self.sync_button.configure(text='Sync' if settings() else 'Connect GitHub')
        self.sync_status_var.set(status_text())
        self._pricing_changed()

    def _pricing_changed(self) -> None:
        if self._usage_refreshing or self._sync_busy:
            self._pricing_refresh_pending = True
        else:
            self.refresh_usage()

    def _build_hero(self, master: tk.Misc) -> tk.Frame:
        frame = tk.Frame(master, bg=BG)
        frame.columnconfigure(0, weight=3, uniform="hero")
        frame.columnconfigure(1, weight=2, uniform="hero")
        self.period_card = PeriodSummaryCard(frame, "Current month")
        for child in self.period_card.grid_slaves(row=0, column=0):
            child.grid_remove()
        self.period_card.columnconfigure(0, weight=1)
        self._build_controls(self.period_card).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.period_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.quota_card = QuotaCard(frame, self.refresh_quota)
        self.quota_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        return frame

    def _build_controls(self, master: tk.Misc) -> tk.Frame:
        frame = tk.Frame(master, bg=CARD)
        frame.columnconfigure(0, weight=1)
        self.active_period_var = tk.StringVar(value="CURRENT MONTH")
        self.selected_period_var = tk.StringVar(value="")
        self.period_choice_var = tk.StringVar(value="This month")
        self.period_choices = ("Today", "This week", "This month", "This year", "All time")
        self.period_selector = ttk.Combobox(frame, textvariable=self.period_choice_var,
                                           values=(*self.period_choices, "Custom dates…"),
                                           width=15, state="readonly", style="Period.TCombobox")
        self.period_selector.grid(row=0, column=0, sticky="w")
        self.period_selector.bind("<<ComboboxSelected>>", self._choose_period)
        ttk.Button(frame, text="‹", width=2, command=lambda: self._cycle_period(-1), style="Quiet.TButton").grid(row=0, column=1, padx=(8, 4))
        ttk.Button(frame, text="›", width=2, command=lambda: self._cycle_period(1), style="Quiet.TButton").grid(row=0, column=2)
        self.custom_dates_button = ttk.Button(frame, text="Dates…", command=self._open_custom_dates, style="Quiet.TButton")
        self.custom_dates_button.grid(row=0, column=3, padx=(8, 0))
        self.custom_dates_button.configure(state="disabled")
        return frame

    def _choose_period(self, _event=None) -> None:
        choice = self.period_choice_var.get()
        if choice == "Custom dates…":
            self.period_choice_var.set("Custom range" if self._custom_range else self.period_choices[self._period_index])
            self._open_custom_dates()
        else:
            self._period_index = self.period_choices.index(choice)
            self._select_primary_period()

    def _build_metrics(self, master: tk.Misc) -> tk.Frame:
        frame = tk.Frame(master, bg=BG)
        labels = ("Est. cost", "Total tokens", "Messages", "Conversations", "Tool calls")
        self.metric_cards: list[MetricCard] = []
        for index, label in enumerate(labels):
            # Keep each value's natural width, then share the remaining space.
            frame.columnconfigure(index, weight=1)
            card = MetricCard(frame, label)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 0 if index == len(labels) - 1 else 5))
            self.metric_cards.append(card)
        return frame

    def _build_visuals(self, master: tk.Misc) -> tk.Frame:
        frame = tk.Frame(master, bg=BG)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(0, weight=1)
        chart_card = BorderedFrame(frame, padx=14, pady=10)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        chart_card.columnconfigure(0, weight=1)
        chart_card.rowconfigure(1, weight=1)
        chart_head = tk.Frame(chart_card, bg=CARD)
        chart_head.grid(row=0, column=0, sticky="ew")
        chart_head.columnconfigure(0, weight=1)
        self.chart_title_var = tk.StringVar(value="Usage · daily")
        tk.Label(chart_head, textvariable=self.chart_title_var, bg=CARD, fg=INK, font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w")
        self.chart_metric_var = tk.StringVar(value="Total tokens")
        chart_metric = ttk.Combobox(chart_head, textvariable=self.chart_metric_var, values=("Total tokens", "Estimated cost (USD)", "Conversations", "Messages"), width=20, state="readonly")
        chart_metric.grid(row=0, column=1, sticky="e")
        chart_metric.bind("<<ComboboxSelected>>", lambda _event: self._update_chart())
        self.chart = UsageChart(chart_card)
        self.chart.grid(row=1, column=0, sticky="nsew", pady=(4, 0))

        token_card = BorderedFrame(frame, padx=16, pady=11)
        token_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        token_card.columnconfigure(1, weight=1)
        self.token_title_var = tk.StringVar(value="Tokens")
        tk.Label(token_card, textvariable=self.token_title_var, bg=CARD, fg=INK, font=(FONT, 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.token_vars: list[tk.StringVar] = []
        for index, label in enumerate(("Input", "Output", "Cached total", "Reasoning")):
            tk.Label(token_card, text="●", bg=CARD, fg=TOKEN_COLORS[index], font=(FONT, 11)).grid(row=index + 1, column=0, sticky="w")
            tk.Label(token_card, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=index + 1, column=1, sticky="w")
            value_var = tk.StringVar(value="—")
            self.token_vars.append(value_var)
            tk.Label(token_card, textvariable=value_var, bg=CARD, fg=INK, font=(MONO_FONT, 9, "bold")).grid(row=index + 1, column=2, sticky="e")
        tk.Frame(token_card, bg=BORDER, height=1).grid(row=5, column=0, columnspan=3, sticky="ew", pady=7)
        self.cache_detail_var = tk.StringVar(value="")
        tk.Label(token_card, textvariable=self.cache_detail_var, bg=CARD, fg=MUTED, justify="left", anchor="w", wraplength=380, font=(FONT, 8)).grid(row=6, column=0, columnspan=3, sticky="ew")
        self.message_mix_var = tk.StringVar(value="")
        tk.Label(token_card, textvariable=self.message_mix_var, bg=CARD, fg=MUTED, font=(FONT, 8), wraplength=380, justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(7, 0))
        return frame

    def _build_content(self, master: tk.Misc) -> ttk.Notebook:
        notebook = ttk.Notebook(master)
        overview_tab = tk.Frame(notebook, bg=BG, padx=1, pady=1)
        analyzer_tab = tk.Frame(notebook, bg=CARD)
        model_tab = tk.Frame(notebook, bg=CARD)
        quota_tab = tk.Frame(notebook, bg=CARD)
        data_tab = tk.Frame(notebook, bg=CARD)
        self.notebook = notebook
        for tab, name in zip((overview_tab, analyzer_tab, model_tab, quota_tab, data_tab), TOP_LEVEL_SECTIONS):
            notebook.add(tab, text=name)

        overview_tab.columnconfigure(0, weight=1)
        overview_tab.rowconfigure(1, weight=1)
        self._build_metrics(overview_tab).grid(row=0, column=0, sticky="ew", pady=(9, 10))
        self._build_visuals(overview_tab).grid(row=1, column=0, sticky="nsew", pady=(0, 1))

        self.analyzer_tree = self._make_tree(analyzer_tab, ("analyzer", "cost", "tokens", "conversations", "user", "ai"), (260, 125, 140, 130, 110, 110))
        for key, title in zip(("analyzer", "cost", "tokens", "conversations", "user", "ai"), ("ANALYZER", "EST. COST", "TOTAL TOKENS", "CONVERSATIONS", "USER MSGS", "AI MSGS")):
            self.analyzer_tree.heading(key, text=title)
        self._tree_with_scrollbars(analyzer_tab, self.analyzer_tree)

        self.model_tree = self._make_tree(model_tab, ("model", "messages", "input", "output", "read", "write", "cost", "coverage"), (220, 95, 110, 110, 110, 110, 105, 170))
        for key, title in zip(("model", "messages", "input", "output", "read", "write", "cost", "coverage"), ("MODEL", "MESSAGES", "INPUT", "OUTPUT", "CACHE READ", "CACHE WRITE", "EST. USD", "COVERAGE")):
            self.model_tree.heading(key, text=title)
        self._tree_with_scrollbars(model_tab, self.model_tree)

        self.quota_tree = self._make_tree(quota_tab, ("name", "kind", "used", "remaining", "reset", "countdown"), (270, 100, 100, 115, 235, 130))
        for key, title in zip(("name", "kind", "used", "remaining", "reset", "countdown"), ("QUOTA WINDOW", "TYPE", "USED", "REMAINING", "LOCAL RESET", "COUNTDOWN")):
            self.quota_tree.heading(key, text=title)
        self._tree_with_scrollbars(quota_tab, self.quota_tree)

        data_columns = ("date", "cost", "cache", "input", "output", "reasoning", "conversations", "tools", "apps", "models")
        data_widths = (155, 110, 145, 140, 145, 170, 150, 115, 145, 350)
        self.data_tree = self._make_tree(data_tab, data_columns, data_widths)
        for key, width in zip(data_columns, data_widths):
            self.data_tree.column(key, width=width, minwidth=width, stretch=False)
        for key, title in zip(data_columns, ("DATE", "EST. COST", "CACHE TOKENS", "INPUT TOKENS", "OUTPUT TOKENS", "REASONING TOKENS", "CONVERSATIONS", "TOOL CALLS", "APPS", "MODELS")):
            self.data_tree.heading(key, text=title)
        self._tree_with_scrollbars(data_tab, self.data_tree)
        return notebook

    def _make_tree(self, master: tk.Misc, columns: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        tree = ttk.Treeview(master, columns=columns, show="headings", height=4)
        for key, width in zip(columns, widths):
            tree.column(key, width=width, minwidth=70, anchor="w" if key in ("analyzer", "model", "name", "coverage", "date", "apps") else "e", stretch=True)
        return tree

    def _tree_with_scrollbars(self, master: tk.Frame, tree: ttk.Treeview) -> None:
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(master, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        scroll = ttk.Scrollbar(master, orient="horizontal", command=tree.xview)
        scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(xscrollcommand=scroll.set, yscrollcommand=vertical.set)

    def refresh_all(self, *, _automatic: bool = False) -> None:
        """Start one full refresh and replace any pending automatic timer."""

        if not _automatic:
            self._cancel_auto_refresh()
        if self._usage_refreshing or self._quota_refreshing:
            # A direct/manual call can arrive while a worker is finishing. Do
            # not overlap either operation, and ensure the cadence is not lost.
            if self._auto_refresh_enabled:
                self._schedule_auto_refresh(self._adaptive_refresh.interval_seconds * 1000)
            return
        self.refresh_usage()
        self.refresh_quota()

    def refresh_usage(self) -> None:
        if self._demo_mode:
            return
        if self._usage_refreshing or self._sync_busy:
            return
        self._usage_refreshing = True
        self._loading_usage_mode = self._usage_mode
        self._sync_refresh_controls()
        if self._dataset is None:
            self._show_notice(f"Loading {USAGE_MODES[self._usage_mode]}…", "info")
        threading.Thread(target=self._usage_worker, name="splitrail-refresh", daemon=True).start()

    def refresh_quota(self) -> None:
        if self._demo_mode:
            return
        if self._quota_refreshing:
            return
        self._quota_refreshing = True
        self.quota_card.set_refreshing(True)
        self._sync_refresh_controls()
        threading.Thread(target=self._quota_worker, name="quota-refresh", daemon=True).start()

    def _schedule_auto_refresh(self, delay_ms: int) -> None:
        if not self._auto_refresh_enabled or self._closed:
            return
        self._cancel_auto_refresh()
        generation = self._auto_refresh_generation
        self._auto_refresh_after_id = self.after(
            max(1, int(delay_ms)),
            lambda generation=generation: self._auto_refresh_callback(generation),
        )

    def _cancel_auto_refresh(self) -> None:
        self._auto_refresh_generation += 1
        after_id = self._auto_refresh_after_id
        self._auto_refresh_after_id = None
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                # The callback may already be dequeued. Its generation check
                # still suppresses stale work.
                pass

    def _auto_refresh_callback(self, generation: int) -> None:
        if self._closed or generation != self._auto_refresh_generation:
            return
        self._auto_refresh_after_id = None
        if self._usage_refreshing or self._quota_refreshing:
            # Keep one future attempt when a timer fires during a worker.
            self._schedule_auto_refresh(self._adaptive_refresh.interval_seconds * 1000)
            return
        self.refresh_all(_automatic=True)

    def _usage_worker(self) -> None:
        try:
            from .portable import run_portable_usage
            from .combined import run_combined_usage
            result = {"codex": run_portable_usage, "local": run_splitrail,
                      "combined": run_combined_usage}[self._loading_usage_mode]()
        except Exception as exc:
            self._results.put(("usage", None, exc))
        else:
            self._results.put(("usage", result, None))

    def _quota_worker(self) -> None:
        try:
            result = run_quota_axi()
        except Exception as exc:
            self._results.put(("quota", None, exc))
        else:
            self._results.put(("quota", result, None))

    def _poll_results(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, result, error = self._results.get_nowait()
                if kind == "usage":
                    self._finish_usage(result, error)
                elif kind == "sync":
                    self._sync_busy = False
                    if error:
                        self.sync_status_var.set(f"Sync failed: {error}")
                        self._show_notice(f"Sync failed: {error}", "error", source="sync")
                        self._sync_refresh_controls()
                    else:
                        self._hide_notice("sync")
                        self.refresh_usage()
                elif kind == "transfer":
                    self._transfer_busy = False
                    if error:
                        messagebox.showerror("Usage transfer failed", str(error), parent=self)
                    else:
                        action, message = result
                        messagebox.showinfo("Usage transfer complete", message, parent=self)
                        if action == "import":
                            if self._usage_mode == "local":
                                self._usage_mode = "combined"
                                self._usage_mode_var.set("combined")
                            self.refresh_usage()
                else:
                    self._finish_quota(result, error)
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    def _finish_usage(self, result: object, error: Exception | None) -> None:
        self._usage_refreshing = False
        if self._loading_usage_mode != self._usage_mode:
            self.refresh_usage()
            return
        if error:
            self._adaptive_refresh.record_failure()
            message = str(error) if isinstance(error, LocalCommandError) else f"Unexpected usage error: {error}"
            self._show_notice(
                (f"Usage refresh failed; showing the previous {USAGE_MODES[self._displayed_usage_mode]} snapshot. " if self._dataset else "Usage unavailable. ") + message,
                "error",
            )
            if self._auto_refresh_enabled:
                self._schedule_auto_refresh(self._adaptive_refresh.interval_seconds * 1000)
        else:
            assert isinstance(result, StatsCommandResult)
            decision = self._adaptive_refresh.record_success(result.dataset)
            self._displayed_usage_mode = self._loading_usage_mode
            self._usage_result = result
            self._dataset = result.dataset
            self.custom_dates_button.configure(state="normal")
            if self._custom_range:
                self._render_period(*self._custom_range)
            else:
                self._select_primary_period()
            self._show_cost_diagnostics(result.cost_diagnostics)
            if self._auto_refresh_enabled:
                self._schedule_auto_refresh(decision.interval_seconds * 1000)
        self._sync_refresh_controls()
        self._update_source_status()
        if self._pricing_refresh_pending:
            self._pricing_refresh_pending = False
            self.refresh_usage()

    def _finish_quota(self, result: object, error: Exception | None) -> None:
        self._quota_refreshing = False
        self.quota_card.set_refreshing(False)
        if error:
            self._show_notice(f"Quota refresh failed: {error}", "error", source="quota")
            if self._quota_snapshot:
                self.quota_card.set_snapshot(self._quota_snapshot)
                self.quota_card.set_refresh_failed()
            else:
                message = str(error) if isinstance(error, LocalCommandError) else "Could not read quota-axi data"
                self.quota_card.set_error(message[:120])
        else:
            self._hide_notice("quota")
            assert isinstance(result, QuotaCommandResult)
            snapshot = preserve_banked_resets(result.snapshot, self._quota_snapshot)
            self._quota_snapshot = snapshot
            self.quota_card.set_snapshot(snapshot)
            self._populate_quota_tree(snapshot)
        self._sync_refresh_controls()
        self._update_source_status()

    def _sync_refresh_controls(self) -> None:
        busy = self._usage_refreshing or self._quota_refreshing or self._sync_busy
        self.refresh_all_button.configure(state="disabled" if busy else "normal")
        self.sync_button.configure(state="disabled" if self._usage_refreshing or self._sync_busy or self._transfer_busy else "normal")

    def _update_source_status(self) -> None:
        usage = "usage loading" if self._usage_refreshing else ("usage ready" if self._dataset else "usage unavailable")
        quota = "quota loading" if self._quota_refreshing else ("quota ready" if self._quota_snapshot else "quota unavailable")
        mode = USAGE_MODES[self._displayed_usage_mode]
        if self._usage_mode != self._displayed_usage_mode:
            mode += f" (requested {USAGE_MODES[self._usage_mode]})"
        from .sync import safe_settings as settings, status_text
        transport = "private GitHub sync" if settings() else "local only"
        self.source_status_var.set(f"{mode}  ·  {usage}  ·  {quota}  ·  {transport}")
        if not self._sync_busy:
            self.sync_status_var.set(status_text())

    def _close(self) -> None:
        self._closed = True
        self._cancel_auto_refresh()
        self.destroy()

    def _cycle_period(self, direction: int) -> None:
        self._period_index = cycle_period_index(self._period_index, direction, len(self._period_names))
        self._select_primary_period()

    def _select_primary_period(self) -> None:
        self._custom_range = None
        self.period_choice_var.set(self.period_choices[self._period_index])
        self.active_period_var.set(self._period_names[self._period_index].upper())
        if not self._dataset:
            return
        start, end = period_for_preset(self._period_presets[self._period_index], date.today(), self._dataset)
        self._render_period(start, end)

    def _open_custom_dates(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Custom date range")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        panel = BorderedFrame(dialog, padx=18, pady=16)
        panel.pack(padx=16, pady=16)
        tk.Label(panel, text="Custom date range", bg=CARD, fg=INK, font=(FONT, 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(panel, text="Use calendar dates in YYYY-MM-DD format.", bg=CARD, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))
        current = self._selected_aggregate
        start_var = tk.StringVar(value=(current.start if current else date.today()).isoformat())
        end_var = tk.StringVar(value=(current.end if current else date.today()).isoformat())
        tk.Label(panel, text="FROM", bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).grid(row=2, column=0, sticky="w")
        tk.Label(panel, text="TO", bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).grid(row=2, column=1, sticky="w", padx=(10, 0))
        start_entry = ttk.Entry(panel, textvariable=start_var, width=15)
        start_entry.grid(row=3, column=0, sticky="ew", pady=(4, 12))
        ttk.Entry(panel, textvariable=end_var, width=15).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(4, 12))
        ttk.Button(panel, text="Cancel", command=dialog.destroy, style="Quiet.TButton").grid(row=4, column=0, sticky="e")
        ttk.Button(panel, text="Apply range", command=lambda: self._apply_custom_dates(start_var, end_var, dialog), style="Primary.TButton").grid(row=4, column=1, sticky="e", padx=(10, 0))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Return>", lambda _event: self._apply_custom_dates(start_var, end_var, dialog))
        dialog.grab_set()
        start_entry.focus_set()

    def _apply_custom_dates(self, start_var: tk.StringVar, end_var: tk.StringVar, dialog: tk.Toplevel) -> None:
        if not self._dataset:
            return
        try:
            start = date.fromisoformat(start_var.get().strip())
            end = date.fromisoformat(end_var.get().strip())
            if end < start:
                raise ValueError("end date is before start date")
        except ValueError as exc:
            self._show_notice(f"Date range is invalid: {exc}. Use YYYY-MM-DD.", "error")
            return
        dialog.destroy()
        self._custom_range = (start, end)
        self.period_choice_var.set("Custom range")
        self.active_period_var.set("CUSTOM RANGE")
        self._render_period(start, end)

    def _render_period(self, start: date, end: date) -> None:
        if not self._dataset:
            return
        aggregate = aggregate_period(self._dataset, start, end)
        self._selected_aggregate = aggregate
        period_name = self.active_period_var.get().capitalize()
        has_usage = any(start <= item.day <= end for item in self._dataset.days)
        range_label = format_date_range(start, end)
        if period_name == "All time" and not has_usage:
            range_label += " · No usage in range"
        self.selected_period_var.set(range_label)
        cost_label = self._cost_label()
        self.period_card.title_var.set(period_name)
        self.period_card.set_values(start, end, aggregate, cost_label, range_label)
        self.token_title_var.set("Tokens")
        self.metric_cards[2].label_var.set("REQUESTS" if self._codex_mode else "MESSAGES")
        self.model_tree.heading("messages", text="REQUESTS" if self._codex_mode else "MESSAGES")
        self.analyzer_tree.heading("ai", text="REQUESTS" if self._codex_mode else "AI MSGS")
        if self._displayed_usage_mode == "combined":
            self.metric_cards[4].label_var.set("LOCAL TOOL CALLS")
        else:
            self.metric_cards[4].label_var.set("TOOL CALLS")

        values = (
            format_currency(aggregate.total.cost),
            format_integer(aggregate.total.tokens.total),
            format_integer(aggregate.total.messages),
            format_integer(aggregate.total.conversations),
            "Not collected" if self._codex_mode else format_integer(aggregate.total.tool_calls),
        )
        for card, value in zip(self.metric_cards, values):
            card.value_var.set(value)
        tokens = aggregate.total.tokens
        for variable, value in zip(self.token_vars, (tokens.input, tokens.output, tokens.cached, tokens.reasoning)):
            variable.set(format_integer(value))

        detailed = [model for model in aggregate.by_model.values() if model.detailed_days]
        read_tokens = sum(model.tokens.cache_read for model in detailed)
        write_tokens = sum(model.tokens.cache_write for model in detailed)
        if not detailed:
            self.cache_detail_var.set("")
        else:
            self.cache_detail_var.set(f"Read {format_compact(read_tokens)}  ·  Write {format_compact(write_tokens)}")
        self.message_mix_var.set(f"Messages · {format_integer(aggregate.total.user_messages)} user  ·  {format_integer(aggregate.total.ai_messages)} AI")
        if self._codex_mode:
            self.message_mix_var.set("Reasoning is included in output. Cached reads are separate from input.")
        self._populate_usage_trees(aggregate)
        self._populate_data_tree(start, end)
        self._update_chart()

    def _update_chart(self) -> None:
        if not self._dataset or not self._selected_aggregate:
            return
        aggregate = self._selected_aggregate
        points = chart_series(self._dataset, aggregate.start, aggregate.end)
        buckets = build_chart_buckets(points)
        span_days = (aggregate.end - aggregate.start).days + 1
        interval = "daily" if span_days <= 62 else ("weekly" if span_days <= 730 else "monthly")
        period_name = self.active_period_var.get().capitalize()
        self.chart_title_var.set(f"Usage · {interval}")
        self.chart.set_data(points, self.chart_metric_var.get())

    def _populate_usage_trees(self, aggregate: PeriodAggregate) -> None:
        clear_tree(self.analyzer_tree)
        for name, total in sorted(aggregate.by_analyzer.items(), key=lambda value: value[1].tokens.total, reverse=True):
            self.analyzer_tree.insert("", "end", values=(name, format_currency(total.cost), format_integer(total.tokens.total), format_integer(total.conversations), "Not collected" if self._codex_mode else format_integer(total.user_messages), format_integer(total.ai_messages)))
        clear_tree(self.model_tree)
        for model in sorted(aggregate.by_model.values(), key=lambda value: (value.messages, value.name), reverse=True):
            available = model.detailed_days > 0
            self.model_tree.insert("", "end", values=(model.name, format_integer(model.messages), format_integer(model.tokens.input) if available else "—", format_integer(model.tokens.output) if available else "—", format_integer(model.tokens.cache_read) if available else "—", format_integer(model.tokens.cache_write) if available else "—", format_currency(model.cost) if available else "—", model.detail_coverage))

    def _populate_quota_tree(self, snapshot: QuotaSnapshot) -> None:
        clear_tree(self.quota_tree)
        for window in snapshot.windows:
            scope = "Base weekly" if window is snapshot.base_weekly else window.kind.title()
            self.quota_tree.insert("", "end", iid=f"quota:{window.window_id}", values=(window.label, scope, format_percent(window.percent_used), format_percent(window.percent_remaining), format_local_reset(window.resets_at), format_countdown(window.resets_at)))
        if not snapshot.windows:
            self.quota_tree.insert("", "end", values=("No quota windows available", "—", "—", "—", "—", "—"))

    def _populate_data_tree(self, start: date, end: date) -> None:
        clear_tree(self.data_tree)
        if not self._dataset:
            return
        for row in daily_data_rows(self._dataset, start, end):
            total = row.total
            self.data_tree.insert("", "end", values=(row.day.strftime("%a %d %b %Y"), format_currency(total.cost), format_integer(total.tokens.cached), format_integer(total.tokens.input), format_integer(total.tokens.output), format_integer(total.tokens.reasoning), format_integer(total.conversations), "Not collected" if self._codex_mode else format_integer(total.tool_calls), "Not provided", ", ".join(row.models) if row.models else "—"))
        if not self.data_tree.get_children():
            self.data_tree.insert("", "end", values=("No usage in active period", "—", "—", "—", "—", "—", "—", "—", "Not provided", "—"))

    def _tick_countdowns(self) -> None:
        self.quota_card.update_countdown(self._quota_snapshot)
        if self._quota_snapshot:
            for window in self._quota_snapshot.windows:
                quota_id = f"quota:{window.window_id}"
                if self.quota_tree.exists(quota_id):
                    values = list(self.quota_tree.item(quota_id, "values"))
                    if len(values) >= 6:
                        values[5] = format_countdown(window.resets_at)
                        self.quota_tree.item(quota_id, values=values)
        self.after(1000, self._tick_countdowns)

    def _cost_label(self) -> str:
        return "Estimated USD"

    def _show_cost_diagnostics(self, diagnostics: CostDiagnostics) -> None:
        if diagnostics.summary:
            self._show_notice(diagnostics.summary, "warning", diagnostics.lines)
        elif self._dataset and self._dataset.ignored_raw_messages:
            self._show_notice("Unexpected message content was discarded.", "warning")
        else:
            self._hide_notice()
        self._details = diagnostics.lines

    def _show_notice(self, message: str, tone: str, details: tuple[str, ...] = (), source: str = "usage") -> None:
        self.notice_var.set(message)
        self._details = details
        self._notifications[source] = (message, tone, details)
        self._update_notification_button()

    def _hide_notice(self, source: str = "usage") -> None:
        self._notifications.pop(source, None)
        if source == "usage":
            self.notice_var.set("")
            self._details = ()
        self._update_notification_button()

    def _update_notification_button(self) -> None:
        count = sum(tone != "info" for _, tone, _ in self._notifications.values())
        self.notification_button.configure(text=f"Notifications · {count}" if count else "Notifications",
                                           style="Alert.TButton" if count else "Quiet.TButton")

    def _show_details(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Notifications")
        dialog.geometry("640x420")
        dialog.configure(bg=BG)
        dialog.transient(self)
        tk.Label(dialog, text="Notifications", bg=BG, fg=INK, font=(FONT, 14, "bold")).pack(anchor="w", padx=20, pady=(18, 12))
        panel = tk.Frame(dialog, bg=CARD)
        panel.pack(fill="both", expand=True, padx=20)
        content = tk.Text(panel, wrap="word", bg=CARD, fg=INK, relief="flat", padx=14, pady=14, font=(FONT, 10))
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=content.yview)
        scrollbar.pack(side="right", fill="y")
        content.configure(yscrollcommand=scrollbar.set)
        content.pack(fill="both", expand=True)
        messages = [message for message, _, _ in self._notifications.values()]
        content.insert("1.0", "\n\n".join(messages) if messages else "You're up to date.")
        content.insert("end", "\n\n" + self.source_status_var.get() + "\n" + self.sync_status_var.get())
        content.configure(state="disabled")
        actions = tk.Frame(dialog, bg=BG)
        actions.pack(fill="x", padx=20, pady=16)
        diagnostic_lines = list(self._usage_result.cost_diagnostics.lines) if self._usage_result else []
        for _, _, lines in self._notifications.values():
            diagnostic_lines.extend(lines)
        diagnostic_lines = list(dict.fromkeys(diagnostic_lines))
        def details():
            content.configure(state="normal")
            content.insert("end", "\n\n" + ("\n".join(diagnostic_lines) or "No additional diagnostics."))
            content.configure(state="disabled")
            detail_button.configure(state="disabled")
        detail_button = ttk.Button(actions, text="Details", command=details, style="Quiet.TButton")
        detail_button.pack(side="left")
        ttk.Button(actions, text="Model pricing", command=lambda: (dialog.destroy(), self._open_settings()), style="Primary.TButton").pack(side="right")
        ttk.Button(actions, text="Close", command=dialog.destroy, style="Quiet.TButton").pack(side="right", padx=8)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())


def cycle_period_index(current: int, direction: int, period_count: int) -> int:
    if period_count <= 0:
        raise ValueError("period_count must be positive")
    return (current + direction) % period_count


def chart_series(dataset: UsageDataset, start: date, end: date) -> list[SeriesPoint]:
    if (end - start).days < 62:
        return daily_series(dataset, start, end)
    return [SeriesPoint(row.day, row.total) for row in reversed(daily_data_rows(dataset, start, end))]


def build_chart_buckets(points: list[SeriesPoint]) -> list[ChartBucket]:
    if not points:
        return []
    span_days = (points[-1].day - points[0].day).days + 1
    if span_days <= 62:
        return [ChartBucket(point.day.strftime("%d %b"), point.total) for point in points]
    monthly = span_days > 730
    buckets: dict[tuple[int, int], UsageTotal] = {}
    labels: dict[tuple[int, int], str] = {}
    for point in points:
        if monthly:
            key = (point.day.year, point.day.month)
            label = point.day.strftime("%b %Y")
        else:
            monday = point.day.fromordinal(point.day.toordinal() - point.day.weekday())
            key = (monday.year, monday.toordinal())
            label = monday.strftime("%d %b")
        target = buckets.setdefault(key, UsageTotal())
        target.tokens = target.tokens + point.total.tokens
        target.cost += point.total.cost
        target.conversations += point.total.conversations
        target.user_messages += point.total.user_messages
        target.ai_messages += point.total.ai_messages
        target.tool_calls += point.total.tool_calls
        labels[key] = label
    return [ChartBucket(labels[key], buckets[key]) for key in sorted(buckets)]


def chart_value(total: UsageTotal, metric: str) -> float:
    if metric == "Estimated cost (USD)":
        return total.cost
    if metric == "Conversations":
        return float(total.conversations)
    if metric == "Messages":
        return float(total.messages)
    return float(total.tokens.total)


def nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    normalized = value / magnitude
    step = 1 if normalized <= 1 else 2 if normalized <= 2 else 5 if normalized <= 5 else 10
    return step * magnitude


def format_axis(value: float, metric: str) -> str:
    if metric == "Estimated cost (USD)":
        return f"${value:,.2f}" if value < 100 else f"${value:,.0f}"
    return format_compact(int(value))


def format_integer(value: int) -> str:
    return f"{value:,}"


def format_compact(value: int) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_currency(value: float) -> str:
    return f"${value:,.2f}"


def format_percent(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—" + (f" {suffix}" if suffix else "")
    shown = f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return f"{shown}%" + (f" {suffix}" if suffix else "")


def format_date_range(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    if start.year == end.year:
        return f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"


def clear_tree(tree: ttk.Treeview) -> None:
    children = tree.get_children()
    if children:
        tree.delete(*children)


def rounded_rectangle(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **options: object,
) -> int:
    points = (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)
