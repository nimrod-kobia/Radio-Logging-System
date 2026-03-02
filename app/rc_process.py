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


def _ffmpeg_worker_pids() -> list[int]:
    root_pattern = str(ROOT).replace("'", "''")
    command = (
        f"$root='{root_pattern}'; "
        "$ff=Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" -ErrorAction SilentlyContinue; "
        "$hits=$ff | Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($root) }; "
        "$hits | ForEach-Object { $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            creationflags=_win_hidden_flags(),
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            pids.append(int(value))
    return pids


def _ffmpeg_parent_pids() -> list[int]:
    root_pattern = str(ROOT).replace("'", "''")
    command = (
        f"$root='{root_pattern}'; "
        "$ff=Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" -ErrorAction SilentlyContinue; "
        "$hits=$ff | Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($root) }; "
        "$hits | ForEach-Object { $_.ParentProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            creationflags=_win_hidden_flags(),
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            pids.append(int(value))
    return sorted(set([pid for pid in pids if pid > 0]))


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
        heartbeat_dt = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False

    age_seconds = time.time() - heartbeat_dt.timestamp()
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
    root_pattern = str(ROOT).replace("'", "''")
    command = (
        f"$root='{root_pattern}'; "
        "$ff=Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" -ErrorAction SilentlyContinue; "
        "$hits=$ff | Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($root) }; "
        "$ids=@($hits | ForEach-Object { $_.ProcessId }); "
        "foreach($id in $ids){ try { Stop-Process -Id $id -Force -ErrorAction Stop } catch {} }; "
        "$remaining=Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" -ErrorAction SilentlyContinue | "
        "  Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($root) }; "
        "$remaining.Count"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            creationflags=_win_hidden_flags(),
            timeout=5,
        )
        for line in reversed(result.stdout.splitlines()):
            line = line.strip()
            if line.isdigit():
                return int(line)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return len(_ffmpeg_worker_pids())


def backend_service_pids() -> list[int]:
    backend_exe_name = _backend_exe_path().name
    command = (
        "$all=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue; "
        "$pythonHits=$all | Where-Object { $_.Name -match '^(python|pythonw|py)\\.exe$' -and $_.CommandLine -and $_.CommandLine -match 'rc_backend_service\\.py' }; "
        f"$exeHits=$all | Where-Object {{ $_.Name -ieq '{backend_exe_name}' }}; "
        "$hits=@($pythonHits + $exeHits) | Where-Object { $_ -ne $null }; "
        "$hits | ForEach-Object { $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            creationflags=_win_hidden_flags(),
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    pids = sorted(set(pids))
    if pids:
        return pids

    parent_pids = [pid for pid in _ffmpeg_parent_pids() if process_exists(pid)]
    return sorted(set(parent_pids))


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


def stop_background():
    service_pids = set(backend_service_pids())
    service_pids.update(_ffmpeg_parent_pids())

    for service_pid in service_pids:
        try:
            _run_quiet(["taskkill", "/F", "/T", "/PID", str(service_pid)])
        except OSError:
            pass

    pid = read_monitor_pid()
    if pid and process_exists(pid):
        try:
            _run_quiet(["taskkill", "/F", "/T", "/PID", str(pid)])
        except OSError:
            pass

    try:
        for _ in range(6):
            worker_pids = _ffmpeg_worker_pids()
            if not worker_pids:
                break
            for ffmpeg_pid in worker_pids:
                _run_quiet(["taskkill", "/F", "/T", "/PID", str(ffmpeg_pid)], timeout=8)
            remaining = _kill_app_ffmpeg_workers_once()
            if remaining <= 0:
                break
            time.sleep(0.6)

        if _ffmpeg_worker_pids():
            _run_quiet(["taskkill", "/F", "/T", "/IM", "ffmpeg.exe"], timeout=10)
            time.sleep(0.8)
    except (subprocess.TimeoutExpired, OSError):
        pass

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
