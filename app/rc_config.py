import ipaddress
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
BACKEND_SERVICE = APP_DIR / "rc_backend_service.py"
STATIONS_FILE = ROOT / "stations.txt"
RECORDINGS = ROOT / "RadioRecordings"
LOGS = ROOT / "Runtime"
MONITOR_PID_FILE = APP_DIR / "monitor.pid"
STOP_FLAG_FILE = LOGS / "stopped_intentionally.flag"

WRITE_STALE_SECONDS = 180
WARMUP_SECONDS = 120
REFRESH_INTERVAL_MS = 5000
MAX_STATION_NAME_LEN = 80
MAX_STATION_URL_LEN = 1000
NAME_PATTERN = re.compile(r"^[A-Za-z0-9 _./\-]+$")

# Hostnames that unambiguously resolve to loopback / link-local.
_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|.+\.local|.+\.localhost|.+\.internal|.+\.corp)$",
    re.IGNORECASE,
)


def is_local_or_private_host(host: str) -> bool:
    """Return True if *host* names a private, loopback, link-local, or
    cloud-metadata address — i.e. a target that must never be fetched."""
    # Strip IPv6 brackets and port suffix so bare IP is left.
    host = host.strip("[]").split(":")[0].strip()
    if not host:
        return True
    if _PRIVATE_HOST_RE.match(host):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        )
    except ValueError:
        # A real hostname — DNS resolution happens inside ffmpeg, not here.
        return False


def safe_station_name(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_")


def safe_station_dir(base: Path, station_name: str) -> Path:
    """Return the recordings directory for *station_name* and assert it
    is contained within *base* (defense-in-depth against path traversal)."""
    candidate = (base / safe_station_name(station_name)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"Station name would escape base directory: {station_name!r}")
    return candidate
