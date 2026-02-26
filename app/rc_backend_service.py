import os
import signal
import subprocess
import sys
import time
from datetime import date, timedelta, datetime
from pathlib import Path

from rc_config import LOGS, MONITOR_PID_FILE, RECORDINGS, is_windows, safe_station_name
from rc_station_store import read_stations

SERVICE_LOG = LOGS / "service.log"
SYNC_SECONDS = 5

if is_windows():
    FFMPEG_BIN = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
else:
    FFMPEG_BIN = Path("ffmpeg")

RUNNING = True


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class WorkerManager:
    def __init__(self):
        self.workers: dict[str, tuple[subprocess.Popen, object]] = {}
        self.restart_state: dict[str, tuple[int, float]] = {}

    @staticmethod
    def restart_backoff_seconds(fail_count: int) -> int:
        base = 5
        delay = base * (2 ** max(0, fail_count - 1))
        return min(300, delay)

    @staticmethod
    def station_paths(station_name: str) -> tuple[Path, Path, Path]:
        safe = safe_station_name(station_name)
        station_dir = RECORDINGS / safe
        log_path = LOGS / f"{safe}.log"
        pid_path = LOGS / f"{safe}.pid"
        return station_dir, log_path, pid_path

    @staticmethod
    def make_today_dir(station_dir: Path):
        """Create only today's recording folder."""
        today = date.today()
        day_dir = station_dir / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_tomorrow_dir(station_dir: Path):
        """Pre-create tomorrow's folder (called near midnight)."""
        tomorrow = date.today() + timedelta(days=1)
        day_dir = station_dir / f"{tomorrow.year:04d}" / f"{tomorrow.month:02d}" / f"{tomorrow.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _remove_empty_date_dir(path: Path):
        """Remove a date-level folder (and empty month/year parents) if it is empty."""
        try:
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
                # Prune empty month parent
                month_dir = path.parent
                if month_dir.exists() and not any(month_dir.iterdir()):
                    month_dir.rmdir()
                    # Prune empty year parent
                    year_dir = month_dir.parent
                    if year_dir.exists() and not any(year_dir.iterdir()):
                        year_dir.rmdir()
        except OSError:
            pass

    @staticmethod
    def cleanup_stale_empty_dirs(station_dir: Path):
        """Remove empty date folders that are not today or tomorrow (tomorrow kept only near midnight)."""
        today = date.today()
        now_dt = datetime.now()
        seconds_to_midnight = (
            (24 * 3600) - now_dt.hour * 3600 - now_dt.minute * 60 - now_dt.second
        )
        near_midnight = seconds_to_midnight <= 300
        tomorrow = today + timedelta(days=1)
        keep = {today}
        if near_midnight:
            keep.add(tomorrow)
        for year_dir in station_dir.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.isdigit():
                        continue
                    try:
                        dir_date = date(
                            int(year_dir.name),
                            int(month_dir.name),
                            int(day_dir.name),
                        )
                    except ValueError:
                        continue
                    if dir_date not in keep:
                        WorkerManager._remove_empty_date_dir(day_dir)

    @staticmethod
    def ffmpeg_command(stream: str, station_dir: Path) -> list[str]:
        out_pattern = str(station_dir / "%Y" / "%m" / "%d" / "%Y-%m-%d-%H-%M-%S.mp3")
        return [
            str(FFMPEG_BIN),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-fflags",
            "+discardcorrupt",
            "-err_detect",
            "ignore_err",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "15",
            "-rw_timeout",
            "15000000",
            "-i",
            stream,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            "96k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-f",
            "segment",
            "-segment_time",
            "3600",
            "-segment_atclocktime",
            "1",
            "-strftime",
            "1",
            out_pattern,
        ]

    @staticmethod
    def log_service(message: str):
        LOGS.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        with SERVICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def start_worker(self, station_name: str, stream: str):
        if station_name in self.workers:
            self.stop_worker(station_name)

        station_dir, log_path, pid_path = self.station_paths(station_name)
        station_dir.mkdir(parents=True, exist_ok=True)
        self.make_today_dir(station_dir)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0
        extra_kwargs = {"start_new_session": True} if not is_windows() else {}

        log_handle = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            self.ffmpeg_command(stream, station_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            **extra_kwargs,
        )

        self.workers[station_name] = (proc, log_handle)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        self.log_service(f"START worker {station_name} pid={proc.pid}")

    def stop_worker(self, station_name: str):
        worker = self.workers.get(station_name)
        _station_dir, _log_path, pid_path = self.station_paths(station_name)

        if worker is not None:
            proc, log_handle = worker
        else:
            proc = None
            log_handle = None

        if proc is not None and proc.poll() is None:
            try:
                if is_windows():
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        check=False,
                        capture_output=True,
                        text=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    proc.terminate()
            except OSError:
                pass

        if station_name in self.workers:
            del self.workers[station_name]

        if log_handle is not None:
            try:
                log_handle.close()
            except OSError:
                pass

        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass

        self.log_service(f"STOP worker {station_name}")

    def sync(self):
        stations = read_stations()
        active_names = {name for name, _ in stations}
        now_ts = time.time()

        for station_name in list(self.workers.keys()):
            if station_name not in active_names:
                self.stop_worker(station_name)
                if station_name in self.restart_state:
                    del self.restart_state[station_name]

        now_dt = datetime.now()
        seconds_to_midnight = (
            (24 * 3600)
            - now_dt.hour * 3600
            - now_dt.minute * 60
            - now_dt.second
        )
        near_midnight = seconds_to_midnight <= 300  # within 5 min of midnight

        for station_name, stream in stations:
            station_dir, _log_path, _pid_path = self.station_paths(station_name)
            station_dir.mkdir(parents=True, exist_ok=True)
            self.make_today_dir(station_dir)
            if near_midnight:
                self.make_tomorrow_dir(station_dir)
            self.cleanup_stale_empty_dirs(station_dir)

            worker = self.workers.get(station_name)
            if worker is None:
                state = self.restart_state.get(station_name)
                if state is not None:
                    fail_count, next_retry = state
                    if now_ts < next_retry:
                        continue
                self.start_worker(station_name, stream)
                continue

            proc, _log_handle = worker
            if proc.poll() is not None:
                exit_code = proc.returncode if proc.returncode is not None else 1
                self.stop_worker(station_name)
                prev_fail_count, _next_retry = self.restart_state.get(station_name, (0, 0.0))
                fail_count = prev_fail_count + 1
                backoff = self.restart_backoff_seconds(fail_count)
                next_retry = now_ts + backoff
                self.restart_state[station_name] = (fail_count, next_retry)
                self.log_service(
                    f"RESTART_DELAY worker {station_name} (exit={exit_code}, fail_count={fail_count}, retry_in={backoff}s)"
                )
            else:
                if station_name in self.restart_state:
                    del self.restart_state[station_name]

    def stop_all(self):
        for station_name in list(self.workers.keys()):
            self.stop_worker(station_name)


