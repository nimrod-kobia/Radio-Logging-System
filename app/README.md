# Radio Control App

Desktop GUI for your radio recording system.

## What it does

- Shows station status in a table (RECORDING / WARMUP / NO WRITE / NO AUDIO / STARTING).
- Runs Python backend supervisor in the background (`app/rc_backend_service.py`).
- Backend is hardened for 24/7 uptime with auto-restart/backoff on worker exits/start failures.
- Backend auto-recovers stalled workers (alive process but no new audio writes) and keeps main loop alive after sync exceptions.
- Stops monitor/worker/ffmpeg background processes from the GUI.
- Refreshes status automatically every 5 seconds.
- Adds stations from GUI with validation.
- Removes selected stations directly from GUI.
- Shows per-station issue analysis from latest log lines.
- Opens selected station log file directly from GUI.
- On initial app open (before first start), stations show `IDLE` and error signals are suppressed.
- Runs a startup self-check (ffmpeg/backend/path permissions) and blocks monitor start if critical checks fail.
- Writes runtime observability snapshots to `Runtime/metrics.json` and `Runtime/service_heartbeat.json`.
- Rotates oversized logs and prunes old rotated logs automatically.
- Supports watchdog-driven auto-recovery using `app/rc_watchdog.py`.

## Runtime layout

- `Runtime/` contains both station logs and generated worker bat files.
- `Runtime/metrics.json` contains service/station counters (starts/stops/restarts, sync health, last write age).
- `Runtime/service_heartbeat.json` is updated every sync loop for liveness checks.
- Legacy `Logs/` and `Workers/` folders are no longer used.

## App file grouping

- `radio_control_app.py` → UI/controller entrypoint
- `rc_backend_service.py` → backend supervisor (spawns/supervises ffmpeg workers)
- `rc_config.py` → paths/constants/platform helpers
- `rc_station_store.py` → station file read/validate/write
- `rc_process.py` → monitor start/stop/process control
- `rc_status.py` → recording status calculations
- `rc_logs.py` → log parsing and issue detection

## Run

Use the root launcher:

- `LAUNCH_APP.bat`
- `LAUNCH_APP.sh` (Linux/macOS)

Or manually:

- `python app/radio_control_app.py`

## Service resilience (watchdog)

- Install watchdog task on Windows: `scripts/INSTALL_WATCHDOG_TASK.bat`
- Remove watchdog task: `scripts/UNINSTALL_WATCHDOG_TASK.bat`
- Watchdog runs every minute and starts monitor if it is down.

## Packaging updates

- Use `scripts/BUILD_NO_PYTHON_WINDOWS.bat`
- Build now includes:
	- `RadioControlApp.exe`
	- `rc_backend_service.exe`
	- `rc_watchdog.exe`
	- watchdog task install/uninstall scripts
- Build also produces `dist/portable.zip` for transfer/deployment.

## Notes

- Batch scripts are consolidated under `scripts/`.
- Recordings continue under `RadioRecordings/<Station>/YYYY/MM/DD`.
- GUI is only for control and visibility.
- `LAUNCH_APP.bat` and `LAUNCH_APP.sh` are intentionally kept at root for quick access.
- Use **Run Self Check** in the GUI to re-validate environment readiness anytime.

## Batch relevancy with GUI

- Primary backend: `app/rc_backend_service.py`.
- `scripts/radio_master.bat` is legacy and not required for normal GUI operation.

## Security hardening in app

- No shell command interpolation from user inputs.
- Station add/remove uses strict validation (name/url format, length limits, forbidden characters).
- `stations.txt` updates are atomic (`tempfile` + `os.replace`) to avoid corruption.
- Monitor state uses PID file checks to reduce accidental process mis-detection.
