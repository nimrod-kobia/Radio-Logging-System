# Enterprise Modernization Roadmap

## Current state

- GUI controller in Python/Tkinter.
- Recording backend driven by batch script (`scripts/radio_master.bat`) and `ffmpeg` workers.
- File-based state (`stations.txt`, runtime logs, pid files).

## Target enterprise architecture

### 1) Replace batch orchestration with a Python service

Build a long-running backend service (same language stack) that:

- Manages stations/workers in-process (no `.bat` generation).
- Spawns and supervises `ffmpeg` subprocesses directly.
- Exposes health and control APIs (`start`, `stop`, `restart`, `add/remove station`).

### 2) Split into control plane + data plane

- **Control plane**: station config, scheduling, process supervision, retries.
- **Data plane**: audio capture/transcode with `ffmpeg`.

### 3) API-first backend

Use FastAPI or gRPC for backend API:

- `GET /stations`
- `POST /stations`
- `DELETE /stations/{id}`
- `POST /stations/{id}/restart`
- `GET /stations/{id}/status`
- `GET /stations/{id}/logs`

GUI becomes a thin client against this API.

### 4) Move config/state to a database

Replace `stations.txt` with SQLite/PostgreSQL:

- station metadata
- worker lifecycle state
- incident history
- retry/backoff state

### 5) Structured logging + observability

- JSON logs per station and service component.
- Central log sink (ELK/Opensearch/Azure Monitor).
- Metrics (Prometheus): uptime, reconnects, bytes written, stale-writes.
- Alerts: no-write threshold, repeated source failures, process crash loops.

### 6) Reliability patterns

- Supervisor with backoff and circuit breakers per station.
- Graceful shutdown and restart policies.
- Idempotent recovery on service restart.
- Optional queue for control commands.

### 7) Security hardening

- Principle of least privilege service account.
- Input validation and strict allowlists for stream URLs.
- Signed releases and dependency scanning (pip-audit, safety, SCA in CI).
- Secret/token storage in OS keyring or vault.

### 8) Deployment model

- Package backend as Windows Service (`nssm`/`pywin32`) and systemd for Linux.
- Containerize where appropriate for non-Windows environments.
- Blue/green or rolling upgrades.

### 9) UI modernization path

Phase A: Keep Tkinter UI, switch data source from local files to service API.

Phase B: Replace with web UI (React/Vue) served by backend API for multi-user access.

## Suggested phased migration

1. Implement Python backend process manager (keep existing GUI).
2. Add REST API and migrate GUI actions to API calls.
3. Replace file state with DB.
4. Introduce metrics, alerting, and centralized logs.
5. Retire `scripts/radio_master.bat` and worker-bat generation.

## Language strategy

- Keep Python for both backend and control UI now (fastest transition).
- Optionally move backend to .NET/Go later if needed for org standards.

Python remains enterprise-capable when combined with:

- strict typing (`mypy`),
- tests (`pytest`),
- API contracts,
- CI/CD checks,
- observability and security controls.
