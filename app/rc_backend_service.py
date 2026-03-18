import os
import shutil
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
    is_local_or_private_host,
    safe_station_dir,
    safe_station_name,
)
from rc_station_store import read_stations
from rc_alerter import RadioAlerter

SERVICE_LOG = LOGS / "service.log"
METRICS_FILE = LOGS / "metrics.json"
HEARTBEAT_FILE = LOGS / "service_heartbeat.json"
RESTART_STATE_FILE = LOGS / "restart_state.json"
STOP_FLAG_FILE = LOGS / "stopped_intentionally.flag"
SYNC_SECONDS = 5
STALE_RESTART_SECONDS = max(300, WRITE_STALE_SECONDS * 3)
LOG_ROTATE_BYTES = 10 * 1024 * 1024
LOG_RETENTION_DAYS = 14

_FFMPEG_HARDCODED = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
_ffmpeg_in_path = shutil.which("ffmpeg")
FFMPEG_BIN = (
    _FFMPEG_HARDCODED
    if _FFMPEG_HARDCODED.exists()
    else (Path(_ffmpeg_in_path) if _ffmpeg_in_path else _FFMPEG_HARDCODED)
)

# Extra HTTP headers required by specific stream sources.
# Key must match the station name exactly as it appears in stations.txt.
# Values are injected via ffmpeg's -headers option (CRLF-terminated lines).
STATION_EXTRA_HEADERS: dict[str, str] = {
    # worldradio.online proxy drops the connection without a proper Referer.
    "Komboni_Radio": "Referer: https://worldradio.online/\r\n",
}

RUNNING = True

