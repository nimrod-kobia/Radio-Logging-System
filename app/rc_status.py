import time
from datetime import date, timedelta
from pathlib import Path

from rc_config import RECORDINGS, WARMUP_SECONDS, WRITE_STALE_SECONDS, safe_station_name
from rc_logs import latest_issue


def format_size(num_bytes: int) -> str:
    units = ["bytes", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    unit_index = 0

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.2f} {units[unit_index]}"


def latest_file(station_name: str) -> Path | None:
    station_dir = RECORDINGS / safe_station_name(station_name)
    if not station_dir.exists():
        return None

    newest = None
    newest_mtime = -1.0
    for file in station_dir.rglob("*.mp3"):
        try:
            mtime = file.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest = file
    return newest


def day_folder(station_name: str, day_offset: int = 0) -> tuple[str, Path]:
    target_day = date.today() + timedelta(days=day_offset)
    folder = (
        RECORDINGS
        / safe_station_name(station_name)
        / f"{target_day.year:04d}"
        / f"{target_day.month:02d}"
        / f"{target_day.day:02d}"
    )
    return target_day.strftime("%Y-%m-%d"), folder


def list_day_files(station_name: str, day_offset: int = 0) -> tuple[str, list[Path]]:
    day_label, folder = day_folder(station_name, day_offset)
    if not folder.exists():
        return day_label, []

    files = [f for f in folder.glob("*.mp3") if f.is_file()]
    files.sort(key=lambda p: p.name, reverse=True)
    return day_label, files


def build_station_status(station_name: str) -> tuple[str, str, str, str]:
    issue_label, issue_reason = latest_issue(station_name)
    issue = f"{issue_label}: {issue_reason}" if issue_label else "-"

    latest = latest_file(station_name)
    if latest is None:
        return "STARTING", "no mp3 files yet", "-", issue

    try:
        stat = latest.stat()
    except OSError:
        return "NO WRITE", "latest file cannot be read", latest.name, issue

    age_seconds = int(time.time() - stat.st_mtime)
    size = stat.st_size
    size_text = format_size(size)
    filename = latest.name

    if size == 0 and age_seconds <= WARMUP_SECONDS:
        return "WARMUP", f"{filename} empty for {age_seconds}s", filename, issue

    if size == 0:
        return "NO AUDIO", f"{filename} empty for {age_seconds}s", filename, issue

    if age_seconds <= WRITE_STALE_SECONDS:
        return "RECORDING", f"{filename} updated {age_seconds}s ago, {size_text}", filename, issue

    return "NO WRITE", f"{filename} last update {age_seconds}s ago, {size_text}", filename, issue
