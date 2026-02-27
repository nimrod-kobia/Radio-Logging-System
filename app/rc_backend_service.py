import os
import signal
import subprocess
import sys
import time
import json
from datetime import date, timedelta, datetime
from pathlib import Path
from urllib.parse import urlparse

from rc_config import (
    LOGS,
    MONITOR_PID_FILE,
    RECORDINGS,
    WARMUP_SECONDS,
    WRITE_STALE_SECONDS,
    is_windows,
    safe_station_name,
)
from rc_station_store import read_stations

SERVICE_LOG = LOGS / "service.log"
METRICS_FILE = LOGS / "metrics.json"
HEARTBEAT_FILE = LOGS / "service_heartbeat.json"
SYNC_SECONDS = 5
STALE_RESTART_SECONDS = max(300, WRITE_STALE_SECONDS * 3)
LOG_ROTATE_BYTES = 10 * 1024 * 1024
LOG_RETENTION_DAYS = 14

if is_windows():
    FFMPEG_BIN = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
else:
    FFMPEG_BIN = Path("ffmpeg")

RUNNING = True


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json_atomic(path: Path, payload: dict):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _rotate_log(path: Path, max_bytes: int = LOG_ROTATE_BYTES, keep: int = 3):
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    try:
        oldest = path.with_name(f"{path.name}.{keep}")
        oldest.unlink(missing_ok=True)
    except OSError:
        pass

    for index in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        try:
            if src.exists():
                src.replace(dst)
        except OSError:
            pass

    try:
        if path.exists():
            path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        pass


def _prune_old_logs(root: Path, retention_days: int = LOG_RETENTION_DAYS):
    cutoff = time.time() - retention_days * 86400
    for log_file in root.glob("*.log.*"):
        try:
            if log_file.stat().st_mtime < cutoff:
                log_file.unlink(missing_ok=True)
        except OSError:
            pass


