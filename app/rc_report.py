"""
Weekly uptime report generator for Brandcomm Radio Control.

- Covers Monday 06:00 through Sunday 23:59 (18 broadcast hours per day).
- A given hour is counted as "covered" if at least one MP3 segment was
  recorded with a filename start-time falling within that hour.
- Reports are saved as HTML files under  <root>/Reports/YYYY/WW/
  e.g.  Reports/2026/12/report_2026-W12.html
- Called automatically by rc_backend_service on Sunday just after midnight.
"""
import re
from datetime import date, timedelta
from pathlib import Path

from rc_config import RECORDINGS, ROOT, safe_station_name
from rc_station_store import read_stations

REPORTS_DIR = ROOT / "Reports"

REPORT_START_HOUR = 6
REPORT_END_HOUR = 24           # midnight — hour 0 next day is out of scope
TOTAL_HOURS = REPORT_END_HOUR - REPORT_START_HOUR   # 18

_FNAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(\d{2})-\d{2}-\d{2}\.mp3$")


# ── Data layer ────────────────────────────────────────────────────────────────

def _station_day_uptime(station_name: str, target_day: date) -> dict:
    """Return coverage info for *station_name* on *target_day*.

    Keys:
        covered  – broadcast hours (6–23) that contain at least one MP3
        total    – 18 (the full 06:00-midnight window)
        pct      – float 0–100
    """
    safe = safe_station_name(station_name)
    day_dir = (
        RECORDINGS
        / safe
        / f"{target_day.year:04d}"
        / f"{target_day.month:02d}"
        / f"{target_day.day:02d}"
    )

    covered_hours: set[int] = set()
    if day_dir.exists():
        for mp3 in day_dir.glob("*.mp3"):
            m = _FNAME_RE.match(mp3.name)
            if m:
                hour = int(m.group(1))
                if REPORT_START_HOUR <= hour < REPORT_END_HOUR:
                    covered_hours.add(hour)

    covered = len(covered_hours)
    pct = round(covered / TOTAL_HOURS * 100, 1)
    return {"covered": covered, "total": TOTAL_HOURS, "pct": pct}


def _week_dates(anchor: date) -> list[date]:
    """Return the Mon–Sun dates of the ISO week that contains *anchor*."""
    monday = anchor - timedelta(days=anchor.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


# ── HTML rendering ────────────────────────────────────────────────────────────

def _pct_cell(info: dict, is_today_or_future: bool) -> str:
    if is_today_or_future:
        return "<td class='na'>–</td>"
    pct = info["pct"]
    if pct >= 90:
        cls = "high"
    elif pct >= 70:
        cls = "mid"
    else:
        cls = "low"
    return (
        f"<td class='{cls}'>{pct:.0f}%"
        f"<span class='sub'>{info['covered']}/{info['total']}h</span></td>"
    )


def _render_html(week_dates: list[date], station_rows: dict[str, list[dict]]) -> str:
    week_start = week_dates[0]
    week_end = week_dates[6]
    iso_week = week_start.isocalendar()[1]
    generated_on = date.today().strftime("%d %b %Y")

    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    header_cells = "".join(
        f"<th>{label}<br><small>{week_dates[i].strftime('%d %b')}</small></th>"
        for i, label in enumerate(day_labels)
    )

    rows_html = ""
    for station_name, day_data in station_rows.items():
        cells = ""
        for i, info in enumerate(day_data):
            future = week_dates[i] >= date.today()
            cells += _pct_cell(info, future)
        rows_html += f"<tr><td class='stn'>{station_name}</td>{cells}</tr>\n"

    if not rows_html:
        rows_html = (
            "<tr><td colspan='8' class='empty'>No stations configured</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Weekly Uptime Report – Week {iso_week} {week_start.year}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#f0f2f5;padding:24px}}
h1{{font-size:1.3rem;font-weight:700;color:#1a1a2e;margin-bottom:4px}}
.meta{{font-size:.8rem;color:#888;margin-bottom:20px}}
.card{{background:#fff;border-radius:12px;box-shadow:0 1px 6px rgba(0,0,0,.1);overflow:hidden;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse}}
th{{background:#263238;color:#cfd8dc;text-align:center;padding:10px 12px;
   font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}}
th:first-child{{text-align:left}}
td{{padding:10px 12px;border-bottom:1px solid #f0f0f0;font-size:.85rem;
   text-align:center;font-weight:600;vertical-align:middle}}
td.stn{{text-align:left;font-weight:500;color:#333;white-space:nowrap}}
td.high{{background:#e8f5e9;color:#1b5e20}}
td.mid{{background:#fff8e1;color:#e65100}}
td.low{{background:#ffebee;color:#b71c1c}}
td.na{{background:#fafafa;color:#bbb;font-weight:400}}
td .sub{{display:block;font-size:.65rem;font-weight:400;margin-top:2px}}
td.empty{{text-align:center;color:#aaa;padding:28px}}
tr:last-child td{{border-bottom:none}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:.78rem;margin-bottom:12px}}
.legend span{{padding:4px 12px;border-radius:4px;font-weight:600}}
.l-h{{background:#e8f5e9;color:#1b5e20}}
.l-m{{background:#fff8e1;color:#e65100}}
.l-l{{background:#ffebee;color:#b71c1c}}
.footer{{color:#bbb;font-size:.72rem;text-align:center;margin-top:8px}}
</style>
</head>
<body>
<h1>Weekly Uptime Report &mdash; Week {iso_week}, {week_start.year}</h1>
<div class="meta">
  Period: {week_start.strftime('%A %d %b %Y')} &ndash; {week_end.strftime('%A %d %b %Y')}
  &nbsp;&bull;&nbsp; Broadcast window: 06:00 &ndash; 00:00 (18 h/day)
  &nbsp;&bull;&nbsp; Generated: {generated_on}
</div>
<div class="legend">
  <span class="l-h">&#9632; &ge;90% on air</span>
  <span class="l-m">&#9632; 70&ndash;89%</span>
  <span class="l-l">&#9632; &lt;70% &mdash; needs attention</span>
</div>
<div class="card">
<table>
  <thead><tr><th>Station</th>{header_cells}</tr></thead>
  <tbody>{rows_html}</tbody>
</table>
</div>
<div class="footer">
  Each cell shows the % of broadcast hours (06:00&ndash;midnight) that contain
  at least one recorded MP3 segment, out of 18 possible hours.
</div>
</body>
</html>"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_and_save_weekly_report(anchor: date | None = None) -> Path:
    """Generate the HTML report for the ISO week containing *anchor* (default: today).

    Saves the file to  Reports/<year>/<week>/report_<year>-W<week>.html
    and returns the saved path.
    """
    if anchor is None:
        anchor = date.today()

    week_dates = _week_dates(anchor)
    iso_year, iso_week, _ = week_dates[0].isocalendar()

    stations = read_stations()
    station_rows: dict[str, list[dict]] = {}
    for station_name, _ in stations:
        station_rows[station_name] = [
            _station_day_uptime(station_name, d) for d in week_dates
        ]

    html = _render_html(week_dates, station_rows)

    out_dir = REPORTS_DIR / f"{iso_year:04d}" / f"{iso_week:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"report_{iso_year:04d}-W{iso_week:02d}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    # Manual run: python app/rc_report.py
    import sys
    path = generate_and_save_weekly_report()
    print(f"Report saved: {path}")
    sys.exit(0)
