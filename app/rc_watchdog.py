import sys
from datetime import datetime

from rc_config import LOGS
from rc_process import is_monitor_running, start_monitor

WATCHDOG_LOG = LOGS / "watchdog.log"


def _log(message: str):
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        with WATCHDOG_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def main() -> int:
    if is_monitor_running():
        _log("Monitor already running")
        return 0

    error = start_monitor()
    if error:
        _log(f"Start failed: {error}")
        return 1

    _log("Monitor started by watchdog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
