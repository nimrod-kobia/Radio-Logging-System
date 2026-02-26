import os
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

from rc_config import ROOT, REFRESH_INTERVAL_MS
from rc_logs import station_log_path
from rc_power import PowerManager
from rc_process import is_monitor_running, open_path, start_monitor, stop_background
from rc_station_store import read_stations, validate_station, write_stations_atomic
from rc_status import build_station_status, day_file_display_entries


class RadioControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Brandcomm Radio Control")
        self.root.geometry("1280x720")
        self.refresh_job = None

        self.monitor_state_var = tk.StringVar(value="Monitor: checking...")
        self.last_refresh_var = tk.StringVar(value="Last refresh: -")
        self.action_state_var = tk.StringVar(value="Action: ready")
        self.station_name_var = tk.StringVar(value="")
        self.station_url_var = tk.StringVar(value="")
        self.day_offset = 0
        self.day_title_var = tk.StringVar(value="Day files: Today")
        self.power_manager = PowerManager()
        self.keep_awake_enabled = self.power_manager.enable_keep_awake()
        self.system_started = is_monitor_running()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_statuses()

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, textvariable=self.monitor_state_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.last_refresh_var).pack(side="left", padx=(20, 0))
        ttk.Label(top, textvariable=self.action_state_var).pack(side="left", padx=(20, 0))

        button_bar = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        button_bar.pack(fill="x")

        ttk.Button(button_bar, text="Start Monitor (Background)", command=self.start_monitor_gui).pack(side="left")
        ttk.Button(button_bar, text="Stop Background Processes", command=self.stop_background_gui).pack(side="left", padx=8)
        ttk.Button(button_bar, text="Refresh Now", command=self.manual_refresh).pack(side="left")
        ttk.Button(button_bar, text="Open Selected Log", command=self.open_selected_log).pack(side="left", padx=(8, 0))

        station_bar = ttk.LabelFrame(self.root, text="Station Management", padding=10)
        station_bar.pack(fill="x", padx=10, pady=(0, 8))

        ttk.Label(station_bar, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(station_bar, textvariable=self.station_name_var, width=30).grid(row=1, column=0, padx=(0, 10), sticky="we")

        ttk.Label(station_bar, text="Stream URL").grid(row=0, column=1, sticky="w")
        ttk.Entry(station_bar, textvariable=self.station_url_var, width=80).grid(row=1, column=1, padx=(0, 10), sticky="we")

        ttk.Button(station_bar, text="Add Station", command=self.add_station).grid(row=1, column=2, padx=(0, 8))
        ttk.Button(station_bar, text="Remove Selected", command=self.remove_selected_station).grid(row=1, column=3)

        station_bar.grid_columnconfigure(1, weight=1)

        day_bar = ttk.LabelFrame(self.root, text="Selected Station Day View", padding=10)
        day_bar.pack(fill="x", padx=10, pady=(0, 8))

        ttk.Button(day_bar, text="Yesterday", command=lambda: self.set_day_offset(-1)).pack(side="left")
        ttk.Button(day_bar, text="Today", command=lambda: self.set_day_offset(0)).pack(side="left", padx=6)
        ttk.Button(day_bar, text="Tomorrow", command=lambda: self.set_day_offset(1)).pack(side="left")
        ttk.Label(day_bar, textvariable=self.day_title_var).pack(side="left", padx=(16, 0))

        day_list_frame = ttk.Frame(day_bar)
        day_list_frame.pack(fill="both", expand=True, pady=(8, 0))

        day_yscroll = ttk.Scrollbar(day_list_frame, orient="vertical")
        day_xscroll = ttk.Scrollbar(day_list_frame, orient="horizontal")

        self.day_files_list = tk.Listbox(
            day_list_frame,
            height=5,
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

        self.tree.column("station", width=200, anchor="w")
        self.tree.column("status", width=120, anchor="w")
        self.tree.column("issue", width=320, anchor="w")
        self.tree.column("detail", width=420, anchor="w")
        self.tree.column("latest", width=220, anchor="w")

        yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        xscroll.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        activity_wrap = ttk.LabelFrame(self.root, text="Activity", padding=10)
        activity_wrap.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        self.activity_list = tk.Listbox(activity_wrap, height=6)
        self.activity_list.pack(fill="both", expand=True)

    def selected_station_name(self) -> str:
        selected = self.tree.selection()
        if selected:
            values = self.tree.item(selected[0], "values")
            if values:
                return str(values[0]).strip()
        return self.station_name_var.get().strip()

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

    def manual_refresh(self):
        self.log_action("Refresh requested")
        self.refresh_statuses()

    def start_monitor_gui(self):
        self.log_action("Start monitor requested")
        already_running = is_monitor_running()
        error = start_monitor()
        if error:
            self.log_action(f"Start failed: {error}")
            messagebox.showerror("Monitor", error)
            return
        if already_running:
            self.log_action("Monitor already running")
        else:
            self.log_action("Monitor started in background")
        self.system_started = True
        time.sleep(1)
        self.refresh_statuses()

    def stop_background_gui(self):
        self.log_action("Stop processes requested")
        stop_background()
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

        self.refresh_day_files()

    def set_day_offset(self, offset: int):
        self.day_offset = offset
        if offset == -1:
            self.log_action("Day view set: Yesterday")
        elif offset == 0:
            self.log_action("Day view set: Today")
        else:
            self.log_action("Day view set: Tomorrow")
        self.refresh_day_files()

    def refresh_day_files(self):
        station_name = self.selected_station_name()

        self.day_files_list.delete(0, tk.END)
        if not station_name:
            self.day_title_var.set("Day files: select a station")
            return

        day_label, entries = day_file_display_entries(station_name, self.day_offset)
        self.day_title_var.set(f"Day files for {station_name}: {day_label}")

        if not entries:
            self.day_files_list.insert(tk.END, "No files for this day yet.")
            return

        for entry in entries[:200]:
            self.day_files_list.insert(tk.END, entry)

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

    def refresh_statuses(self):
        if self.refresh_job is not None:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None

        running = is_monitor_running()
        self.monitor_state_var.set("Monitor: RUNNING (background)" if running else "Monitor: STOPPED")

        for row in self.tree.get_children():
            self.tree.delete(row)

        stations = read_stations()

        if running:
            self.system_started = True

        if not self.system_started:
            for station_name, _stream in stations:
                self.tree.insert(
                    "",
                    "end",
                    values=(station_name, "IDLE", "-", "Press Start Monitor to begin", "-"),
                )
            self.day_files_list.delete(0, tk.END)
            self.day_title_var.set("Day files: system not started")
            self.last_refresh_var.set(f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.refresh_job = self.root.after(REFRESH_INTERVAL_MS, self.refresh_statuses)
            return

        for station_name, _stream in stations:
            status, detail, latest, issue = build_station_status(station_name)
            self.tree.insert("", "end", values=(station_name, status, issue, detail, latest))

        self.refresh_day_files()

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

        if self.keep_awake_enabled:
            self.power_manager.disable_keep_awake()

        self.root.destroy()


def main():
    os.chdir(ROOT)
    app_root = tk.Tk()
    _app = RadioControlApp(app_root)
    app_root.mainloop()


if __name__ == "__main__":
    main()