def _signal_handler(_signum, _frame):
    global RUNNING
    RUNNING = False


def _ffmpeg_available() -> bool:
    if is_windows():
        return FFMPEG_BIN.exists()
    return True


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    RECORDINGS.mkdir(parents=True, exist_ok=True)

    current_pid = os.getpid()
    try:
        existing_pid = int(MONITOR_PID_FILE.read_text(encoding="utf-8").strip()) if MONITOR_PID_FILE.exists() else None
    except (OSError, ValueError):
        existing_pid = None

    if existing_pid and existing_pid != current_pid and process_exists(existing_pid):
        WorkerManager.log_service(f"Service already running with pid={existing_pid}; exiting pid={current_pid}")
        return 0

    MONITOR_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_PID_FILE.write_text(str(current_pid), encoding="utf-8")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    manager = WorkerManager()
    manager.log_service("Service boot")

    if not _ffmpeg_available():
        manager.log_service(f"FATAL ffmpeg not found at {FFMPEG_BIN}")
        return 1

    try:
        while RUNNING:
            manager.sync()
            time.sleep(SYNC_SECONDS)
    finally:
        manager.stop_all()
        try:
            if MONITOR_PID_FILE.exists() and MONITOR_PID_FILE.read_text(encoding="utf-8").strip() == str(current_pid):
                MONITOR_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        manager.log_service("Service shutdown")

    return 0


if __name__ == "__main__":
    sys.exit(main())
