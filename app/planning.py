"""Pure scheduling/calendar data for the web API — no UI dependencies.

The old customtkinter GUI computed these inline; the web backend imports them here
so it never has to load customtkinter. Everything returns JSON-friendly values.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta

from .paths import LOGS_DIR
from .settings import DAY_NAMES, fire_weekday

_MARKER_RE = re.compile(r"^booked-(\d{4}-\d{2}-\d{2})-(\w+)\.flag$")
_FIRE_HOUR, _FIRE_MIN = 18, 55


def _h12(dt: datetime) -> str:
    fmt = "%a %d %b, %#I:%M %p" if sys.platform == "win32" else "%a %d %b, %-I:%M %p"
    return dt.strftime(fmt)


def _fmt_in(delta: timedelta) -> str:
    days = delta.days
    hours = delta.seconds // 3600
    mins = (delta.seconds % 3600) // 60
    if days > 0:
        return f"in {days}d {hours}h"
    if hours > 0:
        return f"in {hours}h {mins}m"
    return f"in {mins}m"


def upcoming_fires(settings: dict, count: int = 4) -> list[dict]:
    """Next N scheduled fire moments (recurring days + future one-offs)."""
    now = datetime.now()
    rows: list[dict] = []
    for d in DAY_NAMES:
        if not settings["days"][d].get("enabled"):
            continue
        days_ahead = (fire_weekday(d) - now.weekday()) % 7
        fire = (now + timedelta(days=days_ahead)).replace(hour=_FIRE_HOUR, minute=_FIRE_MIN, second=0, microsecond=0)
        if days_ahead == 0 and now > fire:
            fire += timedelta(days=7)
        rows.append({"fire": fire, "play": fire + timedelta(days=15),
                     "target_time": settings["days"][d]["target_time"], "one_off": False})
    for oo in settings.get("one_offs", []):
        try:
            play = datetime.fromisoformat(oo["play_date"])
        except Exception:
            continue
        fire = (play - timedelta(days=15)).replace(hour=_FIRE_HOUR, minute=_FIRE_MIN, second=0, microsecond=0)
        if fire > now:
            rows.append({"fire": fire, "play": play,
                         "target_time": oo.get("target_time", "08:12"), "one_off": True})
    rows.sort(key=lambda r: r["fire"])
    out = []
    for r in rows[:count]:
        out.append({
            "fire_pretty": _h12(r["fire"]),
            "play_pretty": r["play"].strftime("%a %d %b"),
            "target_time": r["target_time"],
            "in_str": _fmt_in(r["fire"] - now),
            "one_off": r["one_off"],
        })
    return out


def _log_events() -> dict[str, dict]:
    """Booked (markers) + failed (run logs) events keyed by ISO play date."""
    events: dict[str, dict] = {}
    if not LOGS_DIR.exists():
        return events
    for f in LOGS_DIR.glob("booked-*.flag"):
        m = _MARKER_RE.match(f.name)
        if not m:
            continue
        slot = "?"
        try:
            slot = f.read_text(encoding="utf-8", errors="replace").strip() or "?"
        except Exception:
            pass
        events[m.group(1)] = {"kind": "booked", "time": slot, "detail": f"Booked {slot}"}
    for lf in LOGS_DIR.glob("run-*.log"):
        try:
            txt = lf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"fire target (\d{4}-\d{2}-\d{2})", txt)
        if not m:
            continue
        try:
            play_iso = (date.fromisoformat(m.group(1)) + timedelta(days=15)).isoformat()
        except ValueError:
            continue
        if play_iso not in events and "FAIL" in txt and "OK" not in txt:
            events[play_iso] = {"kind": "failed", "detail": "Booking attempt failed"}
    return events


def recent_runs(limit: int = 40) -> list[dict]:
    """Recent booking-run records from the logs dir, newest first."""
    out: list[dict] = []
    if not LOGS_DIR.exists():
        return out
    files = list(LOGS_DIR.glob("run-*.log")) + list(LOGS_DIR.glob("crash-*.log"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if f.name.startswith("crash-"):
            kind = "crash"
        elif "FAIL" in txt and "OK —" not in txt and "OK -" not in txt:
            kind = "fail"
        else:
            kind = "ok"
        first = next((ln.strip() for ln in txt.splitlines() if ln.strip()), f.name)
        out.append({
            "when": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            "kind": kind,
            "title": first[:140],
            "detail": txt[:6000],
            "file": f.name,
        })
    return out


def calendar_days(settings: dict, live_bookings: list[dict] | None = None,
                  weeks_ahead: int = 78) -> dict:
    """{today, days: {iso: {kind, time, ...}}} — one entry per day that has a dot.
    Priority: live on-site booking > local booked/failed marker > planned."""
    today = date.today()
    now = datetime.now()
    until = today + timedelta(weeks=weeks_ahead)
    days: dict[str, dict] = {}

    # Planned recurring days (indefinite — only those whose fire is still ahead).
    for d_name in DAY_NAMES:
        if not settings["days"][d_name].get("enabled"):
            continue
        tt = settings["days"][d_name]["target_time"]
        day_wd = DAY_NAMES.index(d_name)
        play = today + timedelta(days=(day_wd - today.weekday()) % 7)
        while play <= until:
            fire = datetime.combine(play - timedelta(days=15),
                                    datetime.min.time().replace(hour=_FIRE_HOUR, minute=_FIRE_MIN))
            if fire > now:
                days.setdefault(play.isoformat(), {"kind": "planned", "time": tt})
            play += timedelta(weeks=1)

    # One-off planned days.
    for oo in settings.get("one_offs", []):
        iso = oo.get("play_date")
        try:
            if iso and date.fromisoformat(iso) >= today:
                days[iso] = {"kind": "planned", "time": oo.get("target_time", "08:12"), "one_off": True}
        except ValueError:
            continue

    # Local markers/failures (override planned).
    for iso, ev in _log_events().items():
        days[iso] = ev

    # Live on-site bookings (ground truth, override everything).
    for b in (live_bookings or []):
        iso = b.get("date")
        if not iso:
            continue
        partners = [p for p in b.get("partners", []) if p]
        days[iso] = {"kind": "booked", "time": b.get("time", "?"),
                     "partners": partners, "detail": f"On-site booking {b.get('time', '')}".strip()}

    return {"today": today.isoformat(), "days": days}
