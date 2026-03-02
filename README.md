# Radio Control App

Desktop app for managing and monitoring radio stream recording.

## Quick start

### Run the app

- Windows: `LAUNCH_APP.bat`
- Manual run: `python app/radio_control_app.py`

### What you need

- `ffmpeg` at `C:\ffmpeg\bin\ffmpeg.exe` on Windows targets

## What the app does

- Shows station recording status in the table.
- Starts/stops the backend recording supervisor.
- Auto-refreshes status every 5 seconds.
- Lets you add/remove stations safely.
- Shows recent issue details from logs.
- Runs self-checks before starting monitoring.

## Important folders/files

- `stations.txt` → your station list
- `Runtime/` → runtime logs and health files
- `RadioRecordings/` → saved recordings by station/date
- `app/rc_backend_service.py` → main backend supervisor

## Deployment (manual copy)

Use this simple method:

1. Stop app/backend on target machine.
2. Copy this project folder to the target machine and replace app files.
3. Keep these unchanged:
   - `RadioRecordings/`
   - `Runtime/`
   - `stations.txt`
4. Start with `LAUNCH_APP.bat`.

## Notes

- Use **Run Self Check** in the GUI any time you want to verify setup health.
- Recordings are stored as `RadioRecordings/<Station>/YYYY/MM/DD`.
- If target device shows `LoadLibrary` / missing DLL errors, make sure the target has a complete app copy (not partial files) and required runtime dependencies.
