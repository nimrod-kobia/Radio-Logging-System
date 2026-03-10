import os
import tempfile
from urllib.parse import urlparse

from rc_config import (
    STATIONS_FILE,
    MAX_STATION_NAME_LEN,
    MAX_STATION_URL_LEN,
    NAME_PATTERN,
    is_local_or_private_host,
)


def read_stations() -> list[tuple[str, str]]:
    stations: list[tuple[str, str]] = []
    if not STATIONS_FILE.exists():
        return stations

    for raw in STATIONS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        name, stream = line.split("|", 1)
        stations.append((name.strip(), stream.strip()))
    return stations


def validate_station(name: str, stream: str) -> str | None:
    if not name:
        return "Station name is required."
    if len(name) > MAX_STATION_NAME_LEN:
        return f"Station name is too long (max {MAX_STATION_NAME_LEN})."
    if "|" in name or "\n" in name or "\r" in name:
        return "Station name contains invalid characters."
    if not NAME_PATTERN.fullmatch(name):
        return "Station name can only contain letters, numbers, space, _, -, ., /."
    # Block path traversal components (e.g. '..') in station names.
    if any(part == ".." for part in name.replace("\\", "/").split("/")):
        return "Station name contains an invalid path component ('..')."

    if not stream:
        return "Stream URL is required."
    if len(stream) > MAX_STATION_URL_LEN:
        return f"Stream URL is too long (max {MAX_STATION_URL_LEN})."
    if "|" in stream or "\n" in stream or "\r" in stream:
        return "Stream URL contains invalid characters."

    parsed = urlparse(stream)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return "Stream URL must be a valid http/https URL."
    if is_local_or_private_host(parsed.hostname or ""):
        return "Stream URL must point to a public internet address, not a private or local host."

    return None


def write_stations_atomic(stations: list[tuple[str, str]]):
    STATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="stations_", suffix=".tmp", dir=str(STATIONS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for name, stream in stations:
                handle.write(f"{name}|{stream}\n")
        os.replace(temp_path, STATIONS_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
