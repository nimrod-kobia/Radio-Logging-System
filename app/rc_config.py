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


def safe_station_name(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_")
