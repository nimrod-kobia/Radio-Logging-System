# Radio Control App

Desktop GUI for your radio recording system.

## What it does

- Shows station status in a table (RECORDING / WARMUP / NO WRITE / NO AUDIO / STARTING).
- Runs Python backend supervisor in the background (`app/rc_backend_service.py`) and falls back to monitor scripts only if needed.
- Stops monitor/worker/ffmpeg background processes from the GUI.
- Refreshes status automatically every 5 seconds.
- Adds stations from GUI with validation.
- Removes selected stations directly from GUI.
- Shows per-station issue analysis from latest log lines.
- Opens selected station log file directly from GUI.

## Runtime layout

- `Runtime/` contains both station logs and generated worker bat files.
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

## Notes

- Batch scripts are consolidated under `scripts/`.
- Recordings continue under `RadioRecordings/<Station>/YYYY/MM/DD`.
- GUI is only for control and visibility.
- `LAUNCH_APP.bat` and `LAUNCH_APP.sh` are intentionally kept at root for quick access.

## Batch relevancy with GUI

- Primary backend: `app/rc_backend_service.py`.
- Fallback backend: `scripts/radio_master.bat` (legacy monitor path).
- GUI is sufficient for daily operations; helper batch scripts are no longer required.

## Security hardening in app

- No shell command interpolation from user inputs.
- Station add/remove uses strict validation (name/url format, length limits, forbidden characters).
- `stations.txt` updates are atomic (`tempfile` + `os.replace`) to avoid corruption.
- Monitor state uses PID file checks to reduce accidental process mis-detection.
