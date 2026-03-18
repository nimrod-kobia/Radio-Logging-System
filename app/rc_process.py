import calendar
import csv
import os
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from rc_config import (
    BACKEND_SERVICE,
    ROOT,
    LOGS,
    MONITOR_PID_FILE,
    STOP_FLAG_FILE,
)

HEARTBEAT_FILE = LOGS / "service_heartbeat.json"


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _backend_exe_path() -> Path:
    return ROOT / "rc_backend_service.exe"


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_win_hidden_flags(),
        )
        output = (result.stdout or "").strip()
        if output and not output.startswith("INFO:"):
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_hidden_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_quiet(command: list[str], timeout: int = 8):
    kwargs: dict = {
        "check": False,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": timeout,
        "creationflags": _win_hidden_flags(),
    }
    try:
        subprocess.run(command, **kwargs)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _tasklist_pids_for_image(image_name: str) -> list[int]:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=_win_hidden_flags(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("INFO:"):
            continue
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        if len(row) >= 2 and row[1].strip().isdigit():
            pids.append(int(row[1].strip()))
    return sorted(set(pids))


def _ffmpeg_worker_pids() -> list[int]:
    return _tasklist_pids_for_image("ffmpeg.exe")


def _ffmpeg_parent_pids() -> list[int]:
    return []


def _heartbeat_indicates_running(max_age_seconds: int = 20) -> bool:
    if not HEARTBEAT_FILE.exists():
        return False

    try:
        payload = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if not bool(payload.get("alive", False)):
        return False

    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        return False

    try:
        # Use calendar.timegm to interpret the timestamp as UTC, not local time.
        heartbeat_epoch = calendar.timegm(time.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return False

    age_seconds = time.time() - heartbeat_epoch
    return age_seconds <= max_age_seconds


def _mark_heartbeat_stopped():
    payload = {
        "alive": False,
        "updated_at": _utc_now_iso(),
        "sync_error": "stopped_by_control",
        "active_worker_count": 0,
    }
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _kill_app_ffmpeg_workers_once() -> int:
    for ffmpeg_pid in _ffmpeg_worker_pids()[:16]:
        _run_quiet(["taskkill", "/F", "/T", "/PID", str(ffmpeg_pid)], timeout=3)
    return len(_ffmpeg_worker_pids())


def backend_service_pids() -> list[int]:
    backend_exe_name = _backend_exe_path().name
    # Fast: check for exe by name via tasklist
    pids = _tasklist_pids_for_image(backend_exe_name)
    if pids:
        return pids
    # Fallback: check stored PID
    monitor_pid = read_monitor_pid()
    if monitor_pid and process_exists(monitor_pid):
        return [monitor_pid]
    return []


def monitor_command() -> list[str] | None:
    backend_exe = _backend_exe_path()
    if backend_exe.exists():
        return [str(backend_exe)]

    if BACKEND_SERVICE.exists():
        return [sys.executable, str(BACKEND_SERVICE)]
    return None


def read_monitor_pid() -> int | None:
    if not MONITOR_PID_FILE.exists():
        return None
    try:
        return int(MONITOR_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_monitor_pid(pid: int):
    MONITOR_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_PID_FILE.write_text(str(pid), encoding="utf-8")


def clear_monitor_pid():
    try:
        MONITOR_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_monitor_running() -> bool:
    # If user intentionally stopped, treat as not running regardless of
    # any stale ffmpeg processes that may still be dying
    if STOP_FLAG_FILE.exists():
        return False

    pid = read_monitor_pid()
    if pid is not None:
        if process_exists(pid):
            return True
        clear_monitor_pid()

    service_pids = backend_service_pids()
    if service_pids:
        write_monitor_pid(service_pids[0])
        return True

    ffmpeg_pids = _ffmpeg_worker_pids()
    if ffmpeg_pids:
        return True

    if _heartbeat_indicates_running():
        return True

    return False


def start_monitor() -> str | None:
    command = monitor_command()
    if command is None:
        return "No backend found. Expected app/rc_backend_service.py or rc_backend_service.exe."

    # Clear the intentional-stop flag so the backend is allowed to run
    try:
        STOP_FLAG_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    existing = backend_service_pids()
    if existing:
        write_monitor_pid(existing[0])
        return None

    if is_monitor_running():
        return None

    popen_kwargs: dict = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "creationflags": _win_hidden_flags(),
    }

    proc = subprocess.Popen(command, **popen_kwargs)
    write_monitor_pid(proc.pid)
    return None


def _kill_python_backend():
    """Kill python/py processes running rc_backend_service.py by command-line match."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "CommandLine like '%rc_backend_service%'",
             "get", "ProcessId", "/format:csv"],
            capture_output=True, text=True, timeout=6,
            creationflags=_win_hidden_flags(),
        )
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line or "ProcessId" in line or line.startswith("Node"):
                continue
            parts = line.split(",")
            if parts and parts[-1].strip().isdigit():
                pid = int(parts[-1].strip())
                if pid > 0:
                    _run_quiet(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        pass


def stop_background():
    # ── Write stop flag FIRST ──────────────────────────────────────────────
    # Backend checks this every 0.5 s and exits on its own even if kill misses.
    try:
        STOP_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG_FILE.write_text("stopped", encoding="utf-8")
    except OSError:
        pass

    deadline = time.monotonic() + 20.0
    try:
        # ── Step 1: Kill ALL ffmpeg FIRST (before backend can respawn them) ─
        _run_quiet(["taskkill", "/F", "/IM", "ffmpeg.exe"], timeout=5)

        # ── Step 2: Kill backend by every available method ─────────────────
        # 2a. By stored PID file
        stored_pid = read_monitor_pid()
        if stored_pid and process_exists(stored_pid):
            _run_quiet(["taskkill", "/F", "/T", "/PID", str(stored_pid)], timeout=3)

        # 2b. By tasklist-detected PIDs for the exe name
        for pid in backend_service_pids():
            _run_quiet(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=3)

        # 2c. By exe image name
        backend_exe_name = _backend_exe_path().name
        _run_quiet(["taskkill", "/F", "/T", "/IM", backend_exe_name], timeout=3)

        # 2d. By WMIC command-line search — catches python.exe rc_backend_service.py
        _kill_python_backend()

        # ── Step 3: Second ffmpeg sweep (catch any that started between steps) ─
        time.sleep(0.5)
        _run_quiet(["taskkill", "/F", "/IM", "ffmpeg.exe"], timeout=5)

        # Kill any survivors by PID
        for _ in range(3):
            if time.monotonic() >= deadline:
                break
            pids = _ffmpeg_worker_pids()
            if not pids:
                break
            for ffmpeg_pid in pids:
                if time.monotonic() >= deadline:
                    break
                _run_quiet(["taskkill", "/F", "/T", "/PID", str(ffmpeg_pid)], timeout=3)
            time.sleep(0.3)

    except (subprocess.TimeoutExpired, OSError):
        pass
    finally:
        for pid_file in LOGS.glob("*.pid"):
            try:
                pid_file.unlink(missing_ok=True)
            except OSError:
                pass
        clear_monitor_pid()
        _mark_heartbeat_stopped()


def open_path(path: Path):
    if not path.exists():
        return
    os.startfile(str(path))  # type: ignore[attr-defined]
