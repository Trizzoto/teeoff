"""Fetch the user's CURRENT bookings from the West Beach Parks site.

Used by the dashboard calendar to show "already booked" days (green dots) for
slots grandpa already has, in addition to slots booked by this app.

Strategy:
  1. login
  2. call the Spring events API to enumerate TIMESHEET events in a date window
  3. detect the user's display name from one DefaultPartners.msp (freeRecord.0.full_name)
  4. for each event, GET the slot grid and look for that name
  5. cache results to a JSON file so launches are fast
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from booker.client import MiClubClient
from booker.parser import parse_event_page, parse_make_booking_form, parse_spring_events

from .settings import ResolvedConfig, load_settings, resolve

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
CACHE_PATH = PROJECT_ROOT / "logs" / "bookings-cache.json"


def _detect_user_name(client: MiClubClient, cfg: ResolvedConfig, today: date) -> str | None:
    """Pull the user's display name out of any partner form."""
    end = today + timedelta(days=14)
    try:
        spring = parse_spring_events(client.fetch_events_json(today, end, cfg.resource_id))
    except Exception as e:
        log.warning("detect_user_name: spring fetch failed: %s", e)
        return None
    for ev in spring:
        if ev.status != "Open" or "TIMESHEET" not in ev.title.upper():
            continue
        try:
            page = client.get(
                f"/members/bookings/open/event.msp"
                f"?booking_event_id={ev.event_id}&booking_resource_id={ev.resource_id}"
            )
            rows = parse_event_page(page.text)
            if not rows:
                continue
            any_row = next((r for r in rows if r.is_available), rows[0])
            partners = client.get(
                f"/members/bookings/open/DefaultPartners.msp"
                f"?booking_event_id={ev.event_id}&booking_row_id={any_row.row_id}&hasMultipleFees=false"
            )
            fields = parse_make_booking_form(partners.text)
            name = fields.get("freeRecord.0.full_name", [""])[0]
            if name:
                return name
        except Exception:
            continue
    return None


def fetch_existing_bookings(window_days: int = 75, past_days: int = 90) -> dict:
    """Return {"fetched_at": iso, "user": str, "bookings": [...]}.
    Bookings: [{"date": "YYYY-MM-DD", "time": "08:12 am", "event_id": int, "partners": [str,...]}]

    Scans both PAST (Results) and upcoming (Open) TIMESHEET events so the calendar
    shows the full booking history as well as what's coming up."""
    cfg = resolve(load_settings())
    started_at = datetime.now()
    out: dict = {"fetched_at": started_at.isoformat(timespec="seconds"), "user": None, "bookings": []}

    client = MiClubClient(cfg.effective_base_url, cfg.username, cfg.password)
    if not client.login():
        out["error"] = "login failed"
        return out

    today = date.today()
    user_name = _detect_user_name(client, cfg, today)
    if not user_name:
        out["error"] = "couldn't detect user name"
        return out
    last_name = user_name.split(",")[0].strip()
    out["user"] = user_name

    start = today - timedelta(days=past_days)
    end = today + timedelta(days=window_days)
    try:
        events = parse_spring_events(client.fetch_events_json(start, end, cfg.resource_id))
    except Exception as e:
        out["error"] = f"events fetch failed: {e}"
        return out

    bookings = []
    for ev in events:
        # Past (Results) and current (Open) — skip pure Locked since we can't see slots
        if ev.status not in ("Open", "Results"):
            continue
        if "TIMESHEET" not in ev.title.upper():
            continue
        try:
            page = client.get(
                f"/members/bookings/open/event.msp"
                f"?booking_event_id={ev.event_id}&booking_resource_id={ev.resource_id}"
            )
        except Exception:
            continue
        if last_name not in page.text:
            continue
        soup = BeautifulSoup(page.text, "html.parser")
        for row in soup.select(".row-time"):
            text = row.get_text(" ", strip=True)
            if last_name not in text:
                continue
            m = re.search(r"(\d{1,2}:\d{2}\s*[ap]m)", text)
            time_label = m.group(1) if m else "?"
            names: list[str] = []
            for el in row.select("a, span"):
                t = el.get_text(strip=True)
                if t and "," in t and not t.startswith("Book"):
                    clean = t.split(" [")[0].split("-")[0].strip()
                    if clean and clean not in names:
                        names.append(clean)
            bookings.append({
                "date": ev.event_date,
                "time": time_label,
                "event_id": ev.event_id,
                "title": ev.title,
                "partners": names,
            })

    out["bookings"] = bookings
    out["duration_sec"] = (datetime.now() - started_at).total_seconds()
    return out


def save_cache(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


class BookingsFetcher:
    """Background worker that refreshes the bookings cache periodically and on demand."""

    def __init__(self, refresh_seconds: float = 1800.0) -> None:
        self.refresh_seconds = refresh_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._on_update = lambda data: None  # type: ignore
        self._thread: threading.Thread | None = None
        self.last_result: dict | None = load_cache()
        self.in_progress = False

    def set_callback(self, fn) -> None:
        self._on_update = fn

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def request_refresh(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        # Initial fetch shortly after launch (give UI a chance to render first)
        self._wake.wait(timeout=2.0)
        while not self._stop.is_set():
            try:
                self.in_progress = True
                data = fetch_existing_bookings()
                with self._lock:
                    self.last_result = data
                    save_cache(data)
                try:
                    self._on_update(data)
                except Exception:
                    log.exception("bookings on_update callback failed")
            except Exception:
                log.exception("bookings fetch failed")
            finally:
                self.in_progress = False
            self._wake.clear()
            self._wake.wait(timeout=self.refresh_seconds)
