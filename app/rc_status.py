import re
import time
from datetime import date, datetime, timedelta
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


_FNAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.mp3$")


def _parse_fname_dt(name: str) -> datetime | None:
    m = _FNAME_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d-%H-%M-%S")
    except ValueError:
        return None


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m {secs:02d}s"


def list_day_files(station_name: str, day_offset: int = 0) -> tuple[str, list[Path]]:
    day_label, folder = day_folder(station_name, day_offset)
    if not folder.exists():
        return day_label, []

    files = [f for f in folder.glob("*.mp3") if f.is_file()]
    files.sort(key=lambda p: p.name)
    return day_label, files


def day_file_display_entries(station_name: str, day_offset: int = 0) -> tuple[str, list[str]]:
    """Return (day_label, list of human-readable display strings) for the day view listbox."""
    day_label, files = list_day_files(station_name, day_offset)
    if not files:
        return day_label, []

    now = datetime.now()
    entries: list[str] = []

    for i, fp in enumerate(files):
        start_dt = _parse_fname_dt(fp.name)

        # Determine end: next file's start time, or now for last file
        if i + 1 < len(files):
            end_dt = _parse_fname_dt(files[i + 1].name)
        else:
            end_dt = None

        if start_dt is not None and end_dt is not None:
            duration_str = _format_duration((end_dt - start_dt).total_seconds())
        elif start_dt is not None:
            # Last file in this day folder: use mtime as end estimate.
            # Mark as ongoing only if writes are still fresh.
            try:
                stat = fp.stat()
                mtime_ts = stat.st_mtime
                end_ts = max(start_dt.timestamp(), mtime_ts)
                elapsed = end_ts - start_dt.timestamp()

                age_seconds = time.time() - mtime_ts
                is_ongoing = age_seconds <= WRITE_STALE_SECONDS

                duration_str = _format_duration(elapsed)
                if is_ongoing:
                    duration_str += " (ongoing)"
            except OSError:
                duration_str = "?"
        else:
            duration_str = "?"

        try:
            size_str = format_size(fp.stat().st_size)
        except OSError:
            size_str = "?"

        if start_dt is not None:
            time_str = start_dt.strftime("%H:%M:%S")
        else:
            time_str = fp.name

        entries.append(f"{time_str}  |  {duration_str}  |  {size_str}  |  {fp.name}")

    return day_label, entries


def build_station_status(station_name: str) -> tuple[str, str, str, str]:
    issue_label, issue_reason = latest_issue(station_name)
    issue = f"{issue_label}: {issue_reason}" if issue_label else "-"

    latest = latest_file(station_name)
    if latest is None:
        return "STARTING", "no mp3 files yet", "-", "-"

    try:
        stat = latest.stat()
    except OSError:
        return "NO WRITE", "latest file cannot be read", latest.name, issue

    age_seconds = int(time.time() - stat.st_mtime)
    size = stat.st_size
    filename = latest.name

    if size == 0 and age_seconds <= WARMUP_SECONDS:
        return "WARMUP", f"{filename} empty for {age_seconds}s", filename, "-"

    if size == 0:
        return "NO AUDIO", f"{filename} empty for {age_seconds}s", filename, issue

    if age_seconds <= WRITE_STALE_SECONDS:
        return "RECORDING", f"{filename} updated {age_seconds}s ago", filename, "-"

    return "NO WRITE", f"{filename} last update {age_seconds}s ago", filename, issue
