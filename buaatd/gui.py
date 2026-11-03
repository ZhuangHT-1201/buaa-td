from __future__ import annotations

import ctypes
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from tkinter import filedialog, messagebox, ttk

from .background import (
    clear_pid,
    daemon_status,
    is_running,
    read_pid,
    start_daemon,
    stop_daemon,
    write_pid,
)
from .client import TDClient
from .const import BEIJING_TZ, TD_LIMIT
from .i18n import translate
from .machines import machine_pool
from .paths import project_root
from .photo import is_default_image
from .scheduler import clear_today_errors
from .store import Store
from .window import parse_window
from .wizard import load_users_file, repair_user_assets, setup_user, setup_users


APP_TITLE = "北航 TD 控制台"
APP_TITLE_EN = "BUAA TD Console"
APP_VERSION = "1.2"

BG = "#F3F6FB"
CARD = "#FFFFFF"
NAVY = "#10213D"
NAVY_SOFT = "#1A3155"
TEXT = "#17233C"
MUTED = "#66758C"
LINE = "#E1E7F0"
BLUE = "#2F5BEA"
BLUE_DARK = "#2449C7"
GREEN = "#16866B"
GREEN_SOFT = "#E8F7F1"
AMBER = "#A96800"
AMBER_SOFT = "#FFF5DD"
RED = "#C83E4D"
RED_SOFT = "#FDECEF"
SKY_SOFT = "#EAF1FF"


def run_gui() -> int:
    _enable_windows_ui()
    store = Store()
    store.initialize()

    console_pid_path = store.home / "console.pid"
    existing_pid = read_pid(console_pid_path)
    if (
        existing_pid is not None
        and existing_pid != os.getpid()
        and is_running(existing_pid)
    ):
        if _activate_existing_console():
            return 0
        # The previous console may still be constructing its window.
        for _ in range(15):
            time.sleep(0.1)
            if _activate_existing_console():
                return 0
        if is_running(existing_pid):
            return 0

    clear_pid(console_pid_path)
    write_pid(console_pid_path, os.getpid())
    app: Optional[Dashboard] = None
    try:
        app = Dashboard(store)
        app.mainloop()
        return 0
    finally:
        if app is not None:
            app.closed = True
        clear_pid(console_pid_path)


