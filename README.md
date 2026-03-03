# Radio Control App

Desktop app for recording and managing internet radio streams. It runs a background service that records all stations simultaneously to MP3 files, organised by station and date.

---

## What You Need (Prerequisites)

Install these **before** running the app.

### 1. Python 3.10 or newer

Download from: https://www.python.org/downloads/

**Critical — during installation:**

- On the first screen, tick **"Add Python to PATH"** before clicking Install Now.
- If you missed this, fix it manually (see below).

**Verify Python is in PATH:**
Open Command Prompt and run:
```
python --version
```
You should see something like `Python 3.13.1`. If you get `'python' is not recognized`, Python is not in PATH.

**To add Python to PATH manually:**
1. Press **Win + S**, search for **"Edit the system environment variables"**, open it.
2. Click **Environment Variables**.
3. Under **User variables**, select **Path** and click **Edit**.
4. Click **New** and add the folder where Python is installed, e.g.:
   - `C:\Python313\`
   - `C:\Python313\Scripts\`
   - (The exact folder name depends on your Python version — check `C:\` for a folder starting with `Python`)
5. Click **OK** on all dialogs.
6. **Restart your PC** (or sign out and back in) for the change to take effect.

> If Python was installed from the Microsoft Store, it is usually already in PATH automatically.

---

### 2. ffmpeg

ffmpeg must be placed at exactly this path:

```
C:\ffmpeg\bin\ffmpeg.exe
```

**How to install ffmpeg:**
1. Download a Windows build from: https://www.gyan.dev/ffmpeg/builds/ (get `ffmpeg-release-full.7z` or the `.zip` version)
2. Extract it.
3. Inside the extracted folder, find the `bin` folder (it contains `ffmpeg.exe`, `ffplay.exe`, `ffprobe.exe`).
4. Create the folder `C:\ffmpeg\bin\` and copy those three `.exe` files into it.

**Verify ffmpeg works:**
Open Command Prompt and run:
```
C:\ffmpeg\bin\ffmpeg.exe -version
```
You should see ffmpeg version info.

---

## Folder Structure

```
LAUNCH_APP.bat               ← Double-click to start the app
stations.txt                 ← List of radio stations to record
README.md
app\
    LAUNCH_APP.vbs           ← Hidden-window launcher (called by the .bat)
    radio_control_app.py     ← GUI frontend
    rc_backend_service.py    ← Recording supervisor (runs in background)
    rc_config.py             ← Configuration constants
    rc_process.py            ← Process management (start/stop)
    rc_station_store.py      ← stations.txt read/write
    rc_status.py             ← Status/health checks
    rc_logs.py               ← Log helpers
    rc_power.py              ← Keep-awake (prevents sleep during recording)
    rc_preflight.py          ← Startup self-checks
Runtime\                     ← Runtime logs and health files (auto-created)
RadioRecordings\             ← Saved recordings by station/date (auto-created)
```

---

## Starting the App

Double-click **`LAUNCH_APP.bat`** in the project root folder.

The app window will open. No CMD/console window should appear during normal use.

**Manual start (for debugging):**
```
python app\radio_control_app.py
```

---

## Using the App

### Start Recording
Click **Start Recording** to begin recording all stations in `stations.txt`.

- The backend supervisor starts as a background process.
- Each station gets its own ffmpeg process streaming and saving audio.
- Status updates every 5 seconds automatically.

### Stop Recording
Click **Stop Recording** to stop all recordings immediately.

- All ffmpeg processes are terminated.
- The backend supervisor is shut down.
- A stop flag is written so recording does not restart automatically.

### Station Table
Shows the real-time recording status of every station:

| Column | Meaning |
|---|---|
| Station | Station name from `stations.txt` |
| Status | `RECORDING`, `IDLE`, `STARTING`, `NO WRITE`, `NO AUDIO`, `ERROR` |
| Issue | Short description of any problem |
| Detail | Longer technical detail |
| Latest File | Most recently written recording file |

### Adding a Station
1. Type the station name in the **Name** field.
2. Paste the stream URL in the **Stream URL** field.
3. Click **Add Station**.

Station names may contain letters, numbers, spaces, `_`, `-`, `.`, `/`.
Stream URLs must start with `http://` or `https://`.

### Removing a Station
Select a station row in the table, then click **Remove Selected**.