# Weekly report: fire on Sunday at or after 23:45, once per day.
_REPORT_WEEKDAY = 6   # Sunday (Monday=0 … Sunday=6)
_REPORT_HOUR    = 23
_REPORT_MINUTE  = 45


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
    # Block SSRF: reject URLs targeting private/loopback/link-local addresses.
    if is_local_or_private_host(parsed.hostname or ""):
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
        self.worker_streams: dict[str, str] = {}
        self.restart_state: dict[str, tuple[int, float]] = {}
        self._load_restart_state()
        self.worker_started_at: dict[str, float] = {}
        self.station_metrics: dict[str, dict[str, object]] = {}
        self.service_started_at = _utc_now_iso()
        self.sync_count = 0
        self.sync_error_count = 0
        self.maintenance_ticks = 0
        self.alerter = RadioAlerter()
        self._last_report_date: date | None = None

    @staticmethod
    def restart_backoff_seconds(fail_count: int) -> int:
        base = 5
        delay = base * (2 ** max(0, fail_count - 1))
        return min(1800, delay)  # cap at 30 min for persistently-down stations

    def _load_restart_state(self):
        """Restore persisted restart-backoff counters so they survive service restarts."""
        try:
            if RESTART_STATE_FILE.exists():
                data = json.loads(RESTART_STATE_FILE.read_text(encoding="utf-8"))
                now_ts = time.time()
                for name, entry in data.items():
                    if isinstance(entry, list) and len(entry) == 2:
                        fail_count, next_retry = int(entry[0]), float(entry[1])
                        # Discard entries older than 24 h (station may have recovered)
                        if next_retry > now_ts - 86400:
                            self.restart_state[name] = (fail_count, next_retry)
        except Exception:
            pass

    def _save_restart_state(self):
        """Persist current restart-backoff counters to disk."""
        try:
            payload = {name: list(state) for name, state in self.restart_state.items()}
            _write_json_atomic(RESTART_STATE_FILE, payload)
        except Exception:
            pass

    @staticmethod
    def station_paths(station_name: str) -> tuple[Path, Path, Path]:
        safe = safe_station_name(station_name)
        station_dir = safe_station_dir(RECORDINGS, station_name)
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
    def ffmpeg_command(stream: str, station_dir: Path, extra_headers: str = "") -> list[str]:
        out_pattern = str(station_dir / "%Y" / "%m" / "%d" / "%Y-%m-%d-%H-%M-%S.mp3")
        cmd = [
            str(FFMPEG_BIN),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        if extra_headers:
            cmd += ["-headers", extra_headers]
        cmd += [
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
            "5xx",
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
        return cmd

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

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        extra_headers = STATION_EXTRA_HEADERS.get(station_name, "")

        log_handle = log_path.open("w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                self.ffmpeg_command(stream, station_dir, extra_headers),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as exc:
            try:
                log_handle.close()
            except OSError:
                pass
            self.log_service(f"START_FAIL worker {station_name}: {exc}")
            raise

        self.workers[station_name] = (proc, log_handle)
        self.worker_streams[station_name] = stream
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
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                pass

        if station_name in self.workers:
            del self.workers[station_name]

        if station_name in self.worker_streams:
            del self.worker_streams[station_name]

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

            current_stream = self.worker_streams.get(station_name)
            if current_stream is not None and current_stream != stream:
                self.log_service(f"STREAM_CHANGE worker {station_name}: restarting with updated URL")
                self.stop_worker(station_name)
                if station_name in self.restart_state:
                    del self.restart_state[station_name]
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

        self._save_restart_state()

        # ── Email alerting ────────────────────────────────────────────────────
        try:
            station_statuses = self._collect_station_statuses(stations)
            self.alerter.evaluate(station_statuses, heartbeat_ok=True)
        except Exception as _alert_exc:
            self.log_service(
                f"ALERTER_EVAL_ERROR {_alert_exc.__class__.__name__}: {_alert_exc}"
            )

    def _collect_station_statuses(self, stations: list) -> dict:
        """Return a dict of station_name -> status string for the alerter."""
        from rc_status import build_station_status
        result = {}
        for station_name, _ in stations:
            try:
                status, *_ = build_station_status(station_name)
                result[station_name] = status
            except Exception:
                result[station_name] = "ERROR"
        return result

    def maybe_generate_report(self):
        """Generate the weekly HTML uptime report on Sunday at 23:45, once per day."""
        now = datetime.now()
        if now.weekday() != _REPORT_WEEKDAY:
            return
        if now.hour < _REPORT_HOUR or (now.hour == _REPORT_HOUR and now.minute < _REPORT_MINUTE):
            return
        today = date.today()
        if self._last_report_date == today:
            return
        try:
            from rc_report import generate_and_save_weekly_report
            path = generate_and_save_weekly_report(today)
            self._last_report_date = today
            self.log_service(f"REPORT_GENERATED {path}")
        except Exception as exc:
            self.log_service(f"REPORT_ERROR {exc.__class__.__name__}: {exc}")

    def stop_all(self):
        for station_name in list(self.workers.keys()):
            self.stop_worker(station_name)


def _signal_handler(_signum, _frame):
    global RUNNING
    RUNNING = False


def _ffmpeg_available() -> bool:
    if FFMPEG_BIN.exists():
        return True
    # Re-check PATH in case ffmpeg was installed after this process started
    found = shutil.which("ffmpeg")
    return found is not None


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

    # Refuse to start if the user intentionally stopped recording
    if STOP_FLAG_FILE.exists():
        WorkerManager.log_service("Stop flag present; refusing auto-start. Press 'Start Recording' to resume.")
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

    # Record the moment this instance started so we can ignore stop flags that
    # pre-date this start (e.g. a stale flag synced back by OneDrive/network share
    # a few seconds after start_monitor() deleted it).
    service_start_ts = time.time()

    # Delete any stop flag that already exists at boot time — start_monitor() should
    # have removed it, but a cloud sync may have restored it in the gap.
    try:
        if STOP_FLAG_FILE.exists():
            STOP_FLAG_FILE.unlink(missing_ok=True)
            manager.log_service("Removed stale stop flag at boot (possible sync artifact)")
    except OSError:
        pass

    try:
        while RUNNING:
            # Check stop flag each cycle - exits cleanly if user pressed Stop.
            # Only honour the flag if it was written AFTER this service instance
            # started; a flag with an older mtime is a stale sync artifact.
            if STOP_FLAG_FILE.exists():
                try:
                    flag_mtime = STOP_FLAG_FILE.stat().st_mtime
                except OSError:
                    flag_mtime = service_start_ts + 1  # unreadable → treat as fresh
                if flag_mtime >= service_start_ts - 2.0:
                    manager.log_service("Stop flag detected during run; shutting down.")
                    break
                # Stale flag (older than this service instance) — delete and ignore.
                try:
                    STOP_FLAG_FILE.unlink(missing_ok=True)
                    manager.log_service("Ignored and removed stale stop flag (sync artifact)")
                except OSError:
                    pass
            sync_error: str | None = None
            try:
                manager.sync()
            except Exception as exc:
                sync_error = f"{exc.__class__.__name__}: {exc}"
                manager.log_service(f"SYNC_ERROR {exc.__class__.__name__}: {exc}")
            manager.write_observability(sync_error)
            manager.maybe_generate_report()
            # Sleep in small intervals so stop flag is detected within 0.5s
            # instead of waiting a full SYNC_SECONDS cycle
            for _ in range(SYNC_SECONDS * 2):
                if not RUNNING:
                    break
                if STOP_FLAG_FILE.exists():
                    try:
                        flag_mtime = STOP_FLAG_FILE.stat().st_mtime
                    except OSError:
                        flag_mtime = service_start_ts + 1
                    if flag_mtime >= service_start_ts - 2.0:
                        break
                time.sleep(0.5)
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
