# Radio Control App

Desktop app for managing and monitoring radio stream recording.

## Quick start

### Run the app

- Windows: `LAUNCH_APP.bat`
- Manual run: `python app/radio_control_app.py`

### What you need

- Python 3 (for source run/build scripts)
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

## Build and package (developer machine)

- Build executables: `updates/BUILD_NO_PYTHON_WINDOWS.bat`
- Create update package: `updates/MAKE_UPDATE_PACKAGE_WINDOWS.bat`
- Outputs:
  - `dist/portable/`
  - `dist/portable.zip`
  - `dist/update-package/`
  - `dist/update-package.zip`

`dist` is a build/output folder and is not required on target runtime machines.

## Update process (current)

### Recommended (safe package)

1. Build and package on dev machine.
2. Move `dist/update-package` (or zip) to target machine.
3. Run `FLASH_DRIVE_UPDATE.bat` from the package.
4. Enter the existing install folder when prompted.

The updater (`updates/APPLY_UPDATE_SAFE_WINDOWS.bat`) does this:

- Uses `update_manifest.json` checksum validation when present.
- Copies app files while preserving:
  - `RadioRecordings/`
  - `Runtime/`
  - `stations.txt`
- Removes target `update_manifest.json` after success.
- Removes target `dist/` after success.

### Fast manual method (developer swap)

1. Stop app/backend on target machine.
2. Replace app files from known-good build.
3. Keep these unchanged:
   - `RadioRecordings/`
   - `Runtime/`
   - `stations.txt`
4. Start with `LAUNCH_APP.bat`.

## Notes

- Use **Run Self Check** in the GUI any time you want to verify setup health.
- Recordings are stored as `RadioRecordings/<Station>/YYYY/MM/DD`.
- To clean Python cache files in this repo, run `updates/CLEAN_PYCACHE_WINDOWS.bat`.