def _is_valid_stream_url(stream: str) -> bool:
    if not stream or len(stream) > 1000:
        return False
    if any(ch in stream for ch in "\r\n\x00"):
        return False
    parsed = urlparse(stream)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


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
        self.worker_started_at: dict[str, float] = {}
        self.station_metrics: dict[str, dict[str, object]] = {}
        self.service_started_at = _utc_now_iso()
        self.sync_count = 0
        self.sync_error_count = 0
        self.maintenance_ticks = 0

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
        _rotate_log(SERVICE_LOG)
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        try:
            with SERVICE_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    def station_metric(self, station_name: str) -> dict[str, object]:
        if station_name not in self.station_metrics:
            self.station_metrics[station_name] = {
                "starts": 0,
                "stops": 0,
                "restarts": 0,
                "last_start_utc": None,
                "last_stop_utc": None,
                "last_restart_reason": None,
                "last_exit_code": None,
                "last_write_age_seconds": None,
            }
        return self.station_metrics[station_name]

    def note_restart(self, station_name: str, reason: str, exit_code: int | None = None):
        metric = self.station_metric(station_name)
        metric["restarts"] = int(metric.get("restarts", 0)) + 1
        metric["last_restart_reason"] = reason
        if exit_code is not None:
            metric["last_exit_code"] = int(exit_code)

    def write_observability(self, sync_error: str | None):
        now_utc = _utc_now_iso()
        self.sync_count += 1
        if sync_error:
            self.sync_error_count += 1

        active_workers = sorted(self.workers.keys())
        metrics_payload = {
            "service_started_at": self.service_started_at,
            "last_sync_at": now_utc,
            "sync_count": self.sync_count,
            "sync_error_count": self.sync_error_count,
            "active_worker_count": len(active_workers),
            "active_workers": active_workers,
            "stations": self.station_metrics,
        }

        heartbeat_payload = {
            "alive": True,
            "updated_at": now_utc,
            "sync_error": sync_error,
            "active_worker_count": len(active_workers),
        }

        try:
            _write_json_atomic(METRICS_FILE, metrics_payload)
            _write_json_atomic(HEARTBEAT_FILE, heartbeat_payload)
        except OSError:
            pass

        self.maintenance_ticks += 1
        if self.maintenance_ticks >= 60:
            self.maintenance_ticks = 0
            _prune_old_logs(LOGS)

    @staticmethod
    def latest_write_age_seconds(station_dir: Path, now_ts: float) -> float | None:
        today = date.today()
        days_to_check = [today, today - timedelta(days=1), today + timedelta(days=1)]
        newest_mtime: float | None = None

        for target_day in days_to_check:
            day_dir = station_dir / f"{target_day.year:04d}" / f"{target_day.month:02d}" / f"{target_day.day:02d}"
            if not day_dir.exists():
                continue

            for mp3_file in day_dir.glob("*.mp3"):
                try:
                    mtime = mp3_file.stat().st_mtime
                except OSError:
                    continue
                if newest_mtime is None or mtime > newest_mtime:
                    newest_mtime = mtime

        if newest_mtime is None:
            return None
        return max(0.0, now_ts - newest_mtime)

    def start_worker(self, station_name: str, stream: str):
        if station_name in self.workers:
            self.stop_worker(station_name)

        if not _is_valid_stream_url(stream):
            self.log_service(f"START_FAIL worker {station_name}: invalid stream URL")
            raise ValueError("Invalid stream URL")

        station_dir, log_path, pid_path = self.station_paths(station_name)
        station_dir.mkdir(parents=True, exist_ok=True)
        self.make_today_dir(station_dir)
        _rotate_log(log_path)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0
        extra_kwargs = {"start_new_session": True} if not is_windows() else {}

        log_handle = log_path.open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                self.ffmpeg_command(stream, station_dir),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                **extra_kwargs,
            )
        except Exception as exc:
            try:
                log_handle.close()
            except OSError:
                pass
            self.log_service(f"START_FAIL worker {station_name}: {exc}")
            raise

        self.workers[station_name] = (proc, log_handle)
        self.worker_started_at[station_name] = time.time()
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        metric = self.station_metric(station_name)
        metric["starts"] = int(metric.get("starts", 0)) + 1
        metric["last_start_utc"] = _utc_now_iso()
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

        if station_name in self.worker_started_at:
            del self.worker_started_at[station_name]

        if log_handle is not None:
            try:
                log_handle.close()
            except OSError:
                pass

        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass

        metric = self.station_metric(station_name)
        metric["stops"] = int(metric.get("stops", 0)) + 1
        metric["last_stop_utc"] = _utc_now_iso()

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
                try:
                    self.start_worker(station_name, stream)
                except Exception:
                    prev_fail_count, _next_retry = self.restart_state.get(station_name, (0, 0.0))
                    fail_count = prev_fail_count + 1
                    backoff = self.restart_backoff_seconds(fail_count)
                    next_retry = now_ts + backoff
                    self.restart_state[station_name] = (fail_count, next_retry)
                    self.note_restart(station_name, "start_failed")
                    self.log_service(
                        f"RESTART_DELAY worker {station_name} (start_failed, fail_count={fail_count}, retry_in={backoff}s)"
                    )
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
                self.note_restart(station_name, "exit", exit_code)
                self.log_service(
                    f"RESTART_DELAY worker {station_name} (exit={exit_code}, fail_count={fail_count}, retry_in={backoff}s)"
                )
            else:
                age_seconds = self.latest_write_age_seconds(station_dir, now_ts)
                metric = self.station_metric(station_name)
                metric["last_write_age_seconds"] = int(age_seconds) if age_seconds is not None else None
                started_at = self.worker_started_at.get(station_name, now_ts)
                worker_age = max(0.0, now_ts - started_at)
                warmup_grace = max(120, WARMUP_SECONDS * 2)

                if (
                    worker_age > warmup_grace
                    and age_seconds is not None
                    and age_seconds > STALE_RESTART_SECONDS
                ):
                    self.stop_worker(station_name)
                    prev_fail_count, _next_retry = self.restart_state.get(station_name, (0, 0.0))
                    fail_count = prev_fail_count + 1
                    backoff = self.restart_backoff_seconds(fail_count)
                    next_retry = now_ts + backoff
                    self.restart_state[station_name] = (fail_count, next_retry)
                    self.note_restart(station_name, "stalled_no_write")
                    self.log_service(
                        f"STALL_RESTART worker {station_name} (no_write_for={int(age_seconds)}s, fail_count={fail_count}, retry_in={backoff}s)"
                    )
                    continue

                if worker_age > warmup_grace and age_seconds is None:
                    self.stop_worker(station_name)
                    prev_fail_count, _next_retry = self.restart_state.get(station_name, (0, 0.0))
                    fail_count = prev_fail_count + 1
                    backoff = self.restart_backoff_seconds(fail_count)
                    next_retry = now_ts + backoff
                    self.restart_state[station_name] = (fail_count, next_retry)
                    self.note_restart(station_name, "stalled_no_output")
                    self.log_service(
                        f"STALL_RESTART worker {station_name} (no_output_after_start, fail_count={fail_count}, retry_in={backoff}s)"
                    )
                    continue

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
            sync_error: str | None = None
            try:
                manager.sync()
            except Exception as exc:
                sync_error = f"{exc.__class__.__name__}: {exc}"
                manager.log_service(f"SYNC_ERROR {exc.__class__.__name__}: {exc}")
            manager.write_observability(sync_error)
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
