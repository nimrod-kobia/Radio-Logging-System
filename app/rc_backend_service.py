import os
import signal
import subprocess
import sys
import time
from datetime import date, timedelta, datetime
from pathlib import Path

from rc_config import LOGS, RECORDINGS, is_windows, safe_station_name
from rc_station_store import read_stations

SERVICE_LOG = LOGS / "service.log"
SYNC_SECONDS = 5

if is_windows():
    FFMPEG_BIN = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
else:
    FFMPEG_BIN = Path("ffmpeg")

RUNNING = True


class WorkerManager:
    def __init__(self):
        self.workers: dict[str, tuple[subprocess.Popen, object]] = {}

    @staticmethod
    def station_paths(station_name: str) -> tuple[Path, Path, Path]:
        safe = safe_station_name(station_name)
        station_dir = RECORDINGS / safe
        log_path = LOGS / f"{safe}.log"
        pid_path = LOGS / f"{safe}.pid"
        return station_dir, log_path, pid_path

    @staticmethod
    def make_day_dirs(station_dir: Path):
        for offset in (-1, 0, 1, 2):
            target = date.today() + timedelta(days=offset)
            day_dir = station_dir / f"{target.year:04d}" / f"{target.month:02d}" / f"{target.day:02d}"
            day_dir.mkdir(parents=True, exist_ok=True)

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
        station_dir, log_path, pid_path = self.station_paths(station_name)
        station_dir.mkdir(parents=True, exist_ok=True)
        self.make_day_dirs(station_dir)

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

        for station_name in list(self.workers.keys()):
            if station_name not in active_names:
                self.stop_worker(station_name)

        for station_name, stream in stations:
            station_dir, _log_path, _pid_path = self.station_paths(station_name)
            station_dir.mkdir(parents=True, exist_ok=True)
            self.make_day_dirs(station_dir)

            worker = self.workers.get(station_name)
            if worker is None:
                self.start_worker(station_name, stream)
                continue

            proc, _log_handle = worker
            if proc.poll() is not None:
                self.log_service(f"RESTART worker {station_name} (exit={proc.returncode})")
                self.start_worker(station_name, stream)

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
        manager.log_service("Service shutdown")

    return 0


if __name__ == "__main__":
    sys.exit(main())
