"""
Email alert system for the radio recording service.

Design contract:
- Instantiated once by WorkerManager at service boot.
- Called once per sync cycle via alerter.evaluate(station_statuses, heartbeat_ok).
- All SMTP sends are dispatched to a background daemon thread; never blocks sync.
- Alert state is persisted to Runtime/alert_state.json so incidents survive restarts.
- Configuration is read from <repo-root>/email_config.json on every evaluate() call
  so the user can change settings in the GUI without restarting the service.
"""

import json
import smtplib
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from rc_config import LOGS, ROOT

# ── File paths ───────────────────────────────────────────────────────────────
EMAIL_CONFIG_FILE: Path = ROOT / "email_config.json"
ALERT_STATE_FILE: Path = LOGS / "alert_state.json"

# Statuses that constitute a "down" incident
DOWN_STATUSES: frozenset = frozenset({"OFFLINE", "NO WRITE", "NO AUDIO", "ERROR"})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def _write_json_atomic_local(path: Path, payload: dict) -> None:
    """Atomic JSON write — local copy to avoid circular import with rc_backend_service."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        import os
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ── Incident dataclass (plain class for Python 3.9 compat) ──────────────────

class StationIncident:
    __slots__ = ("station_name", "status", "first_seen_ts", "alerted", "recovered")

    def __init__(
        self,
        station_name: str,
        status: str,
        first_seen_ts: float,
        alerted: bool = False,
        recovered: bool = False,
    ) -> None:
        self.station_name = station_name
        self.status = status
        self.first_seen_ts = first_seen_ts
        self.alerted = alerted
        self.recovered = recovered


# ── Main alerter class ───────────────────────────────────────────────────────

class RadioAlerter:
    """
    Evaluates station statuses each sync cycle and fires email alerts
    when stations remain down beyond the configured threshold.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._incidents: dict[str, StationIncident] = {}
        self._heartbeat_incident_active: bool = False
        self._heartbeat_first_stale_ts: Optional[float] = None
        self._heartbeat_alerted: bool = False
        self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        station_statuses: dict,
        heartbeat_ok: bool = True,
    ) -> None:
        """
        Called once per sync cycle.

        station_statuses: mapping of station_name -> current status string
        heartbeat_ok:     True if the heartbeat file is fresh. Always pass True
                          when called from within the service itself.
        """
        cfg = self._load_config()
        if cfg is None or not cfg.get("enabled", False):
            return

        now_ts = time.time()
        threshold_seconds = max(1, int(cfg.get("alert_threshold_minutes", 5))) * 60

        self._evaluate_stations(station_statuses, now_ts, threshold_seconds, cfg)
        self._evaluate_heartbeat(heartbeat_ok, now_ts, threshold_seconds, cfg)
        self._save_state()

    # ── Config loading ────────────────────────────────────────────────────────

    def _load_config(self) -> Optional[dict]:
        """Re-read email_config.json on every call for live GUI changes."""
        try:
            if not EMAIL_CONFIG_FILE.exists():
                return None
            cfg = json.loads(EMAIL_CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                return None
            return cfg
        except Exception:
            return None

    # ── Station incident lifecycle ────────────────────────────────────────────

    def _evaluate_stations(
        self,
        station_statuses: dict,
        now_ts: float,
        threshold_seconds: int,
        cfg: dict,
    ) -> None:
        with self._lock:
            for station_name, status in station_statuses.items():
                is_down = status in DOWN_STATUSES

                if is_down:
                    incident = self._incidents.get(station_name)
                    if incident is None:
                        incident = StationIncident(
                            station_name=station_name,
                            status=status,
                            first_seen_ts=now_ts,
                        )
                        self._incidents[station_name] = incident
                    else:
                        incident.status = status

                    if not incident.alerted:
                        elapsed = now_ts - incident.first_seen_ts
                        if elapsed >= threshold_seconds:
                            self._send_async(cfg, "alert", station_name, status, elapsed)
                            incident.alerted = True

                else:
                    incident = self._incidents.get(station_name)
                    if incident is not None:
                        if incident.alerted and not incident.recovered:
                            self._send_async(cfg, "recovery", station_name, status, 0.0)
                            incident.recovered = True
                        if not incident.alerted or incident.recovered:
                            del self._incidents[station_name]

    # ── Heartbeat incident lifecycle ──────────────────────────────────────────

    def _evaluate_heartbeat(
        self,
        heartbeat_ok: bool,
        now_ts: float,
        threshold_seconds: int,
        cfg: dict,
    ) -> None:
        """No-op unless alert_on_service_crash is true and heartbeat_ok is False."""
        if not cfg.get("alert_on_service_crash", False):
            return

        if not heartbeat_ok:
            if self._heartbeat_first_stale_ts is None:
                self._heartbeat_first_stale_ts = now_ts
            elapsed = now_ts - self._heartbeat_first_stale_ts
            if not self._heartbeat_alerted and elapsed >= threshold_seconds:
                self._send_async(cfg, "heartbeat_down", "__service__", "CRASHED", elapsed)
                self._heartbeat_alerted = True
                self._heartbeat_incident_active = True
        else:
            if self._heartbeat_incident_active and self._heartbeat_alerted:
                self._send_async(cfg, "heartbeat_recovery", "__service__", "ALIVE", 0.0)
            self._heartbeat_first_stale_ts = None
            self._heartbeat_alerted = False
            self._heartbeat_incident_active = False

    # ── Email dispatch ────────────────────────────────────────────────────────

    def _send_async(
        self,
        cfg: dict,
        alert_type: str,
        station_name: str,
        status: str,
        elapsed_seconds: float,
    ) -> None:
        """Build and send email on a daemon thread — never blocks the sync loop."""
        subject, body = self._build_message(alert_type, station_name, status, elapsed_seconds)
        thread = threading.Thread(
            target=self._send_email,
            args=(cfg, subject, body),
            daemon=True,
            name=f"alerter-{alert_type}-{station_name}",
        )
        thread.start()

    def _build_message(
        self,
        alert_type: str,
        station_name: str,
        status: str,
        elapsed_seconds: float,
    ) -> tuple:
        """Return (subject, plain-text body) for the given alert type."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elapsed_str = _format_elapsed(elapsed_seconds)

        if alert_type == "alert":
            subject = f"[Radio Alert] {station_name} is {status}"
            body = (
                f"STATION DOWN ALERT\n"
                f"{'=' * 40}\n"
                f"Station    : {station_name}\n"
                f"Status     : {status}\n"
                f"Duration   : {elapsed_str}\n"
                f"Time (local): {now_str}\n\n"
                f"The station has been in a non-recording state for longer than "
                f"the configured threshold.\n\n"
                f"Check the station log in the Runtime/ folder for details."
            )
        elif alert_type == "recovery":
            subject = f"[Radio Recovery] {station_name} is back ({status})"
            body = (
                f"STATION RECOVERY\n"
                f"{'=' * 40}\n"
                f"Station    : {station_name}\n"
                f"Status     : {status}\n"
                f"Time (local): {now_str}\n\n"
                f"The station has resumed a healthy recording state."
            )
        elif alert_type == "heartbeat_down":
            subject = "[Radio Alert] Recording service appears to have crashed"
            body = (
                f"SERVICE CRASH ALERT\n"
                f"{'=' * 40}\n"
                f"The recording service heartbeat has gone stale.\n"
                f"Duration   : {elapsed_str}\n"
                f"Time (local): {now_str}\n\n"
                f"The backend service (rc_backend_service) may have crashed.\n"
                f"Check Runtime/service.log and restart the service if needed."
            )
        elif alert_type == "heartbeat_recovery":
            subject = "[Radio Recovery] Recording service heartbeat restored"
            body = (
                f"SERVICE RECOVERY\n"
                f"{'=' * 40}\n"
                f"The recording service heartbeat is fresh again.\n"
                f"Time (local): {now_str}\n"
            )
        else:
            subject = f"[Radio Alert] {alert_type}: {station_name}"
            body = f"alert_type={alert_type}, station={station_name}, status={status}"

        return subject, body

    def _send_email(self, cfg: dict, subject: str, body: str) -> None:
        """
        Perform the actual SMTP send. Runs on a daemon thread.
        Swallows all exceptions and logs failures to service.log.
        """
        try:
            smtp_host: str = cfg.get("smtp_host", "smtp.gmail.com")
            smtp_port: int = int(cfg.get("smtp_port", 465))
            sender: str = cfg.get("sender_email", "")
            recipients: list = cfg.get("recipient_emails", [])
            password: str = cfg.get("app_password", "")
            use_ssl: bool = bool(cfg.get("use_ssl", True))

            if not all([smtp_host, sender, recipients, password]):
                return  # Config incomplete; skip silently

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(body, "plain", "utf-8"))

            context = ssl.create_default_context()
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15) as server:
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())

        except Exception as exc:
            try:
                _log_service_message(f"ALERTER_SEND_FAIL {exc.__class__.__name__}: {exc}")
            except Exception:
                pass

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist current incident state atomically to Runtime/alert_state.json."""
        try:
            with self._lock:
                payload = {
                    "saved_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "station_incidents": {
                        name: {
                            "station_name": inc.station_name,
                            "status": inc.status,
                            "first_seen_ts": inc.first_seen_ts,
                            "alerted": inc.alerted,
                            "recovered": inc.recovered,
                        }
                        for name, inc in self._incidents.items()
                    },
                    "heartbeat_incident": {
                        "active": self._heartbeat_incident_active,
                        "first_stale_ts": self._heartbeat_first_stale_ts,
                        "alerted": self._heartbeat_alerted,
                    },
                }
            _write_json_atomic_local(ALERT_STATE_FILE, payload)
        except Exception:
            pass

    def _load_state(self) -> None:
        """
        Restore persisted state on service restart so we don't re-alert
        for incidents that were already alerted before the restart.
        Incidents older than 24 hours are discarded.
        """
        try:
            if not ALERT_STATE_FILE.exists():
                return
            data = json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
            now_ts = time.time()

            for name, entry in data.get("station_incidents", {}).items():
                age = now_ts - float(entry.get("first_seen_ts", 0))
                if age > 86400:
                    continue
                self._incidents[name] = StationIncident(
                    station_name=entry["station_name"],
                    status=entry["status"],
                    first_seen_ts=float(entry["first_seen_ts"]),
                    alerted=bool(entry["alerted"]),
                    recovered=bool(entry["recovered"]),
                )

            hb = data.get("heartbeat_incident", {})
            self._heartbeat_incident_active = bool(hb.get("active", False))
            raw_ts = hb.get("first_stale_ts")
            self._heartbeat_first_stale_ts = float(raw_ts) if raw_ts is not None else None
            self._heartbeat_alerted = bool(hb.get("alerted", False))
        except Exception:
            pass


# ── Module-level log helper (avoids circular import) ─────────────────────────

def _log_service_message(msg: str) -> None:
    """Write a timestamped line to Runtime/service.log without importing rc_backend_service."""
    try:
        log_file = LOGS / "service.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass
