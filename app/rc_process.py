import os
import signal
import subprocess
import sys
from pathlib import Path

from rc_config import (
    BACKEND_SERVICE,
    ROOT,
    LOGS,
    MONITOR_PID_FILE,
    RADIO_MASTER_BAT,
    RADIO_MASTER_SH,
    is_windows,
)


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


def monitor_command() -> list[str] | None:
    if BACKEND_SERVICE.exists():
        return [sys.executable, str(BACKEND_SERVICE)]
    if is_windows() and RADIO_MASTER_BAT.exists():
        return ["cmd", "/c", str(RADIO_MASTER_BAT)]
    if (not is_windows()) and RADIO_MASTER_SH.exists():
        return ["bash", str(RADIO_MASTER_SH)]
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
    if pid is None:
        return False
    running = process_exists(pid)
    if not running:
        clear_monitor_pid()
    return running


def start_monitor() -> str | None:
    command = monitor_command()
    if command is None:
        return "No backend found. Expected app/rc_backend_service.py or fallback monitor scripts."

    if is_monitor_running():
        return None

    popen_kwargs: dict = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if is_windows():
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(command, **popen_kwargs)
    write_monitor_pid(proc.pid)
    return None


def stop_background():
    pid = read_monitor_pid()
    if pid and process_exists(pid):
        try:
            if is_windows():
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    try:
        if is_windows():
            subprocess.run(
                ["taskkill", "/F", "/IM", "ffmpeg.exe"],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.run(["pkill", "-f", "ffmpeg"], check=False, capture_output=True, text=True)
    except OSError:
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
