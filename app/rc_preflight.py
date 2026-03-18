import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rc_config import BACKEND_SERVICE, LOGS, RECORDINGS, ROOT, STATIONS_FILE


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    critical: bool


@dataclass
class PreflightReport:
    checks: list[PreflightCheck]

    @property
    def critical_failures(self) -> list[PreflightCheck]:
        return [check for check in self.checks if check.critical and not check.ok]

    @property
    def noncritical_failures(self) -> list[PreflightCheck]:
        return [check for check in self.checks if not check.critical and not check.ok]

    @property
    def is_ready(self) -> bool:
        return len(self.critical_failures) == 0


def _check_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create directory: {exc}"

    try:
        with tempfile.NamedTemporaryFile(prefix=".write_test_", dir=path, delete=False) as handle:
            temp_name = Path(handle.name)
            handle.write(b"ok")
        temp_name.unlink(missing_ok=True)
        return True, "Writable"
    except OSError as exc:
        return False, f"Not writable: {exc}"


def _check_stations_file_access() -> tuple[bool, str]:
    if STATIONS_FILE.exists():
        read_ok = os.access(STATIONS_FILE, os.R_OK)
        write_ok = os.access(STATIONS_FILE, os.W_OK)
        if read_ok and write_ok:
            return True, "Readable/Writable"
        return False, "Missing read/write permission"

    parent = STATIONS_FILE.parent
    writable, detail = _check_writable_dir(parent)
    if writable:
        return True, "Parent folder writable (file can be created)"
    return False, detail


def _check_ffmpeg() -> tuple[bool, str]:
    ffmpeg_path = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
    if ffmpeg_path.exists():
        return True, str(ffmpeg_path)
    found = shutil.which("ffmpeg")
    if found:
        return True, found
    return False, f"Not found at {ffmpeg_path} and not in PATH"


def run_preflight_checks() -> PreflightReport:
    checks: list[PreflightCheck] = []

    python_exe = Path(sys.executable) if sys.executable else None
    python_ok = bool(python_exe and python_exe.exists())
    checks.append(
        PreflightCheck(
            name="Python executable",
            ok=python_ok,
            detail=str(python_exe) if python_ok else "Python executable not resolved",
            critical=False,
        )
    )

    try:
        import tkinter  # noqa: F401

        checks.append(PreflightCheck(name="Tkinter", ok=True, detail="Available", critical=False))
    except Exception as exc:
        checks.append(PreflightCheck(name="Tkinter", ok=False, detail=str(exc), critical=True))

    backend_exe = ROOT / "rc_backend_service.exe"
    backend_ok = backend_exe.exists() or BACKEND_SERVICE.exists()
    if backend_exe.exists():
        backend_detail = str(backend_exe)
    elif BACKEND_SERVICE.exists():
        backend_detail = str(BACKEND_SERVICE)
    else:
        backend_detail = f"Missing: {backend_exe} and {BACKEND_SERVICE}"

    checks.append(
        PreflightCheck(
            name="Backend service",
            ok=backend_ok,
            detail=backend_detail,
            critical=True,
        )
    )

    ffmpeg_ok, ffmpeg_detail = _check_ffmpeg()
    checks.append(PreflightCheck(name="FFmpeg", ok=ffmpeg_ok, detail=ffmpeg_detail, critical=True))

    logs_ok, logs_detail = _check_writable_dir(LOGS)
    checks.append(PreflightCheck(name="Runtime folder", ok=logs_ok, detail=logs_detail, critical=True))

    recordings_ok, recordings_detail = _check_writable_dir(RECORDINGS)
    checks.append(PreflightCheck(name="Recordings folder", ok=recordings_ok, detail=recordings_detail, critical=True))

    stations_ok, stations_detail = _check_stations_file_access()
    checks.append(PreflightCheck(name="Stations file", ok=stations_ok, detail=stations_detail, critical=True))

    return PreflightReport(checks=checks)