### Viewing Recordings
Select a station in the table, then use the **Yesterday / Today / Pick Date** buttons to browse recording files for that day. Click **Play Selected** to open a recording in your default media player.

### Run Self Check
Click **Run Self Check** at any time to verify that Python, ffmpeg, folders, and the backend are all correctly set up. Any warnings or failures will be listed.

---

## stations.txt Format

One station per line, name and URL separated by `|`:

```
Station_Name|https://stream.url/path
ZNBC_Radio_1|https://eu6.fastcast4u.com/proxy/radio1?mp=/1
Hot_FM_Lusaka|https://s2.yesstreaming.net:17091/stream
```

- Names can contain letters, numbers, space, `_`, `-`, `.`, `/`.
- URLs must be `http://` or `https://`.
- Lines without `|` are ignored.
- Edit this file while recording is running — the backend picks up changes within 5 seconds.

---

## Recordings Storage

Recordings are saved to:
```
RadioRecordings\<StationName>\YYYY\MM\DD\YYYY-MM-DD-HH-MM-SS.mp3
```

Example:
```
RadioRecordings\ZNBC_Radio_1\2026\03\03\2026-03-03-14-30-00.mp3
```

Files are recorded continuously. A new file starts automatically at each recording restart or reconnect.

---

## Deployment to Another PC

Use this procedure to copy the app to a new machine.

### What to copy
Copy the **entire project folder** to the target machine. You can use a USB drive or network share.

### What NOT to overwrite
On the target machine, preserve these if they exist:
- `RadioRecordings\` — contains saved recordings
- `Runtime\` — contains logs and state
- `stations.txt` — contains the station list for that machine

### Step-by-step

1. On the target PC, install **Python 3** with **"Add Python to PATH"** ticked.
2. Install **ffmpeg** at `C:\ffmpeg\bin\ffmpeg.exe` (see prerequisites above).
3. Copy the project folder to the target PC (e.g. to the Desktop).
4. Do **not** overwrite `RadioRecordings\`, `Runtime\`, or `stations.txt` if they already exist.
5. Double-click `LAUNCH_APP.bat` to start.
6. Click **Run Self Check** to confirm everything is working.

---

## Troubleshooting

### "App files not found"
The launcher could not find `app\radio_control_app.py`.
- Make sure the **entire project folder** was copied — not just `LAUNCH_APP.bat` on its own.
- The folder structure must be intact (`LAUNCH_APP.bat` and the `app\` subfolder must be in the same directory).

### "Python interpreter not found"
Python is not in the system PATH.
- Follow the Python PATH setup steps in the **Prerequisites** section above.
- After adding Python to PATH, restart the PC before trying again.

### "ffmpeg not found at C:\ffmpeg\bin\ffmpeg.exe"
- Verify the file exists at exactly `C:\ffmpeg\bin\ffmpeg.exe`.
- Re-run the **Run Self Check** after installing ffmpeg.

### Station shows `NO WRITE` or `NO AUDIO`
- The stream URL may be down or changed — check if you can open the URL in a browser or VLC.
- Update the URL in `stations.txt` and the backend will reconnect within 5 seconds.
- Some streams require authentication — if you see `401 Unauthorized` in the station log, the stream is password-protected or the URL has changed.

### Recording restarts after Stop
- This was a known bug that has been fixed. Make sure you have the latest version of all files in the `app\` folder.
- After updating files, close the app and reopen via `LAUNCH_APP.bat`.

### App window does not appear
- Check `Runtime\launch_last.log` for error details.
- Try running `python app\radio_control_app.py` in a Command Prompt window to see any error messages.

### Missing DLL / LoadLibrary error on another PC
- Install the **Microsoft Visual C++ Redistributable**: https://aka.ms/vs/17/release/vc_redist.x64.exe

---

## Log Files

All logs are in the `Runtime\` folder:

| File | Contents |
|---|---|
| `service.log` | Backend supervisor activity and errors |
| `metrics.json` | Per-station recording statistics |
| `service_heartbeat.json` | Backend alive status (updated every 5s) |
| `restart_state.json` | Retry backoff state for failed stations |
| `stopped_intentionally.flag` | Present when recording was manually stopped |
| `<StationName>.log` | Per-station ffmpeg output and errors |

To diagnose a specific station's problem: select it in the table and click **Open Selected Log**.