class Dashboard(tk.Tk):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.closed = False
        self.language = self._load_language()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._starting = False
        self._querying_counts = False
        self._checking_health = False
        self._last_log_text = ""
        self._users_signature: Optional[str] = None
        self._log_file_signature: Optional[tuple[str, Optional[int], Optional[int]]] = None
        self._last_running: Optional[bool] = None
        self._refresh_after_id: Optional[str] = None
        self._poll_after_id: Optional[str] = None
        self._window_labels: list[tuple[int, tk.Label, tk.Label]] = []

        self.title(self.tr("app_title"))
        self.configure(bg=BG)
        self._set_initial_geometry()
        self.protocol("WM_DELETE_WINDOW", self._close_console)
        self.bind("<F5>", lambda _event: self.refresh())
        self.bind("<Control-n>", lambda _event: self._open_member_dialog())
        self.bind("<F2>", lambda _event: self._edit_selected())

        self.font_family = self._pick_font()
        self._configure_styles()
        self._build_ui()

        self._schedule_refresh(80)
        self._schedule_poll(80)

    def _pick_font(self) -> str:
        available = set(tkfont.families(self))
        for family in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Segoe UI"):
            if family in available:
                return family
        return "TkDefaultFont"

    def tr(self, key: str, **values: Any) -> str:
        return translate(self.language, key, **values)

    def _load_language(self) -> str:
        try:
            language = self.store.load_settings().get("ui", {}).get("language", "zh")
        except Exception:
            language = "zh"
        return "en" if language == "en" else "zh"

    def _toggle_language(self) -> None:
        self.language = "en" if self.language == "zh" else "zh"
        settings = self.store.load_settings()
        settings.setdefault("ui", {})["language"] = self.language
        self.store.save_settings(settings)
        self.title(self.tr("app_title"))
        for child in list(self.winfo_children()):
            child.destroy()
        self._window_labels = []
        self._last_log_text = ""
        self._users_signature = None
        self._log_file_signature = None
        self._build_ui()
        if self._querying_counts:
            self.refresh_button.configure(
                state="disabled",
                text=self.tr("syncing_counts"),
            )
        if self._checking_health:
            self.health_button.configure(
                state="disabled",
                text=self.tr("checking_health"),
            )
        self.refresh()

    def _set_initial_geometry(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        work_left, work_top = 0, 0
        work_width, work_height = screen_width, screen_height
        if os.name == "nt":
            try:
                rect = _win_rect()
                if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0
                ):
                    work_left = int(rect.left)
                    work_top = int(rect.top)
                    work_width = int(rect.right - rect.left)
                    work_height = int(rect.bottom - rect.top)
            except Exception:
                pass

        width = min(1200, max(960, work_width - 36))
        height = min(860, max(700, work_height - 52))
        x = work_left + max(0, (work_width - width) // 2)
        y = work_top + max(8, (work_height - height - 40) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1060, width), min(760, height))

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return self.font_family, size, weight

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            borderwidth=0,
            relief="flat",
            rowheight=37,
            font=self._font(10),
        )
        style.map(
            "Treeview",
            background=[("selected", SKY_SOFT)],
            foreground=[("selected", BLUE_DARK)],
        )
        style.configure(
            "Treeview.Heading",
            background="#F7F9FC",
            foreground=MUTED,
            borderwidth=0,
            relief="flat",
            padding=(5, 10),
            font=self._font(9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", "#F0F4FA")])

        button_base = {
            "font": self._font(10, "bold"),
            "borderwidth": 0,
            "focuscolor": "",
            "padding": (15, 10),
        }
        style.configure(
            "Primary.TButton",
            **button_base,
            background=BLUE,
            foreground="#FFFFFF",
        )
        style.map(
            "Primary.TButton",
            background=[("active", BLUE_DARK), ("disabled", "#AAB9DE")],
            foreground=[("disabled", "#F4F6FB")],
        )
        style.configure(
            "Secondary.TButton",
            **button_base,
            background="#EEF3FC",
            foreground=TEXT,
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#E0E9FA"), ("disabled", "#F3F5F8")],
            foreground=[("disabled", "#9AA5B5")],
        )
        style.configure(
            "Danger.TButton",
            **button_base,
            background=RED_SOFT,
            foreground=RED,
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#F9DDE2"), ("disabled", "#F7F2F3")],
            foreground=[("disabled", "#C5ACB0")],
        )
        style.configure(
            "Small.TButton",
            font=self._font(9, "bold"),
            borderwidth=0,
            focuscolor="",
            padding=(11, 7),
            background="#F0F4FA",
            foreground=TEXT,
        )
        style.map("Small.TButton", background=[("active", "#E4EBF5")])
        style.configure(
            "SmallPrimary.TButton",
            font=self._font(9, "bold"),
            borderwidth=0,
            focuscolor="",
            padding=(11, 7),
            background=BLUE,
            foreground="#FFFFFF",
        )
        style.map(
            "SmallPrimary.TButton",
            background=[("active", BLUE_DARK), ("disabled", "#AAB9DE")],
            foreground=[("disabled", "#F4F6FB")],
        )

    def _build_ui(self) -> None:
        self._build_header()
        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=22, pady=(18, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_metrics(main)

        content = tk.Frame(main, bg=BG)
        content.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, minsize=338)
        content.grid_rowconfigure(0, weight=1)

        self._build_members(content)
        self._build_controls(content)
        self._build_log(main)
        self._build_footer(main)

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=NAVY, height=102)
        header.pack(fill="x")
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=NAVY)
        inner.pack(fill="both", expand=True, padx=25, pady=16)

        badge = tk.Label(
            inner,
            text="TD",
            bg=BLUE,
            fg="#FFFFFF",
            font=self._font(17, "bold"),
            width=3,
            height=1,
        )
        badge.pack(side="left", padx=(0, 14))

        titles = tk.Frame(inner, bg=NAVY)
        titles.pack(side="left", fill="y")
        tk.Label(
            titles,
            text=self.tr("brand_title"),
            bg=NAVY,
            fg="#FFFFFF",
            font=self._font(20, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(5, 3))
        tk.Label(
            titles,
            text=self.tr("subtitle"),
            bg=NAVY,
            fg="#AEC0DA",
            font=self._font(10),
            anchor="w",
        ).pack(anchor="w")

        right = tk.Frame(inner, bg=NAVY)
        right.pack(side="right", fill="y")
        self.clock_var = tk.StringVar(value="")
        tk.Label(
            right,
            textvariable=self.clock_var,
            bg=NAVY,
            fg="#DCE7F7",
            font=self._font(10),
            anchor="e",
        ).pack(anchor="e", pady=(3, 7))

        controls = tk.Frame(right, bg=NAVY)
        controls.pack(anchor="e")
        pill = tk.Frame(controls, bg=NAVY_SOFT, padx=12, pady=6)
        pill.pack(side="left")
        self.header_dot = tk.Label(
            pill,
            text="●",
            bg=NAVY_SOFT,
            fg="#94A3B8",
            font=self._font(9),
        )
        self.header_dot.pack(side="left", padx=(0, 5))
        self.header_status = tk.Label(
            pill,
            text=self.tr("backend_stopped"),
            bg=NAVY_SOFT,
            fg="#E7EEF9",
            font=self._font(9, "bold"),
        )
        self.header_status.pack(side="left")
        self.language_button = tk.Button(
            controls,
            text=self.tr("language_button"),
            command=self._toggle_language,
            bg=NAVY_SOFT,
            fg="#FFFFFF",
            activebackground="#29456F",
            activeforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            font=self._font(9, "bold"),
            padx=11,
            pady=6,
            cursor="hand2",
        )
        self.language_button.pack(side="left", padx=(8, 0))

    def _build_metrics(self, parent: tk.Widget) -> None:
        row = tk.Frame(parent, bg=BG)
        row.grid(row=0, column=0, sticky="ew")
        for column in range(3):
            row.grid_columnconfigure(column, weight=1, uniform="metrics")

        service_card, service_inner = self._metric_card(row, row=0, column=0)
        self.metric_service = tk.Label(
            service_inner,
            text=self.tr("status_stopped"),
            bg=CARD,
            fg=TEXT,
            font=self._font(22, "bold"),
            anchor="w",
        )
        self.metric_service.pack(anchor="w", pady=(2, 2))
        self.metric_service_detail = tk.Label(
            service_inner,
            text=self.tr("service_stop_hint"),
            bg=CARD,
            fg=MUTED,
            font=self._font(9),
            anchor="w",
        )
        self.metric_service_detail.pack(anchor="w")

        today_card, today_inner = self._metric_card(row, row=0, column=1)
        self.metric_today = tk.Label(
            today_inner,
            text=self.tr("rounds_zero"),
            bg=CARD,
            fg=TEXT,
            font=self._font(22, "bold"),
            anchor="w",
        )
        self.metric_today.pack(anchor="w", pady=(2, 2))
        self.metric_today_detail = tk.Label(
            today_inner,
            text=self.tr("rounds_per_day"),
            bg=CARD,
            fg=MUTED,
            font=self._font(9),
            anchor="w",
        )
        self.metric_today_detail.pack(anchor="w")

        total_card, total_inner = self._metric_card(row, row=0, column=2)
        self.metric_total = tk.Label(
            total_inner,
            text="—",
            bg=CARD,
            fg=TEXT,
            font=self._font(22, "bold"),
            anchor="w",
        )
        self.metric_total.pack(anchor="w", pady=(2, 2))
        self.metric_total_detail = tk.Label(
            total_inner,
            text=self.tr("cap_skip", limit=TD_LIMIT),
            bg=CARD,
            fg=MUTED,
            font=self._font(9),
            anchor="w",
        )
        self.metric_total_detail.pack(anchor="w")

        # Keep references alive; Tk widgets already own their parent relationship.
        self._metric_cards = (service_card, today_card, total_card)

    def _metric_card(
        self, parent: tk.Widget, row: int, column: int
    ) -> tuple[tk.Frame, tk.Frame]:
        outer = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        outer.grid(
            row=row,
            column=column,
            sticky="ew",
            padx=(0 if column == 0 else 6, 0 if column == 2 else 6),
        )
        accent = tk.Frame(
            outer,
            bg=(BLUE, GREEN, AMBER)[column],
            width=4,
        )
        accent.pack(side="left", fill="y")
        inner = tk.Frame(outer, bg=CARD, padx=16, pady=13)
        inner.pack(side="left", fill="both", expand=True)
        title = tk.Label(
            inner,
            text=(
                self.tr("metric_backend"),
                self.tr("metric_today"),
                self.tr("metric_progress"),
            )[column],
            bg=CARD,
            fg=MUTED,
            font=self._font(9, "bold"),
            anchor="w",
        )
        title.pack(anchor="w", pady=(0, 2))
        return outer, inner

    def _build_members(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        heading = tk.Frame(card, bg=CARD)
        heading.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 10))
        heading.grid_columnconfigure(0, weight=1)

        title_group = tk.Frame(heading, bg=CARD)
        title_group.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_group,
            text=self.tr("members_title"),
            bg=CARD,
            fg=TEXT,
            font=self._font(12, "bold"),
        ).pack(side="left")
        self.member_count = tk.Label(
            title_group,
            text=self.tr("member_count", count=0),
            bg=SKY_SOFT,
            fg=BLUE_DARK,
            font=self._font(8, "bold"),
            padx=8,
            pady=3,
        )
        self.member_count.pack(side="left", padx=(8, 0))

        tools = tk.Frame(heading, bg=CARD)
        tools.grid(row=0, column=1, sticky="e")
        self.import_button = ttk.Button(
            tools,
            text=self.tr("import_roster"),
            style="Small.TButton",
            command=self._import_roster,
        )
        self.import_button.pack(side="left", padx=(0, 6))
        self.add_button = ttk.Button(
            tools,
            text=self.tr("add_member"),
            style="SmallPrimary.TButton",
            command=lambda: self._open_member_dialog(),
        )
        self.add_button.pack(side="left", padx=(0, 6))
        self.edit_button = ttk.Button(
            tools,
            text=self.tr("edit"),
            style="Small.TButton",
            command=self._edit_selected,
            state="disabled",
        )
        self.edit_button.pack(side="left", padx=(0, 6))
        self.delete_button = ttk.Button(
            tools,
            text=self.tr("delete"),
            style="Small.TButton",
            command=self._delete_selected,
            state="disabled",
        )
        self.delete_button.pack(side="left")

        table_frame = tk.Frame(card, bg=CARD)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        columns = ("name", "sid", "campus", "total", "today", "state", "detail")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        definitions = (
            ("name", self.tr("column_name"), 82, "w"),
            ("sid", self.tr("column_student_id"), 118, "w"),
            ("campus", self.tr("column_campus"), 72, "w"),
            ("total", self.tr("column_td", limit=TD_LIMIT), 82, "center"),
            ("today", self.tr("column_today"), 78, "center"),
            ("state", self.tr("column_status"), 108, "w"),
            ("detail", self.tr("column_latest"), 245, "w"),
        )
        for key, label, width, anchor in definitions:
            self.tree.heading(key, text=label)
            self.tree.column(
                key,
                width=width,
                minwidth=min(60, width),
                anchor=anchor,
                stretch=(key == "detail"),
            )
        self.tree.tag_configure("pending", foreground=TEXT)
        self.tree.tag_configure("waiting", foreground=AMBER)
        self.tree.tag_configure("window_done", foreground=GREEN)
        self.tree.tag_configure("completed", foreground=GREEN)
        self.tree.tag_configure("error", foreground=RED)
        self.tree.tag_configure("empty", foreground=MUTED)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self._edit_selected())

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 7), pady=(0, 8))
        self.tree.configure(yscrollcommand=scroll.set)

        self.member_detail_var = tk.StringVar(
            value=self.tr("member_detail_default")
        )
        self.member_detail_label = tk.Label(
            card,
            textvariable=self.member_detail_var,
            bg="#F8FAFD",
            fg=MUTED,
            font=self._font(8),
            anchor="w",
            padx=12,
            pady=8,
        )
        self.member_detail_label.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=12,
            pady=(0, 11),
        )

    def _build_controls(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.grid(row=0, column=1, sticky="nsew")
        inner = tk.Frame(card, bg=CARD, padx=17, pady=15)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text=self.tr("automation_title"),
            bg=CARD,
            fg=TEXT,
            font=self._font(12, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            inner,
            text=self.tr("automation_desc"),
            bg=CARD,
            fg=MUTED,
            font=self._font(9),
            anchor="w",
            justify="left",
            wraplength=290,
        ).pack(fill="x", pady=(3, 12))

        schedule = tk.Frame(inner, bg=CARD)
        schedule.pack(fill="x", pady=(0, 8))
        for index in range(3):
            schedule.grid_columnconfigure(index, weight=1, uniform="schedule")
            row = tk.Frame(schedule, bg="#F7F9FC", padx=6, pady=7)
            row.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 3, 0 if index == 2 else 3),
            )
            status_row = tk.Frame(row, bg="#F7F9FC")
            status_row.pack(fill="x")
            dot = tk.Label(
                status_row,
                text="●",
                bg="#F7F9FC",
                fg="#B6C1D2",
                font=self._font(8),
            )
            dot.pack(side="left", padx=(0, 4))
            state_label = tk.Label(
                status_row,
                text=self.tr("schedule_waiting"),
                bg="#F7F9FC",
                fg=MUTED,
                font=self._font(8, "bold"),
                anchor="w",
            )
            state_label.pack(side="left")
            time_label = tk.Label(
                row,
                text="--:--\n--:--",
                bg="#F7F9FC",
                fg=TEXT,
                font=self._font(8, "bold"),
                anchor="w",
                justify="left",
            )
            time_label.pack(fill="x", pady=(4, 0))
            self._window_labels.append((index, dot, state_label))
            setattr(self, f"_window_time_{index}", time_label)
            setattr(self, f"_window_row_{index}", row)

        self.window_hint = tk.Label(
            inner,
            text=self.tr("schedule_reading"),
            bg=CARD,
            fg=MUTED,
            font=self._font(9),
            anchor="w",
            justify="left",
            wraplength=290,
        )
        self.window_hint.pack(fill="x", pady=(1, 8))

        button_row = tk.Frame(inner, bg=CARD)
        button_row.pack(fill="x", pady=(0, 7))
        button_row.grid_columnconfigure(0, weight=3)
        button_row.grid_columnconfigure(1, weight=2)
        self.start_button = ttk.Button(
            button_row,
            text=self.tr("start_auto"),
            style="Primary.TButton",
            command=self._start_background,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(
            button_row,
            text=self.tr("stop_background"),
            style="Danger.TButton",
            command=self._stop_background,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.refresh_button = ttk.Button(
            inner,
            text=self.tr("sync_count"),
            style="Secondary.TButton",
            command=self._refresh_counts,
        )
        self.refresh_button.pack(fill="x", pady=(0, 8))

        utilities = tk.Frame(inner, bg=CARD)
        utilities.pack(fill="x")
        for column in range(3):
            utilities.grid_columnconfigure(column, weight=1, uniform="utility")
        self.health_button = ttk.Button(
            utilities,
            text=self.tr("health_check"),
            style="Small.TButton",
            command=self._run_health_check,
        )
        self.health_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(
            utilities,
            text=self.tr("open_logs"),
            style="Small.TButton",
            command=self._open_logs,
        ).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(
            utilities,
            text=self.tr("data_folder"),
            style="Small.TButton",
            command=self._open_data,
        ).grid(row=0, column=2, sticky="ew", padx=(3, 0))

        tip = tk.Frame(inner, bg=GREEN_SOFT, padx=10, pady=6)
        tip.pack(fill="x", side="bottom", pady=(12, 0))
        tk.Label(
            tip,
            text=self.tr("runtime_tip"),
            bg=GREEN_SOFT,
            fg=GREEN,
            font=self._font(8, "bold"),
            anchor="w",
        ).pack(fill="x")

    def _build_log(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)

        heading = tk.Frame(card, bg=CARD)
        heading.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 5))
        heading.grid_columnconfigure(0, weight=1)
        tk.Label(
            heading,
            text=self.tr("logs_title"),
            bg=CARD,
            fg=TEXT,
            font=self._font(10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            heading,
            text=self.tr("refresh_logs"),
            style="Small.TButton",
            command=self._refresh_log,
        ).grid(row=0, column=1, sticky="e")

        text_frame = tk.Frame(card, bg="#F8FAFD")
        text_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        text_frame.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            text_frame,
            height=4,
            bg="#F8FAFD",
            fg="#344054",
            insertbackground=TEXT,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            wrap="none",
            font=self._font(8),
        )
        self.log_text.grid(row=0, column=0, sticky="ew")
        log_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set, state="disabled")

    def _build_footer(self, parent: tk.Widget) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        self.notice_var = tk.StringVar(value=self.tr("ready"))
        self.notice_label = tk.Label(
            footer,
            textvariable=self.notice_var,
            bg=BG,
            fg=MUTED,
            font=self._font(8),
            anchor="w",
        )
        self.notice_label.pack(side="left")
        tk.Label(
            footer,
            text=f"v{APP_VERSION}  ·  {self.tr('footer_shortcut')}",
            bg=BG,
            fg="#8A98AB",
            font=self._font(8),
            anchor="e",
        ).pack(side="right")

    def refresh(self) -> None:
        if self.closed:
            return
        if self._refresh_after_id is not None:
            try:
                self.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
        try:
            now = datetime.now(BEIJING_TZ)
            clock_format = (
                "%Y-%m-%d  %H:%M:%S"
                if self.language == "en"
                else "%Y年%m月%d日  %H:%M:%S"
            )
            self.clock_var.set(now.strftime(clock_format))
            bg = daemon_status(self.store)
            users = self.store.list_users()
            state = self.store.load_state()
            settings = self.store.load_settings()
            self._render_background(bg.running, bg.pid, bool(users))
            self._render_users(users, state)
            self._render_summary(users, state)
            self._render_schedule(settings, now)
            self._refresh_log()
        except Exception as exc:
            self._set_notice(self.tr("read_state_failed", error=exc), "error")
        finally:
            self._schedule_refresh(3000)

    def _schedule_refresh(self, delay_ms: int) -> None:
        if self.closed or self._refresh_after_id is not None:
            return
        self._refresh_after_id = self.after(delay_ms, self.refresh)

    def _render_background(
        self, running: bool, pid: Optional[int], has_users: bool
    ) -> None:
        if running:
            self.metric_service.configure(text=self.tr("status_running"), fg=GREEN)
            self.metric_service_detail.configure(
                text=self.tr("active_pid", pid=pid)
            )
            self.header_dot.configure(fg="#45D6A9")
            self.header_status.configure(text=self.tr("backend_running"))
            self.window_hint.configure(fg=MUTED)
        else:
            self.metric_service.configure(text=self.tr("status_stopped"), fg=MUTED)
            self.metric_service_detail.configure(text=self.tr("service_stop_hint"))
            self.header_dot.configure(fg="#94A3B8")
            self.header_status.configure(text=self.tr("backend_stopped"))

        if self._last_running is not None and self._last_running != running:
            self._set_notice(
                self.tr("background_started_notice")
                if running
                else self.tr("background_stopped_notice"),
                "success" if running else "muted",
            )
        self._last_running = running
        self.stop_button.configure(state="normal" if running else "disabled")
        self.start_button.configure(
            state="disabled"
            if running or self._starting or not has_users
            else "normal"
        )

    def _render_users(self, users: list[Any], state: dict[str, Any]) -> None:
        selected = self._selected_student_id()
        today_states = self._today_states(state)
        counts = state.get("counts", {})
        signature = json.dumps(
            {
                "users": [user.to_dict() for user in users],
                "states": today_states,
                "counts": counts,
                "language": self.language,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        self.member_count.configure(text=self.tr("member_count", count=len(users)))
        if signature == self._users_signature:
            selected_user = users_by_id(users).get(selected or "")
            self.delete_button.configure(
                state="normal" if selected_user is not None else "disabled"
            )
            self.edit_button.configure(
                state="normal" if selected_user is not None else "disabled"
            )
            self._render_selected_detail(selected_user, state)
            return
        self._users_signature = signature
        self.tree.delete(*self.tree.get_children())
        for user in users:
            user_state = today_states.get(user.student_id, {})
            count_info = counts.get(user.student_id, {})
            count = count_info.get("count") if isinstance(count_info, dict) else None
            count_text = f"{count}/{TD_LIMIT}" if count is not None else f"—/{TD_LIMIT}"
            completed = int(user_state.get("completed_rounds", 0) or 0)
            status = str(user_state.get("status", "pending"))
            state_text = {
                "pending": self.tr("status_pending"),
                "waiting": self.tr("status_waiting"),
                "window_done": self.tr("status_window_done"),
                "completed": self.tr("status_completed"),
                "error": self.tr("status_error"),
            }.get(status, status)
            detail = self._state_detail(user_state)
            due_at = self._parse_datetime(user_state.get("due_at"))
            if due_at is not None and status == "waiting":
                detail = self.tr(
                    "execute_at",
                    message=detail,
                    time=due_at.strftime("%H:%M"),
                )
            self.tree.insert(
                "",
                "end",
                iid=user.student_id,
                values=(
                    user.name or "—",
                    user.student_id,
                    self._campus_for_user(user),
                    count_text,
                    f"{completed}/{user.rounds}",
                    state_text,
                    detail,
                ),
                tags=(
                    status
                    if status
                    in {"pending", "waiting", "window_done", "completed", "error"}
                    else "pending",
                ),
            )

        if not users:
            self.tree.insert(
                "",
                "end",
                iid="__empty__",
                values=(
                    "—",
                    "—",
                    "—",
                    f"—/{TD_LIMIT}",
                    "—",
                    self.tr("no_members_short"),
                    self.tr("empty_member_hint"),
                ),
                tags=("empty",),
            )
        elif selected and selected in users_by_id(users):
            self.tree.selection_set(selected)
            self.tree.focus(selected)
        selected_user = users_by_id(users).get(selected or "")
        self.delete_button.configure(
            state="normal" if selected_user is not None else "disabled"
        )
        self.edit_button.configure(
            state="normal" if selected_user is not None else "disabled"
        )
        self._render_selected_detail(selected_user, state)

    def _render_selected_detail(
        self, user: Any, state: Optional[dict[str, Any]] = None
    ) -> None:
        if user is None:
            self.member_detail_var.set(self.tr("member_detail_default"))
            self.member_detail_label.configure(fg=MUTED)
            return
        if state is None:
            state = self.store.load_state()
        user_state = self._today_states(state).get(user.student_id, {})
        default_entrance = is_default_image(user.entrance_image)
        default_exit = is_default_image(user.exit_image)
        if default_entrance and default_exit:
            photo_text = self.tr("photo_default")
        elif not default_entrance and not default_exit:
            photo_text = self.tr("photo_custom")
        else:
            photo_text = self.tr("photo_mixed")
        detail = self._state_detail(user_state)
        self.member_detail_var.set(
            self.tr(
                "member_detail",
                photo=photo_text,
                entrance=user.entrance_machine_id,
                exit=user.exit_machine_id,
                detail=detail,
            )
        )
        self.member_detail_label.configure(fg=TEXT)

    def _render_summary(self, users: list[Any], state: dict[str, Any]) -> None:
        today_states = self._today_states(state)
        completed_rounds = 0
        completed_users = 0
        for user in users:
            user_state = today_states.get(user.student_id, {})
            completed_rounds += int(user_state.get("completed_rounds", 0) or 0)
            if user_state.get("status") == "completed":
                completed_users += 1
        possible_rounds = sum(max(1, int(user.rounds)) for user in users)
        self.metric_today.configure(
            text=self.tr(
                "today_rounds",
                completed=completed_rounds,
                possible=possible_rounds,
            )
        )
        self.metric_today_detail.configure(
            text=self.tr(
                "today_users",
                completed=completed_users,
                total=len(users),
            )
            if users
            else self.tr("imported_start_hint")
        )

        counts = state.get("counts", {})
        known_counts = []
        for user in users:
            info = counts.get(user.student_id, {})
            if isinstance(info, dict) and info.get("count") is not None:
                try:
                    known_counts.append(int(info["count"]))
                except (TypeError, ValueError):
                    pass
        if known_counts:
            possible_total = TD_LIMIT * len(users)
            self.metric_total.configure(text=f"{sum(known_counts)} / {possible_total}")
            self.metric_total_detail.configure(
                text=self.tr(
                    "known_counts",
                    known=len(known_counts),
                    total=len(users),
                    limit=TD_LIMIT,
                )
            )
        else:
            self.metric_total.configure(text="—")
            self.metric_total_detail.configure(
                text=self.tr("sync_counts_hint", limit=TD_LIMIT)
            )

    def _render_schedule(self, settings: dict[str, Any], now: datetime) -> None:
        windows = list(settings.get("schedule", {}).get("windows", []))
        now_minutes = now.hour * 60 + now.minute
        active_index: Optional[int] = None
        upcoming: list[tuple[int, datetime]] = []

        for index in range(3):
            text = windows[index] if index < len(windows) else "--:-- - --:--"
            time_label = getattr(self, f"_window_time_{index}")
            if index < len(windows) and "-" in text:
                start_text, end_text = text.split("-", 1)
                time_label.configure(text=f"{start_text.strip()}\n{end_text.strip()}")
            else:
                time_label.configure(text=text)
            _, dot, state_label = self._window_labels[index]
            row = getattr(self, f"_window_row_{index}")
            bg_color = "#F7F9FC"
            dot_color = "#B6C1D2"
            state_color = MUTED
            label = self.tr("schedule_waiting")
            if index < len(windows):
                try:
                    start, end = parse_window(windows[index])
                    start_dt = now.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
                    if start <= now_minutes <= end:
                        active_index = index
                        bg_color = GREEN_SOFT
                        dot_color = GREEN
                        state_color = GREEN
                        label = self.tr("schedule_active")
                    elif now_minutes > end:
                        label = self.tr("schedule_ended")
                    elif start_dt > now:
                        upcoming.append((index, start_dt))
                except (TypeError, ValueError):
                    text = self.tr("schedule_invalid")
                    time_label.configure(text=text)
                    state_color = RED
                    label = self.tr("schedule_check")
            else:
                label = self.tr("schedule_missing")
            row.configure(bg=bg_color)
            for child in row.winfo_children():
                child.configure(bg=bg_color)
            dot.configure(bg=bg_color, fg=dot_color)
            state_label.configure(bg=bg_color, fg=state_color, text=label)

        if active_index is not None:
            try:
                _, end = parse_window(windows[active_index])
                remaining = max(0, end - now_minutes)
                self.window_hint.configure(
                    text=self.tr(
                        "active_window_hint",
                        duration=self._duration_text(remaining),
                    ),
                    fg=GREEN,
                )
            except (TypeError, ValueError):
                self.window_hint.configure(text=self.tr("check_schedule"), fg=RED)
            return

        if upcoming:
            next_at = min(upcoming, key=lambda item: item[1])
            wait_minutes = max(0, int((next_at[1] - now).total_seconds() // 60))
            self.window_hint.configure(
                text=self.tr(
                    "next_window_hint",
                    time=next_at[1].strftime("%H:%M"),
                    duration=self._duration_text(wait_minutes),
                ),
                fg=BLUE_DARK,
            )
        elif windows:
            try:
                start, _ = parse_window(windows[0])
                tomorrow = (now + timedelta(days=1)).replace(
                    hour=start // 60, minute=start % 60, second=0, microsecond=0
                )
                wait_minutes = max(0, int((tomorrow - now).total_seconds() // 60))
                self.window_hint.configure(
                    text=self.tr(
                        "day_finished_hint",
                        time=tomorrow.strftime("%H:%M"),
                        duration=self._duration_text(wait_minutes),
                    ),
                    fg=MUTED,
                )
            except (TypeError, ValueError):
                self.window_hint.configure(text=self.tr("check_schedule"), fg=RED)
        else:
            self.window_hint.configure(text=self.tr("missing_schedule"), fg=RED)

    def _refresh_log(self) -> None:
        today = datetime.now(BEIJING_TZ).date().isoformat()
        path = self.store.logs_dir / f"{today}.log"
        try:
            stat = path.stat()
            file_signature = (str(path), stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            file_signature = (str(path), None, None)
        if file_signature == self._log_file_signature:
            return
        self._log_file_signature = file_signature
        text = _tail_text(path, max_lines=160)
        if not text:
            text = self.tr("log_empty")
        if text == self._last_log_text:
            return
        self._last_log_text = text
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _start_background(self) -> None:
        users = self.store.list_users()
        if not users:
            messagebox.showinfo(
                self.tr("no_members_title"),
                self.tr("no_members_prompt"),
                parent=self,
            )
            return
        self._starting = True
        self.start_button.configure(state="disabled")
        self._set_notice(self.tr("starting_background"), "muted")
        try:
            repairs = repair_user_assets(self.store)
            users = self.store.list_users()
            if not users:
                raise ValueError(self.tr("no_usable_members"))
            clear_today_errors(self.store)
            result = start_daemon(self.store)
        except Exception as exc:
            self._starting = False
            self.start_button.configure(state="normal")
            self._set_notice(self.tr("start_failed_message", error=exc), "error")
            messagebox.showerror(self.tr("start_failed"), str(exc), parent=self)
            return

        def verify() -> None:
            self._starting = False
            status = daemon_status(self.store)
            if status.running:
                action = (
                    self.tr("already_running")
                    if result.already_running
                    else self.tr("started_background")
                )
                repair_note = (
                    self.tr("repair_note", count=len(repairs)) if repairs else ""
                )
                self._set_notice(
                    self.tr(
                        "started_notice",
                        action=action,
                        pid=status.pid,
                        repair=repair_note,
                    ),
                    "success",
                )
            else:
                self._set_notice(self.tr("process_not_running"), "error")
            if not self.closed:
                self.refresh()

        self.after(700, verify)

    def _stop_background(self) -> None:
        if not messagebox.askyesno(
            self.tr("stop_title"),
            self.tr("stop_confirm"),
            parent=self,
        ):
            return
        try:
            result = stop_daemon(self.store)
        except Exception as exc:
            self._set_notice(f"{self.tr('stop_failed')}: {exc}", "error")
            messagebox.showerror(self.tr("stop_failed"), str(exc), parent=self)
            return
        self._set_notice(result.message, "muted")
        self.refresh()

    def _refresh_counts(self) -> None:
        if self._querying_counts:
            return
        users = self.store.list_users()
        if not users:
            messagebox.showinfo(
                self.tr("no_members_title"),
                self.tr("no_members_prompt"),
                parent=self,
            )
            return
        self._querying_counts = True
        self.refresh_button.configure(
            state="disabled",
            text=self.tr("syncing_counts"),
        )
        self._set_notice(self.tr("sync_connecting"), "muted")

        def worker() -> None:
            succeeded = 0
            failures: list[str] = []
            try:
                client = TDClient(self.store.load_config())
                for user in users:
                    try:
                        count = client.query_count(user)
                        self.store.set_last_count(user.student_id, count)
                        succeeded += 1
                    except Exception as exc:
                        failures.append(f"{user.display()}: {exc}")
            except Exception as exc:
                failures.append(str(exc))
            self.events.put(
                (
                    "counts_finished",
                    {
                        "succeeded": succeeded,
                        "failures": failures,
                        "total": len(users),
                    },
                )
            )

        threading.Thread(target=worker, name="buaatd-count-sync", daemon=True).start()

    def _run_health_check(self) -> None:
        if self._checking_health:
            return
        self._checking_health = True
        self.health_button.configure(
            state="disabled",
            text=self.tr("checking_health"),
        )
        self._set_notice(self.tr("health_checking_notice"), "muted")

        def worker() -> None:
            issues: list[str] = []
            repairs: list[str] = []
            try:
                repairs = repair_user_assets(self.store)
                users = self.store.list_users()
                if not users:
                    issues.append(self.tr("empty_roster"))
                config = self.store.load_config()
                endpoint = (
                    str(config.get("server", {}).get("ip", "")),
                    int(config.get("server", {}).get("port", 0)),
                )
                try:
                    connection = socket.create_connection(endpoint, timeout=4)
                    connection.close()
                    reachable = True
                except OSError as exc:
                    reachable = False
                    issues.append(
                        self.tr(
                            "server_unreachable",
                            host=endpoint[0],
                            port=endpoint[1],
                            error=exc,
                        )
                    )
            except Exception as exc:
                reachable = False
                issues.append(str(exc))
            self.events.put(
                (
                    "health_finished",
                    {
                        "issues": issues,
                        "repairs": repairs,
                        "reachable": reachable,
                    },
                )
            )

        threading.Thread(target=worker, name="buaatd-health-check", daemon=True).start()

    def _poll_events(self) -> None:
        if self.closed:
            return
        self._poll_after_id = None
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "counts_finished":
                self._querying_counts = False
                self.refresh_button.configure(
                    state="normal",
                    text=self.tr("sync_count"),
                )
                succeeded = int(payload["succeeded"])
                failures = list(payload["failures"])
                total = int(payload["total"])
                if succeeded:
                    detail = ""
                    if failures:
                        detail = self.tr("count_partial", count=len(failures))
                    self._set_notice(
                        self.tr(
                            "count_success",
                            succeeded=succeeded,
                            total=total,
                            detail=detail,
                        ),
                        "success" if not failures else "muted",
                    )
                else:
                    first_error = (
                        failures[0] if failures else self.tr("cannot_connect")
                    )
                    self._set_notice(
                        self.tr("count_failed", error=first_error),
                        "error",
                    )
                self.refresh()
            elif event == "health_finished":
                self._checking_health = False
                self.health_button.configure(
                    state="normal",
                    text=self.tr("health_check"),
                )
                issues = list(payload["issues"])
                repairs = list(payload["repairs"])
                if issues:
                    first_issue = issues[0]
                    extra = (
                        self.tr("health_more", count=len(issues) - 1)
                        if len(issues) > 1
                        else ""
                    )
                    self._set_notice(
                        self.tr("health_failed", issue=first_issue, extra=extra),
                        "error",
                    )
                else:
                    repair_note = (
                        self.tr("auto_repaired", count=len(repairs))
                        if repairs
                        else ""
                    )
                    self._set_notice(
                        self.tr("health_passed", repair=repair_note),
                        "success",
                    )
                self.refresh()
        self._schedule_poll(180)

    def _schedule_poll(self, delay_ms: int) -> None:
        if self.closed or self._poll_after_id is not None:
            return
        self._poll_after_id = self.after(delay_ms, self._poll_events)

    def _import_roster(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title=self.tr("choose_roster_title"),
            initialdir=str(project_root()),
            filetypes=(
                (self.tr("text_roster"), "*.txt"),
                (self.tr("all_files"), "*.*"),
            ),
        )
        if not path:
            return
        try:
            rows = load_users_file(Path(path))
            if not messagebox.askyesno(
                self.tr("import_title"),
                self.tr("import_confirm", count=len(rows)),
                parent=self,
            ):
                return
            photo_paths = None
            if messagebox.askyesno(
                self.tr("photo_optional_title"),
                self.tr("photo_optional_prompt"),
                parent=self,
            ):
                photo_path = filedialog.askopenfilename(
                    parent=self,
                    title=self.tr("choose_shared_photo"),
                    filetypes=(
                        (self.tr("jpg_files"), "*.jpg *.jpeg *.jfif *.png"),
                        (self.tr("all_files"), "*.*"),
                    ),
                )
                if not photo_path:
                    return
                photo_paths = [photo_path]
            setup_users(self.store, rows, photo_paths=photo_paths)
            photo_note = (
                self.tr("import_photo_note")
                if photo_paths
                else self.tr("import_default_note")
            )
            self._set_notice(
                self.tr("import_done", count=len(rows), photo=photo_note),
                "success",
            )
            self.refresh()
        except Exception as exc:
            self._set_notice(self.tr("import_failed_message", error=exc), "error")
            messagebox.showerror(self.tr("import_failed"), str(exc), parent=self)

    def _open_member_dialog(self, existing: Any = None) -> None:
        MemberDialog(
            self,
            self.store,
            existing=existing,
            on_saved=self._member_saved,
        )

    def _member_saved(self, user: Any) -> None:
        self._set_notice(
            self.tr("member_saved", user=user.display()),
            "success",
        )
        self.refresh()

    def _delete_selected(self) -> None:
        student_id = self._selected_student_id()
        if not student_id:
            return
        try:
            user = self.store.get_user_model(student_id)
        except KeyError:
            self.refresh()
            return
        if not messagebox.askyesno(
            self.tr("delete_title"),
            self.tr("delete_confirm", user=user.display()),
            parent=self,
        ):
            return
        self.store.delete_user(student_id)
        self._set_notice(self.tr("deleted", user=user.display()), "muted")
        self.refresh()

    def _on_tree_select(self, _event: tk.Event) -> None:
        student_id = self._selected_student_id()
        state = "normal" if student_id else "disabled"
        self.delete_button.configure(state=state)
        self.edit_button.configure(state=state)
        if not student_id:
            self._render_selected_detail(None)
            return
        try:
            self._render_selected_detail(self.store.get_user_model(student_id))
        except (KeyError, ValueError):
            self._render_selected_detail(None)

    def _edit_selected(self) -> None:
        student_id = self._selected_student_id()
        if not student_id:
            return
        try:
            user = self.store.get_user_model(student_id)
        except (KeyError, ValueError) as exc:
            messagebox.showerror(self.tr("cannot_edit"), str(exc), parent=self)
            return
        self._open_member_dialog(user)

    def _selected_student_id(self) -> Optional[str]:
        selection = self.tree.selection()
        if not selection:
            return None
        value = str(selection[0])
        return None if value == "__empty__" else value

    def _today_states(self, state: dict[str, Any]) -> dict[str, Any]:
        day_key = datetime.now(BEIJING_TZ).date().isoformat()
        users = state.get("daily_runs", {}).get(day_key, {}).get("users", {})
        return users if isinstance(users, dict) else {}

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ)

    def _state_detail(self, user_state: dict[str, Any]) -> str:
        if self.language != "en":
            return str(
                user_state.get("last_error")
                or user_state.get("last_message")
                or self.tr("wait_next_schedule")
            )
        status = str(user_state.get("status", "pending"))
        error = user_state.get("last_error")
        if status == "error":
            return self.tr("state_error", error=error or "-")
        if status == "waiting" and error:
            return self.tr("state_retrying")
        if status == "waiting" and user_state.get("next_action") == "exit":
            return self.tr("state_wait_exit")
        if status == "window_done":
            return self.tr("state_window_done_message")
        if status == "completed":
            return self.tr("state_today_done_message")
        return self.tr("state_wait_entrance")

    def _campus_code_for_user(self, user: Any) -> str:
        entrances = {int(machine["id"]) for machine in machine_pool("学院路", entrance=True)}
        if user.entrance_machine_id in entrances:
            return "xueyuanlu"
        return "shahe"

    def _campus_for_user(self, user: Any) -> str:
        code = self._campus_code_for_user(user)
        return self.tr(f"campus_{code}")

    def _duration_text(self, minutes: int) -> str:
        minutes = max(0, minutes)
        hours, remainder = divmod(minutes, 60)
        if hours and remainder:
            return self.tr(
                "duration_hours_minutes",
                hours=hours,
                minutes=remainder,
            )
        if hours:
            return self.tr("duration_hours", hours=hours)
        return self.tr("duration_minutes", minutes=remainder)

    def _set_notice(self, message: str, kind: str) -> None:
        colors = {
            "success": GREEN,
            "error": RED,
            "muted": MUTED,
        }
        self.notice_var.set(message)
        self.notice_label.configure(fg=colors.get(kind, MUTED))

    def _open_logs(self) -> None:
        self._open_path(self.store.logs_dir)

    def _open_data(self) -> None:
        self._open_path(self.store.home)

    def _open_path(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(path))
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(self.tr("open_directory_failed"), str(exc), parent=self)

    def _close_console(self) -> None:
        self.closed = True
        for callback_id in (self._refresh_after_id, self._poll_after_id):
            if callback_id is None:
                continue
            try:
                self.after_cancel(callback_id)
            except tk.TclError:
                pass
        self._refresh_after_id = None
        self._poll_after_id = None
        self.destroy()


class MemberDialog(tk.Toplevel):
    def __init__(
        self,
        parent: Dashboard,
        store: Store,
        existing: Any = None,
        on_saved: Any = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.store = store
        self.existing = existing
        self.on_saved = on_saved
        self.saved_user = None
        self.use_default_photos = False
        self.selected_photo = ""
        self._preview_image = None
        self._focus_after_id: Optional[str] = None

        self.title(
            parent.tr("dialog_edit_title")
            if existing is not None
            else parent.tr("dialog_add_title")
        )
        self.configure(bg=BG)
        self.geometry("590x570")
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _center_window(self, parent, 590, 570)

        self.sid_var = tk.StringVar(
            value=existing.student_id if existing is not None else ""
        )
        self.name_var = tk.StringVar(
            value=existing.name if existing is not None else ""
        )
        self.campus_values = (
            parent.tr("campus_xueyuanlu"),
            parent.tr("campus_shahe"),
        )
        self.campus_var = tk.StringVar(
            value=(
                parent._campus_for_user(existing)
                if existing is not None
                else self.campus_values[0]
            )
        )
        self.photo_status_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self._build()

        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self._save())
        self._focus_after_id = self.after(60, self.id_entry.focus_set)

    def destroy(self) -> None:
        if self._focus_after_id is not None:
            try:
                self.after_cancel(self._focus_after_id)
            except tk.TclError:
                pass
            self._focus_after_id = None
        super().destroy()

    def _build(self) -> None:
        header = tk.Frame(self, bg=NAVY, height=84)
        header.pack(fill="x")
        header.pack_propagate(False)
        title_group = tk.Frame(header, bg=NAVY)
        title_group.pack(fill="both", expand=True, padx=22, pady=14)
        tk.Label(
            title_group,
            text=(
                self.parent.tr("dialog_edit_header")
                if self.existing is not None
                else self.parent.tr("dialog_add_header")
            ),
            bg=NAVY,
            fg="#FFFFFF",
            font=self.parent._font(16, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            title_group,
            text=self.parent.tr("dialog_subtitle"),
            bg=NAVY,
            fg="#B9C8DD",
            font=self.parent._font(9),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        card = tk.Frame(
            self,
            bg=CARD,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)
        body = tk.Frame(card, bg=CARD, padx=22, pady=18)
        body.pack(fill="both", expand=True)

        fields = tk.Frame(body, bg=CARD)
        fields.pack(fill="x")
        fields.grid_columnconfigure(0, weight=1, uniform="fields")
        fields.grid_columnconfigure(1, weight=1, uniform="fields")
        self.id_entry = self._entry_field(
            fields,
            column=0,
            label=self.parent.tr("student_id"),
            variable=self.sid_var,
            hint=self.parent.tr("student_id_hint"),
        )
        if self.existing is not None:
            self.id_entry.configure(state="disabled", disabledbackground="#EEF2F7")
        self._entry_field(
            fields,
            column=1,
            label=self.parent.tr("name"),
            variable=self.name_var,
            hint=self.parent.tr("optional"),
        )

        campus_group = tk.Frame(body, bg=CARD)
        campus_group.pack(fill="x", pady=(13, 0))
        tk.Label(
            campus_group,
            text=self.parent.tr("campus"),
            bg=CARD,
            fg=TEXT,
            font=self.parent._font(9, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 5))
        self.campus_box = ttk.Combobox(
            campus_group,
            textvariable=self.campus_var,
            values=self.campus_values,
            state="readonly",
            font=self.parent._font(10),
        )
        self.campus_box.pack(fill="x", ipady=3)

        photo_heading = tk.Frame(body, bg=CARD)
        photo_heading.pack(fill="x", pady=(15, 7))
        tk.Label(
            photo_heading,
            text=self.parent.tr("checkin_photo"),
            bg=CARD,
            fg=TEXT,
            font=self.parent._font(9, "bold"),
        ).pack(side="left")
        tk.Label(
            photo_heading,
            text=self.parent.tr("optional"),
            bg=GREEN_SOFT,
            fg=GREEN,
            font=self.parent._font(8, "bold"),
            padx=7,
            pady=2,
        ).pack(side="left", padx=(7, 0))

        photo_card = tk.Frame(body, bg="#F7F9FC", padx=12, pady=10)
        photo_card.pack(fill="x")
        photo_copy = tk.Frame(photo_card, bg="#F7F9FC")
        photo_copy.pack(side="left", fill="both", expand=True)
        tk.Label(
            photo_copy,
            textvariable=self.photo_status_var,
            bg="#F7F9FC",
            fg=TEXT,
            font=self.parent._font(9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            photo_copy,
            text=self.parent.tr("photo_formats"),
            bg="#F7F9FC",
            fg=MUTED,
            font=self.parent._font(8),
            anchor="w",
            justify="left",
            wraplength=350,
        ).pack(fill="x", pady=(4, 9))
        photo_buttons = tk.Frame(photo_copy, bg="#F7F9FC")
        photo_buttons.pack(fill="x")
        ttk.Button(
            photo_buttons,
            text=self.parent.tr("choose_photo"),
            style="Small.TButton",
            command=self._choose_photo,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            photo_buttons,
            text=self.parent.tr("use_default_photo"),
            style="Small.TButton",
            command=self._reset_photo,
        ).pack(side="left")

        self.preview_label = tk.Label(
            photo_card,
            text="IMG",
            bg="#E9EFF8",
            fg=BLUE_DARK,
            font=self.parent._font(10, "bold"),
            width=9,
            height=4,
        )
        self.preview_label.pack(side="right", padx=(12, 0), fill="y")
        self._update_photo_status()

        self.error_label = tk.Label(
            body,
            textvariable=self.error_var,
            bg=CARD,
            fg=RED,
            font=self.parent._font(8),
            anchor="w",
            justify="left",
            wraplength=500,
        )
        self.error_label.pack(fill="x", pady=(8, 0))

        actions = tk.Frame(body, bg=CARD)
        actions.pack(fill="x", side="bottom", pady=(10, 0))
        ttk.Button(
            actions,
            text=self.parent.tr("cancel"),
            style="Secondary.TButton",
            command=self.destroy,
        ).pack(side="right")
        self.save_button = ttk.Button(
            actions,
            text=self.parent.tr("save_member"),
            style="Primary.TButton",
            command=self._save,
        )
        self.save_button.pack(side="right", padx=(0, 8))
        tk.Label(
            actions,
            text=self.parent.tr("save_shortcut"),
            bg=CARD,
            fg="#8A98AB",
            font=self.parent._font(8),
        ).pack(side="left")

    def _entry_field(
        self,
        parent: tk.Widget,
        column: int,
        label: str,
        variable: tk.StringVar,
        hint: str,
    ) -> tk.Entry:
        frame = tk.Frame(parent, bg=CARD)
        frame.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(0, 7) if column == 0 else (7, 0),
        )
        tk.Label(
            frame,
            text=label,
            bg=CARD,
            fg=TEXT,
            font=self.parent._font(9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 5))
        entry = tk.Entry(
            frame,
            textvariable=variable,
            relief="flat",
            bg="#F7F9FC",
            fg=TEXT,
            disabledforeground=MUTED,
            font=self.parent._font(10),
            highlightthickness=1,
            highlightbackground="#E5EAF2",
            highlightcolor=BLUE,
        )
        entry.pack(fill="x", ipady=7)
        tk.Label(
            frame,
            text=hint,
            bg=CARD,
            fg=MUTED,
            font=self.parent._font(8),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))
        return entry

    def _choose_photo(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title=self.parent.tr("choose_photo_title"),
            filetypes=(
                (self.parent.tr("jpg_files"), "*.jpg *.jpeg *.jfif *.png"),
                (self.parent.tr("all_files"), "*.*"),
            ),
        )
        if not selected:
            return
        self.selected_photo = selected
        self.use_default_photos = False
        self.error_var.set("")
        self._update_photo_status()

    def _reset_photo(self) -> None:
        self.selected_photo = ""
        self.use_default_photos = True
        self.error_var.set("")
        self._update_photo_status()

    def _update_photo_status(self) -> None:
        if self.selected_photo:
            self.photo_status_var.set(
                self.parent.tr(
                    "photo_selected",
                    name=Path(self.selected_photo).name,
                )
            )
            preview_path = Path(self.selected_photo)
        elif self.use_default_photos:
            self.photo_status_var.set(self.parent.tr("will_use_default"))
            preview_path = None
        elif self.existing is not None:
            custom = not (
                is_default_image(self.existing.entrance_image)
                and is_default_image(self.existing.exit_image)
            )
            self.photo_status_var.set(
                self.parent.tr("current_custom")
                if custom
                else self.parent.tr("current_default")
            )
            preview_path = (
                self.store.image_path(self.existing.entrance_image) if custom else None
            )
        else:
            self.photo_status_var.set(self.parent.tr("not_selected_default"))
            preview_path = None
        self._render_preview(preview_path)

    def _render_preview(self, path: Optional[Path]) -> None:
        self._preview_image = None
        if path is None or not path.is_file():
            self.preview_label.configure(
                image="",
                text=self.parent.tr("preview_default"),
                bg="#E9EFF8",
            )
            return
        try:
            from PIL import Image, ImageTk

            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((88, 88))
                self._preview_image = ImageTk.PhotoImage(preview)
            self.preview_label.configure(
                image=self._preview_image,
                text="",
                bg="#E9EFF8",
            )
        except Exception:
            self.preview_label.configure(
                image="",
                text=self.parent.tr("preview_selected"),
                bg="#E9EFF8",
            )

    def _save(self) -> None:
        self.error_var.set("")
        student_id = self.sid_var.get().strip()
        if not student_id:
            self.error_var.set(self.parent.tr("student_id_required"))
            self.id_entry.focus_set()
            return
        if not student_id.isdigit():
            self.error_var.set(self.parent.tr("student_id_digits"))
            self.id_entry.focus_set()
            return
        if self.existing is None:
            try:
                duplicate = self.store.get_user_model(student_id)
            except (KeyError, ValueError):
                duplicate = None
            if duplicate is not None and not messagebox.askyesno(
                self.parent.tr("update_existing_title"),
                self.parent.tr("update_existing_prompt", user=duplicate.display()),
                parent=self,
            ):
                return

        self.save_button.configure(
            state="disabled",
            text=self.parent.tr("saving"),
        )
        photo_paths = [self.selected_photo] if self.selected_photo else None
        campus = (
            "学院路"
            if self.campus_var.get() == self.campus_values[0]
            else "沙河"
        )
        try:
            user = setup_user(
                self.store,
                student_id=student_id,
                campus=campus,
                name=self.name_var.get(),
                photo_paths=photo_paths,
                use_default_photos=self.use_default_photos,
            )
        except Exception as exc:
            self.save_button.configure(
                state="normal",
                text=self.parent.tr("save_member"),
            )
            self.error_var.set(
                self.parent.tr("save_failed", error=exc)
            )
            return
        self.saved_user = user
        if self.on_saved is not None:
            self.on_saved(user)
        self.destroy()


def users_by_id(users: list[Any]) -> dict[str, Any]:
    return {user.student_id: user for user in users}


def _tail_text(path: Path, max_lines: int = 160) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 128 * 1024))
            raw = handle.read()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:]) + ("\n" if lines else "")


def _center_window(window: tk.Toplevel, parent: tk.Tk, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _win_rect() -> wintypes.RECT:
    return wintypes.RECT()


def _enable_windows_ui() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BUAA.TD.Console.1"
        )
    except Exception:
        pass


def _activate_existing_console() -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            hwnd = user32.FindWindowW(None, APP_TITLE_EN)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def main() -> int:
    try:
        return run_gui()
    except Exception as exc:
        try:
            store = Store()
            store.initialize()
            error_path = store.logs_dir / "gui-error.log"
            with error_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{datetime.now(BEIJING_TZ).isoformat(timespec='seconds')} {exc}\n"
                )
        except Exception:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("BUAA TD Console startup failed", str(exc), parent=root)
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
