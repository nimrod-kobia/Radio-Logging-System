import os
import threading
import time
import webbrowser
import calendar
from datetime import datetime, date, timedelta
import tkinter as tk
from tkinter import ttk, messagebox

from rc_config import ROOT, REFRESH_INTERVAL_MS, RECORDINGS
from rc_logs import station_log_path
from rc_power import PowerManager
from rc_preflight import PreflightReport, run_preflight_checks
from rc_process import is_monitor_running, open_path, start_monitor, stop_background
from rc_station_store import read_stations, validate_station, write_stations_atomic
from rc_status import build_station_status, day_file_display_entries, format_size, list_day_files
STARTUP_STATUS_GRACE_SECONDS = 120
RECORDING_SIZE_REFRESH_SECONDS = 30


class RadioControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Brandcomm Radio Control")

        # Size the window relative to the screen, within sensible bounds.
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(1600, max(1000, sw - 120))
        h = min(960, max(620, sh - 120))
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(820, 520)

        self.refresh_job = None

        self.monitor_state_var = tk.StringVar(value="Monitor: checking...")
        self.readiness_var = tk.StringVar(value="System Ready: checking...")
        self.last_refresh_var = tk.StringVar(value="Last refresh: -")
        self.action_state_var = tk.StringVar(value="Action: ready")
        self.station_stats_var = tk.StringVar(value="Stations: 0")
        self.recordings_stats_var = tk.StringVar(value="Recordings (Total): 0 bytes")
        self.recording_scope_var = tk.StringVar(value="Total")
        self.recording_sizes_cache = {
            "Day": 0,
            "Week": 0,
            "Month": 0,
            "Total": 0,
        }
        self.station_name_var = tk.StringVar(value="")
        self.station_url_var = tk.StringVar(value="")
        self.selected_day: date | None = None
        self.day_files_paths = []
        self.day_title_var = tk.StringVar(value="Day files: Today")
        self.power_manager = PowerManager()
        self.keep_awake_enabled = self.power_manager.enable_keep_awake()
        self.system_started = False
        self.preflight_report: PreflightReport | None = None
        self.action_in_progress = False
        self.action_progress_var = tk.StringVar(value="Idle")
        self._action_spinner_job = None
        self._action_spinner_frames = ("|", "/", "-", "\\")
        self._action_spinner_index = 0
        self._action_progress_base = "Working"
        self._last_running_state = False
        self.monitor_started_at_ts: float | None = None
        self._recording_sizes_last_compute_ts = 0.0

        # Email alert settings
        self.alert_enabled_var = tk.BooleanVar(value=False)
        self.alert_smtp_host_var = tk.StringVar(value="smtp.gmail.com")
        self.alert_smtp_port_var = tk.StringVar(value="465")
        self.alert_sender_var = tk.StringVar(value="")
        self.alert_new_recipient_var = tk.StringVar(value="")
        self.alert_recipients: list = []
        self.alert_password_var = tk.StringVar(value="")
        self.alert_threshold_var = tk.StringVar(value="5")
        self.alert_service_crash_var = tk.BooleanVar(value=False)
        self.alert_use_ssl_var = tk.BooleanVar(value=True)
        self._email_config_path = ROOT / "email_config.json"
        self._alert_recipients_listbox: tk.Listbox | None = None
        self._load_email_config_vars()

        self._build_ui()
        self.run_startup_checks(show_dialog=False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_statuses()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, textvariable=self.monitor_state_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.readiness_var).pack(side="left", padx=(20, 0))
        ttk.Label(top, textvariable=self.last_refresh_var).pack(side="left", padx=(20, 0))
        ttk.Label(top, textvariable=self.action_state_var).pack(side="left", padx=(20, 0))
        ttk.Label(top, textvariable=self.recordings_stats_var).pack(side="right")
        self.recording_scope_combo = ttk.Combobox(
            top,
            state="readonly",
            width=7,
            values=("Day", "Week", "Month", "Total"),
            textvariable=self.recording_scope_var,
        )
        self.recording_scope_combo.bind("<<ComboboxSelected>>", self.on_recording_scope_change)
        self.recording_scope_combo.pack(side="right", padx=(0, 8))
        ttk.Label(top, textvariable=self.station_stats_var).pack(side="right", padx=(0, 16))

        button_bar = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        button_bar.pack(fill="x")

        self.start_button = ttk.Button(button_bar, text="Start Recording", command=self.start_monitor_gui)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(button_bar, text="Stop Recording", command=self.stop_background_gui)
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(button_bar, textvariable=self.action_progress_var).pack(side="left", padx=(10, 0))
        ttk.Button(button_bar, text="Open Selected Log", command=self.open_selected_log).pack(side="left", padx=(8, 0))
        ttk.Button(button_bar, text="Run Self Check", command=lambda: self.run_startup_checks(show_dialog=True)).pack(side="left", padx=(8, 0))
        station_bar = ttk.LabelFrame(self.root, text="Station Management", padding=10)
        station_bar.pack(fill="x", padx=10, pady=(0, 8))

        ttk.Label(station_bar, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(station_bar, textvariable=self.station_name_var, width=30).grid(row=1, column=0, padx=(0, 10), sticky="we")

        ttk.Label(station_bar, text="Stream URL").grid(row=0, column=1, sticky="w")
        ttk.Entry(station_bar, textvariable=self.station_url_var, width=80).grid(row=1, column=1, padx=(0, 10), sticky="we")

        ttk.Button(station_bar, text="Go", command=self.open_selected_stream).grid(row=1, column=2, padx=(0, 8))
        ttk.Button(station_bar, text="Add Station", command=self.add_station).grid(row=1, column=3, padx=(0, 8))
        ttk.Button(station_bar, text="Remove Selected", command=self.remove_selected_station).grid(row=1, column=4)

        station_bar.grid_columnconfigure(1, weight=1)

        # ── Email Alerts section (compact 2-row layout) ──────────────────────
        email_bar = ttk.LabelFrame(self.root, text="Email Alerts", padding=(8, 4))
        email_bar.pack(fill="x", padx=10, pady=(0, 4))

        # Row 0 — all SMTP settings + save/test on one line
        r0 = ttk.Frame(email_bar)
        r0.pack(fill="x")
        ttk.Checkbutton(r0, text="Enable", variable=self.alert_enabled_var).pack(side="left")
        ttk.Label(r0, text="Host:").pack(side="left", padx=(10, 2))
        ttk.Entry(r0, textvariable=self.alert_smtp_host_var, width=18).pack(side="left")
        ttk.Label(r0, text="Port:").pack(side="left", padx=(6, 2))
        ttk.Entry(r0, textvariable=self.alert_smtp_port_var, width=5).pack(side="left")
        ttk.Checkbutton(r0, text="SSL", variable=self.alert_use_ssl_var).pack(side="left", padx=(6, 0))
        ttk.Label(r0, text="From:").pack(side="left", padx=(10, 2))
        ttk.Entry(r0, textvariable=self.alert_sender_var, width=20).pack(side="left")
        ttk.Label(r0, text="Pass:").pack(side="left", padx=(6, 2))
        ttk.Entry(r0, textvariable=self.alert_password_var, width=16, show="*").pack(side="left")
        ttk.Label(r0, text="After:").pack(side="left", padx=(10, 2))
        ttk.Entry(r0, textvariable=self.alert_threshold_var, width=4).pack(side="left")
        ttk.Label(r0, text="min").pack(side="left", padx=(2, 6))
        ttk.Checkbutton(r0, text="Crash alerts", variable=self.alert_service_crash_var).pack(side="left")
        ttk.Button(r0, text="Save", command=self._save_email_config).pack(side="right")
        ttk.Button(r0, text="Test", command=self._test_email_alert).pack(side="right", padx=(0, 4))

        # Row 1 — recipients
        r1 = ttk.Frame(email_bar)
        r1.pack(fill="x", pady=(4, 0))
        ttk.Label(r1, text="To:").pack(side="left", padx=(0, 4))
        ttk.Entry(r1, textvariable=self.alert_new_recipient_var, width=26).pack(side="left")
        ttk.Button(r1, text="Add", command=self._add_alert_recipient).pack(side="left", padx=(4, 8))
        self._alert_recipients_listbox = tk.Listbox(
            r1, height=2, selectmode=tk.SINGLE, exportselection=False
        )
        self._alert_recipients_listbox.pack(side="left", fill="x", expand=True)
        self._refresh_recipients_listbox()
        ttk.Button(r1, text="Remove", command=self._remove_alert_recipient).pack(side="left", padx=(6, 0))

        day_bar = ttk.LabelFrame(self.root, text="Selected Station Day View", padding=(8, 4))
        day_bar.pack(fill="x", padx=10, pady=(0, 4))

        day_top = ttk.Frame(day_bar)
        day_top.pack(fill="x")
        ttk.Button(day_top, text="Yesterday", command=lambda: self.set_day_offset(-1)).pack(side="left")
        ttk.Button(day_top, text="Today", command=lambda: self.set_day_offset(0)).pack(side="left", padx=4)
        ttk.Button(day_top, text="Pick Date", command=self.open_day_picker).pack(side="left", padx=(0, 4))
        ttk.Button(day_top, text="Play Selected", command=self.play_selected_day_file).pack(side="left", padx=(4, 0))
        ttk.Label(day_top, textvariable=self.day_title_var).pack(side="left", padx=(12, 0))

        day_list_frame = ttk.Frame(day_bar)
        day_list_frame.pack(fill="both", expand=True, pady=(4, 0))

        day_yscroll = ttk.Scrollbar(day_list_frame, orient="vertical")
        day_xscroll = ttk.Scrollbar(day_list_frame, orient="horizontal")

        self.day_files_list = tk.Listbox(
            day_list_frame,
            height=3,
            yscrollcommand=day_yscroll.set,
            xscrollcommand=day_xscroll.set,
        )
        day_yscroll.config(command=self.day_files_list.yview)
        day_xscroll.config(command=self.day_files_list.xview)

        day_xscroll.pack(side="bottom", fill="x")
        day_yscroll.pack(side="right", fill="y")
        self.day_files_list.pack(side="left", fill="both", expand=True)

        info = ttk.Label(
            self.root,
            padding=(10, 0, 10, 8),
            text="Monitor runs in background. Select a station and click 'Open Selected Log' for exact error details.",
        )
        info.pack(fill="x")

        table_wrap = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        table_wrap.pack(fill="both", expand=True)

        columns = ("station", "status", "issue", "detail", "latest")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings", height=20)

        self.tree.heading("station", text="Station")
        self.tree.heading("status", text="Status")
        self.tree.heading("issue", text="Issue")
        self.tree.heading("detail", text="Detail")
        self.tree.heading("latest", text="Latest File")

        self.tree.column("station", width=200, anchor="w", stretch=False, minwidth=140)
        self.tree.column("status", width=110, anchor="w", stretch=False, minwidth=90)
        self.tree.column("issue", width=300, anchor="w", stretch=True, minwidth=140)
        self.tree.column("detail", width=380, anchor="w", stretch=True, minwidth=160)
        self.tree.column("latest", width=200, anchor="w", stretch=False, minwidth=140)

        yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.tag_configure("offline", foreground="#cc0000")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Button-1>", self._block_column_resize)
        self.tree.bind("<B1-Motion>", self._block_column_resize)

        xscroll.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        activity_wrap = ttk.LabelFrame(self.root, text="Activity", padding=10)
        activity_wrap.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        activity_frame = ttk.Frame(activity_wrap)
        activity_frame.pack(fill="both", expand=True)

        activity_scroll = ttk.Scrollbar(activity_frame, orient="vertical")
        self.activity_list = tk.Listbox(activity_frame, height=6, yscrollcommand=activity_scroll.set)
        activity_scroll.config(command=self.activity_list.yview)

        self.activity_list.pack(side="left", fill="both", expand=True)
        activity_scroll.pack(side="right", fill="y")

    def selected_station_name(self) -> str:
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0], "values")
            if values:
                return str(values[0]).strip()
        return self.station_name_var.get().strip()

    def _block_column_resize(self, event) -> str | None:
        if self.tree.identify_region(event.x, event.y) == "separator":
            return "break"
        return None

    def log_action(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.action_state_var.set(f"Action: {message}")
        self.activity_list.insert(tk.END, line)
        max_rows = 300
        current = self.activity_list.size()
        if current > max_rows:
            self.activity_list.delete(0, current - max_rows - 1)
        self.activity_list.yview_moveto(1.0)

    def recording_size_summary_bytes(self) -> tuple[int, int, int, int]:
        day_bytes = 0
        week_bytes = 0
        month_bytes = 0
        total_bytes = 0

        if not RECORDINGS.exists():
            return day_bytes, week_bytes, month_bytes, total_bytes

        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=6)
        month_start = day_start.replace(day=1)

        for root_dir, _dir_names, file_names in os.walk(RECORDINGS):
            for file_name in file_names:
                file_path = os.path.join(root_dir, file_name)
                try:
                    file_size = os.path.getsize(file_path)
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                except OSError:
                    continue

                total_bytes += file_size
                if mtime >= month_start:
                    month_bytes += file_size
                if mtime >= week_start:
                    week_bytes += file_size
                if mtime >= day_start:
                    day_bytes += file_size

        return day_bytes, week_bytes, month_bytes, total_bytes

    def _update_recording_size_display(self):
        scope = self.recording_scope_var.get().strip() or "Total"
        if scope not in self.recording_sizes_cache:
            scope = "Total"
            self.recording_scope_var.set(scope)

        value = self.recording_sizes_cache.get(scope, 0)
        self.recordings_stats_var.set(f"Recordings ({scope}): {format_size(value)}")

    def on_recording_scope_change(self, _event=None):
        self._update_recording_size_display()

    def _refresh_recording_size_cache(self, force: bool = False):
        now_ts = time.time()
        if not force and (now_ts - self._recording_sizes_last_compute_ts) < RECORDING_SIZE_REFRESH_SECONDS:
            return

        day_size, week_size, month_size, total_size = self.recording_size_summary_bytes()
        self.recording_sizes_cache = {
            "Day": day_size,
            "Week": week_size,
            "Month": month_size,
            "Total": total_size,
        }
        self._recording_sizes_last_compute_ts = now_ts

    def _apply_startup_status_grace(self, status: str, detail: str, latest: str, issue: str) -> tuple[str, str, str, str]:
        if self.monitor_started_at_ts is None:
            return status, detail, latest, issue

        elapsed = time.time() - self.monitor_started_at_ts
        if elapsed >= STARTUP_STATUS_GRACE_SECONDS:
            return status, detail, latest, issue

        if status in {"NO WRITE", "NO AUDIO"}:
            remaining = max(0, int(STARTUP_STATUS_GRACE_SECONDS - elapsed))
            return "STARTING", f"startup grace ({remaining}s)", latest, "-"

        return status, detail, latest, issue

    def _preflight_summary_lines(self) -> list[str]:
        if self.preflight_report is None:
            return ["No startup check data yet."]

        lines: list[str] = []
        for check in self.preflight_report.checks:
            status = "OK" if check.ok else "FAIL"
            scope = "CRITICAL" if check.critical else "OPTIONAL"
            lines.append(f"[{status}] {check.name} ({scope}) - {check.detail}")
        return lines

    def run_startup_checks(self, show_dialog: bool):
        self.preflight_report = run_preflight_checks()

        critical_failures = self.preflight_report.critical_failures
        if critical_failures:
            self.readiness_var.set(f"System Ready: BLOCKED ({len(critical_failures)} critical checks failed)")
            self.log_action("Startup self-check: blocked")
        else:
            optional_failures = self.preflight_report.noncritical_failures
            if optional_failures:
                self.readiness_var.set(f"System Ready: YES ({len(optional_failures)} warnings)")
                self.log_action("Startup self-check: ready with warnings")
            else:
                self.readiness_var.set("System Ready: YES")
                self.log_action("Startup self-check: passed")

        if show_dialog:
            details = "\n".join(self._preflight_summary_lines())
            if critical_failures:
                messagebox.showerror("Startup self-check", details)
            else:
                messagebox.showinfo("Startup self-check", details)

    def _set_action_buttons_enabled(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        try:
            self.start_button.config(state=state)
            self.stop_button.config(state=state)
        except tk.TclError:
            pass

    def _tick_action_spinner(self):
        if not self.action_in_progress:
            return
        frame = self._action_spinner_frames[self._action_spinner_index]
        self._action_spinner_index = (self._action_spinner_index + 1) % len(self._action_spinner_frames)
        self.action_progress_var.set(f"{self._action_progress_base}... {frame}")
        self._action_spinner_job = self.root.after(180, self._tick_action_spinner)

    def _set_action_in_progress(self, in_progress: bool, base_label: str = "Working"):
        self.action_in_progress = in_progress
        self._set_action_buttons_enabled(not in_progress)
        if in_progress:
            self._action_progress_base = base_label
            self._action_spinner_index = 0
            if self._action_spinner_job is not None:
                try:
                    self.root.after_cancel(self._action_spinner_job)
                except tk.TclError:
                    pass
                self._action_spinner_job = None
            self._tick_action_spinner()
        else:
            if self._action_spinner_job is not None:
                try:
                    self.root.after_cancel(self._action_spinner_job)
                except tk.TclError:
                    pass
                self._action_spinner_job = None
            running = is_monitor_running()
            self.action_progress_var.set("Recording" if running else "Idle")

    def start_monitor_gui(self):
        if self.action_in_progress:
            self.log_action("Start ignored: action already in progress")
            return

        self.run_startup_checks(show_dialog=False)
        if self.preflight_report is not None and self.preflight_report.critical_failures:
            self.log_action("Start blocked by startup self-check")
            details = "\n".join(self._preflight_summary_lines())
            messagebox.showerror("Monitor", f"Cannot start monitor until critical checks pass.\n\n{details}")
            return

        self.log_action("Start monitor requested")
        self._set_action_in_progress(True, "Starting")

        def worker():
            already_running = is_monitor_running()
            error = start_monitor()
            self.root.after(0, lambda: self._on_start_monitor_complete(already_running, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_start_monitor_complete(self, already_running: bool, error: str | None):
        self._set_action_in_progress(False)
        if error:
            self.log_action(f"Start failed: {error}")
            messagebox.showerror("Monitor", error)
            return

        if already_running:
            self.log_action("Monitor already running")
        else:
            self.log_action("Monitor started in background")
        self.system_started = True
        self.refresh_statuses()

    def stop_background_gui(self):
        if self.action_in_progress:
            self.log_action("Stop ignored: action already in progress")
            return

        self.log_action("Stop processes requested")
        self._set_action_in_progress(True, "Stopping")

        def worker():
            stop_background()
            self.root.after(0, self._on_stop_background_complete)

        threading.Thread(target=worker, daemon=True).start()

    def _on_stop_background_complete(self):
        self._set_action_in_progress(False)
        self.system_started = False
        self.log_action("Background processes stopped")
        self.refresh_statuses()

    def add_station(self):
        name = self.station_name_var.get().strip()
        stream = self.station_url_var.get().strip()
        self.log_action(f"Add station requested: {name or '(empty)'}")

        validation_error = validate_station(name, stream)
        if validation_error:
            self.log_action(f"Add station rejected: {validation_error}")
            messagebox.showerror("Invalid station", validation_error)
            return

        stations = read_stations()
        existing_names = {station_name.lower() for station_name, _ in stations}
        if name.lower() in existing_names:
            self.log_action(f"Add station rejected: duplicate {name}")
            messagebox.showerror("Duplicate station", f"Station already exists: {name}")
            return

        stations.append((name, stream))
        write_stations_atomic(stations)
        self.station_name_var.set("")
        self.station_url_var.set("")
        self.log_action(f"Station added: {name}")
        self.refresh_statuses()

    def remove_selected_station(self):
        station_name = self.selected_station_name()
        self.log_action(f"Remove station requested: {station_name or '(empty)'}")
        if not station_name:
            self.log_action("Remove station failed: no station selected")
            messagebox.showerror("Remove station", "Select a station from the table or enter station name.")
            return

        stations = read_stations()
        filtered = [(name, stream) for name, stream in stations if name.lower() != station_name.lower()]
        if len(filtered) == len(stations):
            self.log_action(f"Remove station failed: {station_name} not found")
            messagebox.showerror("Not found", f"Station not found: {station_name}")
            return

        write_stations_atomic(filtered)
        self.station_name_var.set("")
        self.station_url_var.set("")
        self.log_action(f"Station removed: {station_name}")
        self.refresh_statuses()

    def on_tree_select(self, _event):
        station_name = self.selected_station_name()
        if not station_name:
            return

        self.station_name_var.set(station_name)
        for name, stream in read_stations():
            if name == station_name:
                self.station_url_var.set(stream)
                break

        # Pass already-known running state to avoid re-checking is_monitor_running()
        # on every click (which could misread stale ffmpeg pids mid-stop)
        self.refresh_day_files(monitor_running=self.system_started)

    def set_day_offset(self, offset: int):
        self.selected_day = date.today() + timedelta(days=offset)
        if offset < 0:
            self.log_action("Day view set: Yesterday")
        else:
            self.log_action("Day view set: Today")
        self.refresh_day_files()

    def open_day_picker(self):
        base_day = self.selected_day or date.today()

        picker = tk.Toplevel(self.root)
        picker.title("Select Day")
        picker.transient(self.root)
        picker.grab_set()
        picker.resizable(False, False)

        frame = ttk.Frame(picker, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Year").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Month").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(frame, text="Day").grid(row=0, column=2, sticky="w", padx=(8, 0))

        year_var = tk.IntVar(value=base_day.year)
        month_var = tk.IntVar(value=base_day.month)
        day_var = tk.IntVar(value=base_day.day)

        max_year = date.today().year
        year_spin = tk.Spinbox(frame, from_=2000, to=max_year, textvariable=year_var, width=8)
        month_spin = tk.Spinbox(frame, from_=1, to=12, textvariable=month_var, width=6)
        day_spin = tk.Spinbox(frame, from_=1, to=31, textvariable=day_var, width=6)

        year_spin.grid(row=1, column=0, sticky="w")
        month_spin.grid(row=1, column=1, sticky="w", padx=(8, 0))
        day_spin.grid(row=1, column=2, sticky="w", padx=(8, 0))

        def sync_day_limits(*_args):
            try:
                y = int(year_var.get())
                m = int(month_var.get())
            except (tk.TclError, ValueError):
                return

            m = max(1, min(12, m))
            year_var.set(max(2000, min(max_year, y)))
            month_var.set(m)

            max_day = calendar.monthrange(year_var.get(), month_var.get())[1]
            current_day = day_var.get()
            if current_day > max_day:
                day_var.set(max_day)
            day_spin.config(to=max_day)

        year_var.trace_add("write", sync_day_limits)
        month_var.trace_add("write", sync_day_limits)
        sync_day_limits()

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=3, pady=(12, 0), sticky="e")

        def apply_selected_day():
            try:
                selected = date(int(year_var.get()), int(month_var.get()), int(day_var.get()))
            except ValueError:
                messagebox.showerror("Select Day", "Invalid date selected.", parent=picker)
                return

            if selected > date.today():
                messagebox.showerror("Select Day", "Future dates are not allowed.", parent=picker)
                return

            self.selected_day = selected
            self.log_action(f"Day view set: {selected.strftime('%Y-%m-%d')}")
            picker.destroy()
            self.refresh_day_files()

        def choose_today():
            self.selected_day = date.today()
            self.log_action("Day view set: Today")
            picker.destroy()
            self.refresh_day_files()

        ttk.Button(button_row, text="Today", command=choose_today).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Apply", command=apply_selected_day).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Cancel", command=picker.destroy).pack(side="left")

    def refresh_day_files(self, monitor_running: bool | None = None):
        station_name = self.selected_station_name()

        previous_selection = self.day_files_list.curselection()
        previous_index = int(previous_selection[0]) if previous_selection else None
        try:
            previous_top_fraction = self.day_files_list.yview()[0]
        except Exception:
            previous_top_fraction = 0.0

        self.day_files_list.delete(0, tk.END)
        self.day_files_paths = []
        if not station_name:
            self.day_title_var.set("Day files: select a station")
            return

        running = is_monitor_running() if monitor_running is None else monitor_running
        _day_label_paths, files = list_day_files(station_name, target_day=self.selected_day)
        day_label, entries = day_file_display_entries(
            station_name,
            target_day=self.selected_day,
            monitor_running=running,
        )
        self.day_title_var.set(f"Day files for {station_name}: {day_label}")

        if not entries:
            self.day_files_list.insert(tk.END, "No files for this day yet.")
            return

        self.day_files_paths = files[:200]
        for entry in entries[:200]:
            self.day_files_list.insert(tk.END, entry)

        item_count = len(self.day_files_paths)
        if previous_index is not None and 0 <= previous_index < item_count:
            self.day_files_list.selection_set(previous_index)

        if item_count > 0:
            max_fraction = 1.0
            clamped_fraction = max(0.0, min(max_fraction, previous_top_fraction))
            self.day_files_list.yview_moveto(clamped_fraction)

    def play_selected_day_file(self):
        station_name = self.selected_station_name()
        if not station_name:
            self.log_action("Play recording failed: no station selected")
            messagebox.showerror("Play recording", "Select a station first.")
            return

        selection = self.day_files_list.curselection()
        if not selection:
            self.log_action("Play recording failed: no recording selected")
            messagebox.showerror("Play recording", "Select a recording from Day files first.")
            return

        index = int(selection[0])
        if index < 0 or index >= len(self.day_files_paths):
            self.log_action("Play recording failed: invalid day-file selection")
            messagebox.showerror("Play recording", "Selected row is not a recording file.")
            return

        target_file = self.day_files_paths[index]
        if not target_file.exists():
            self.log_action(f"Play recording failed: file missing {target_file.name}")
            messagebox.showerror("Play recording", f"Recording file not found:\n{target_file}")
            return

        try:
            open_path(target_file)
            self.log_action(f"Playing recording: {target_file.name}")
        except Exception as exc:
            self.log_action(f"Play recording failed: {exc}")
            messagebox.showerror("Play recording", f"Could not open recording file.\n{exc}")

    def open_selected_log(self):
        station_name = self.selected_station_name()
        if not station_name:
            self.log_action("Open log failed: no station selected")
            messagebox.showerror("Open log", "Select a station first.")
            return

        log_path = station_log_path(station_name)
        if not log_path.exists():
            self.log_action(f"Open log: no file for {station_name}")
            messagebox.showinfo("Open log", f"No log file found for {station_name}.")
            return

        try:
            open_path(log_path)
            self.log_action(f"Opened log: {station_name}")
        except Exception as exc:
            self.log_action(f"Open log failed for {station_name}: {exc}")
            messagebox.showerror("Open log", f"Could not open log file.\n{exc}")

    def open_selected_stream(self):
        station_name = self.selected_station_name()
        stream_url = self.station_url_var.get().strip()

        if not stream_url and station_name:
            for name, stream in read_stations():
                if name == station_name:
                    stream_url = stream.strip()
                    self.station_url_var.set(stream_url)
                    break

        if not stream_url:
            self.log_action("Open stream failed: no URL available")
            messagebox.showerror("Open stream", "Select a station with a valid stream URL first.")
            return

        if not (stream_url.startswith("http://") or stream_url.startswith("https://")):
            self.log_action("Open stream failed: invalid URL")
            messagebox.showerror("Open stream", "Stream URL must start with http:// or https://")
            return

        try:
            webbrowser.open(stream_url, new=2)
            self.log_action(f"Opened stream URL for {station_name or 'selected station'}")
        except Exception as exc:
            self.log_action(f"Open stream failed: {exc}")
            messagebox.showerror("Open stream", f"Could not open browser.\n{exc}")

    def refresh_statuses(self):
        if self.refresh_job is not None:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None

        running = is_monitor_running()
        if running and not self._last_running_state:
            self.monitor_started_at_ts = time.time()
        if not running:
            self.monitor_started_at_ts = None
        self._last_running_state = running

        self.system_started = running
        self.monitor_state_var.set("Monitor: RUNNING (background)" if running else "Monitor: STOPPED")
        if not self.action_in_progress:
            self.action_progress_var.set("Recording" if running else "Idle")

        previously_selected_station = self.selected_station_name()
        station_row_map: dict[str, str] = {}

        for row in self.tree.get_children():
            self.tree.delete(row)

        stations = read_stations()
        self._refresh_recording_size_cache()
        self.station_stats_var.set(f"Stations: {len(stations)}")
        self._update_recording_size_display()

        if not self.system_started:
            for station_name, _stream in stations:
                row_id = self.tree.insert(
                    "",
                    "end",
                    values=(station_name, "IDLE", "-", "Press Start Monitor to begin", "-"),
                )
                station_row_map[station_name] = row_id

            if previously_selected_station and previously_selected_station in station_row_map:
                row_id = station_row_map[previously_selected_station]
                self.tree.selection_set(row_id)
                self.tree.focus(row_id)
                self.tree.see(row_id)

            self.day_files_list.delete(0, tk.END)
            self.day_title_var.set("Day files: system not started")
            self.last_refresh_var.set(f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.refresh_job = self.root.after(REFRESH_INTERVAL_MS, self.refresh_statuses)
            return

        for station_name, _stream in stations:
            status, detail, latest, issue = build_station_status(station_name)
            status, detail, latest, issue = self._apply_startup_status_grace(status, detail, latest, issue)
            tags = ("offline",) if status == "OFFLINE" else ()
            row_id = self.tree.insert("", "end", values=(station_name, status, issue, detail, latest), tags=tags)
            station_row_map[station_name] = row_id

        if previously_selected_station and previously_selected_station in station_row_map:
            row_id = station_row_map[previously_selected_station]
            self.tree.selection_set(row_id)
            self.tree.focus(row_id)
            self.tree.see(row_id)

        self.refresh_day_files(monitor_running=running)

        self.last_refresh_var.set(f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.refresh_job = self.root.after(REFRESH_INTERVAL_MS, self.refresh_statuses)

    def on_close(self):
        self.log_action("Application closing")
        if self.refresh_job is not None:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None

        if self._action_spinner_job is not None:
            try:
                self.root.after_cancel(self._action_spinner_job)
            except tk.TclError:
                pass
            self._action_spinner_job = None

        if self.keep_awake_enabled:
            self.power_manager.disable_keep_awake()

        self.root.destroy()

    # ── Email alert settings methods ──────────────────────────────────────────

    def _load_email_config_vars(self) -> None:
        """Populate alert StringVars from email_config.json if it exists."""
        try:
            if self._email_config_path.exists():
                import json as _json
                cfg = _json.loads(self._email_config_path.read_text(encoding="utf-8"))
                self.alert_enabled_var.set(bool(cfg.get("enabled", False)))
                self.alert_smtp_host_var.set(str(cfg.get("smtp_host", "smtp.gmail.com")))
                self.alert_smtp_port_var.set(str(cfg.get("smtp_port", 465)))
                self.alert_sender_var.set(str(cfg.get("sender_email", "")))
                self.alert_password_var.set(str(cfg.get("app_password", "")))
                self.alert_threshold_var.set(str(cfg.get("alert_threshold_minutes", 5)))
                self.alert_service_crash_var.set(bool(cfg.get("alert_on_service_crash", False)))
                self.alert_use_ssl_var.set(bool(cfg.get("use_ssl", True)))
                self.alert_recipients = list(cfg.get("recipient_emails", []))
        except Exception:
            pass

    def _refresh_recipients_listbox(self) -> None:
        """Sync the Listbox widget with self.alert_recipients."""
        if self._alert_recipients_listbox is None:
            return
        self._alert_recipients_listbox.delete(0, tk.END)
        for email in self.alert_recipients:
            self._alert_recipients_listbox.insert(tk.END, email)

    def _add_alert_recipient(self) -> None:
        email = self.alert_new_recipient_var.get().strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            messagebox.showwarning("Email Alerts", "Please enter a valid email address.")
            return
        if email.lower() in [r.lower() for r in self.alert_recipients]:
            messagebox.showwarning("Email Alerts", f"{email} is already in the list.")
            return
        self.alert_recipients.append(email)
        self._refresh_recipients_listbox()
        self.alert_new_recipient_var.set("")

    def _remove_alert_recipient(self) -> None:
        if self._alert_recipients_listbox is None:
            return
        selection = self._alert_recipients_listbox.curselection()
        if not selection:
            messagebox.showwarning("Email Alerts", "Select a recipient to remove.")
            return
        index = selection[0]
        del self.alert_recipients[index]
        self._refresh_recipients_listbox()

    def _save_email_config(self) -> None:
        """Write current alert settings to email_config.json atomically."""
        try:
            import json as _json, os as _os
            threshold_raw = self.alert_threshold_var.get().strip()
            threshold = int(threshold_raw) if threshold_raw.isdigit() and int(threshold_raw) > 0 else 5
            port_raw = self.alert_smtp_port_var.get().strip()
            port = int(port_raw) if port_raw.isdigit() else 465

            cfg = {
                "enabled": self.alert_enabled_var.get(),
                "smtp_host": self.alert_smtp_host_var.get().strip(),
                "smtp_port": port,
                "use_ssl": self.alert_use_ssl_var.get(),
                "sender_email": self.alert_sender_var.get().strip(),
                "recipient_emails": list(self.alert_recipients),
                "app_password": self.alert_password_var.get(),
                "alert_threshold_minutes": threshold,
                "alert_on_service_crash": self.alert_service_crash_var.get(),
            }

            temp = self._email_config_path.with_suffix(".json.tmp")
            temp.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            _os.replace(temp, self._email_config_path)
            self.log_action("Email alert settings saved")
        except Exception as exc:
            messagebox.showerror("Email Settings", f"Failed to save settings:\n{exc}")

    def _test_email_alert(self) -> None:
        """Save settings then fire a test email on a background thread."""
        self._save_email_config()
        if not self.alert_recipients:
            messagebox.showwarning("Email Test", "Add at least one recipient before testing.")
            return
        self.log_action("Sending test email...")

        def _worker():
            try:
                import json as _j, smtplib, ssl
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart

                cfg = _j.loads(self._email_config_path.read_text(encoding="utf-8"))
                sender = cfg.get("sender_email", "")
                recipients = cfg.get("recipient_emails", [])
                password = cfg.get("app_password", "")
                smtp_host = cfg.get("smtp_host", "smtp.gmail.com")
                smtp_port = int(cfg.get("smtp_port", 465))
                use_ssl = bool(cfg.get("use_ssl", True))

                msg = MIMEMultipart("alternative")
                msg["Subject"] = "[Radio Alert] Test email from Radio Control"
                msg["From"] = sender
                msg["To"] = ", ".join(recipients)
                msg.attach(MIMEText(
                    "This is a test email from the Radio Control alert system.\n"
                    "If you received this, your email settings are correct.",
                    "plain", "utf-8"
                ))
                context = ssl.create_default_context()
                if use_ssl:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15) as s:
                        s.login(sender, password)
                        s.sendmail(sender, recipients, msg.as_string())
                else:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
                        s.ehlo()
                        s.starttls(context=context)
                        s.ehlo()
                        s.login(sender, password)
                        s.sendmail(sender, recipients, msg.as_string())

                self.root.after(0, lambda: (
                    self.log_action("Test email sent successfully"),
                    messagebox.showinfo("Email Test", "Test email sent successfully!")
                ))
            except Exception as exc:
                self.root.after(0, lambda e=exc: (
                    self.log_action(f"Test email failed: {e}"),
                    messagebox.showerror("Email Test", f"Failed to send test email:\n{e}")
                ))

        threading.Thread(target=_worker, daemon=True).start()


def main():
    os.chdir(ROOT)
    app_root = tk.Tk()
    _app = RadioControlApp(app_root)
    app_root.mainloop()


if __name__ == "__main__":
    main()
