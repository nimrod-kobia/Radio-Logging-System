import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rc_config import (
    BACKEND_SERVICE,
    ROOT,
    LOGS,
    MONITOR_PID_FILE,
    is_windows,
)


def _backend_exe_path() -> Path:
    if is_windows():
        return ROOT / "rc_backend_service.exe"
    return ROOT / "rc_backend_service"


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


def _win_hidden_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_quiet(command: list[str], timeout: int = 8):
    kwargs: dict = {
        "check": False,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": timeout,
    }
    if is_windows():
        kwargs["creationflags"] = _win_hidden_flags()
    try:
        subprocess.run(command, **kwargs)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _run_quiet_nowait(command: list[str]):
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if is_windows():
        kwargs["creationflags"] = _win_hidden_flags()
    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        pass


def _windows_ffmpeg_worker_pids() -> list[int]:
    if not is_windows():
        return []

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


def backend_service_pids() -> list[int]:
    if is_windows():
        backend_exe_name = _backend_exe_path().name
        command = (
            "$python=Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" -ErrorAction SilentlyContinue; "
            "$pythonHits=$python | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -and $_.CommandLine -match 'rc_backend_service\\.py' }; "
            f"$exeHits=Get-CimInstance Win32_Process -Filter \"Name='{backend_exe_name}'\" -ErrorAction SilentlyContinue; "
            "$all=@($pythonHits + $exeHits) | Where-Object { $_ -ne $null }; "
            "$all | ForEach-Object { $_.ProcessId }"
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
        return pids

    result = subprocess.run(["pgrep", "-f", "rc_backend_service.py"], capture_output=True, text=True)
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


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
    return False


def start_monitor() -> str | None:
    command = monitor_command()
    if command is None:
        return "No backend found. Expected app/rc_backend_service.py or fallback monitor scripts."

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
    }
    if is_windows():
        popen_kwargs["creationflags"] = _win_hidden_flags()
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)
    write_monitor_pid(proc.pid)
    return None


def stop_background():
    for service_pid in set(backend_service_pids()):
        try:
            if is_windows():
                _run_quiet(["taskkill", "/F", "/T", "/PID", str(service_pid)])
            else:
                os.kill(service_pid, signal.SIGTERM)
        except OSError:
            pass

    pid = read_monitor_pid()
    if pid and process_exists(pid):
        try:
            if is_windows():
                _run_quiet(["taskkill", "/F", "/T", "/PID", str(pid)])
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    try:
        if is_windows():
            for _ in range(3):
                worker_pids = _windows_ffmpeg_worker_pids()
                if not worker_pids:
                    break
                for ffmpeg_pid in worker_pids:
                    _run_quiet(["taskkill", "/F", "/T", "/PID", str(ffmpeg_pid)], timeout=8)
                time.sleep(0.4)
        else:
            _run_quiet(["pkill", "-f", "ffmpeg"])
    except (subprocess.TimeoutExpired, OSError):
        pass

    for pid_file in LOGS.glob("*.pid"):
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    clear_monitor_pid()


def open_path(path: Path):
    if not path.exists():
        return
    if is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
