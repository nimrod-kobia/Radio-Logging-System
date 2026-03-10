import re
from pathlib import Path

from rc_config import LOGS, safe_station_name


ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"404|not found", re.IGNORECASE), "URL not found (404)"),
    (re.compile(r"401|unauthorized", re.IGNORECASE), "Authentication required (401)"),
    (re.compile(r"403|forbidden|denied", re.IGNORECASE), "Access forbidden (403)"),
    (re.compile(r"503|service unavailable", re.IGNORECASE), "Service unavailable (503)"),
    (re.compile(r"5\d\d|bad gateway", re.IGNORECASE), "Server error (5xx)"),
    (re.compile(r"timed out|refused|connection|io error|network", re.IGNORECASE), "Network/connectivity issue"),
    (re.compile(r"invalid data|corrupt|decode", re.IGNORECASE), "Corrupt/invalid stream data"),
    (re.compile(r"Failed to open segment|No such file or directory", re.IGNORECASE), "Output path/directory issue"),
]


def station_log_path(station_name: str) -> Path:
    return LOGS / f"{safe_station_name(station_name)}.log"


def tail_lines(path: Path, max_lines: int = 120) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def latest_issue(station_name: str) -> tuple[str, str]:
    log_path = station_log_path(station_name)
    lines = tail_lines(log_path, max_lines=140)
    if not lines:
        return "", ""

    last_match = ""
    for line in reversed(lines):
        cleaned = " ".join(line.split())
        lowered = cleaned.lower()
        if any(word in lowered for word in ["error", "failed", "forbidden", "unauthorized", "unavailable", "404", "401", "403", "502", "503", "timed", "refused", "invalid", "denied"]):
            last_match = cleaned
            break

    if not last_match:
        return "", ""

    for pattern, label in ERROR_PATTERNS:
        if pattern.search(last_match):
            return label, last_match[:180]

    return "Input/stream error", last_match[:180]
