"""Fetch grandpa's MiClub default-partner list and reconcile it with settings.

Used by the Schedule tab so grandpa can tick/untick which of his usual partners
get added to each booking — without ever needing to touch the MiClub website.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Callable

from booker.client import MiClubClient
from booker.parser import parse_event_page, parse_partner_list, parse_spring_events

from .settings import DAY_NAMES, load_settings, resolve

log = logging.getLogger(__name__)


def fetch_partner_list() -> list[dict] | None:
    """Return [{"id", "full_name", "is_self"}, ...] from MiClub, or None on failure.

    Strategy: log in, find any Open TIMESHEET event in the next two weeks, hit
    its DefaultPartners.msp and lift the freeRecord.N entries from the booking
    form.
    """
    cfg = resolve(load_settings())
    if not cfg.username or not cfg.password:
        log.info("partner fetch: no credentials configured")
        return None

    client = MiClubClient(cfg.effective_base_url, cfg.username, cfg.password)
    if not client.login():
        log.warning("partner fetch: login failed")
        return None

    today = date.today()
    end = today + timedelta(days=14)
    try:
        events = parse_spring_events(client.fetch_events_json(today, end, cfg.resource_id))
    except Exception as e:
        log.warning("partner fetch: spring events failed: %s", e)
        return None

    for ev in events:
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
            partners_page = client.get(
                f"/members/bookings/open/DefaultPartners.msp"
                f"?booking_event_id={ev.event_id}&booking_row_id={any_row.row_id}"
                f"&hasMultipleFees=false"
            )
            records = parse_partner_list(partners_page.text)
            if records:
                return [
                    {"id": r["id"], "full_name": r["full_name"], "is_self": r["is_self"]}
                    for r in records
                ]
        except Exception as e:
            log.warning("partner fetch: event %s failed: %s", ev.event_id, e)
            continue
    return None


def merge_into_settings(settings: dict, fresh: list[dict]) -> bool:
    """Mutate settings["partners"] in place: keep existing playing_days selections,
    add new partners (default = playing on all 7 days), drop entries MiClub no
    longer returns. Grandpa always plays every day.

    Returns True if anything changed.
    """
    existing = {p.get("id"): p for p in settings.get("partners", [])}
    new_list: list[dict] = []
    for f in fresh:
        prev = existing.get(f["id"])
        if f["is_self"]:
            playing_days = list(DAY_NAMES)
        elif prev is not None and "playing_days" in prev:
            playing_days = [d for d in prev["playing_days"] if d in DAY_NAMES]
        elif prev is not None and "enabled" in prev:
            # v32 → v33 migration: old single boolean expands to all days / none
            playing_days = list(DAY_NAMES) if bool(prev["enabled"]) else []
        else:
            playing_days = list(DAY_NAMES)
        new_list.append({
            "id": f["id"],
            "full_name": f["full_name"],
            "is_self": bool(f["is_self"]),
            "playing_days": playing_days,
        })
    changed = new_list != settings.get("partners", [])
    settings["partners"] = new_list
    return changed


class PartnerFetcher:
    """One-shot, on-demand partner fetcher run on a background thread.

    Used by the Schedule tab: kicked once on app startup (if no partners cached)
    and again when the user clicks "Refresh partner list".
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_progress = False
        self._on_done: Callable[[list[dict] | None, str | None], None] | None = None

    @property
    def in_progress(self) -> bool:
        return self._in_progress

    def fetch(self, on_done: Callable[[list[dict] | None, str | None], None]) -> bool:
        """Start a fetch. on_done(fresh_list, error_msg) is called on a worker thread.
        Returns False if a fetch is already running."""
        with self._lock:
            if self._in_progress:
                return False
            self._in_progress = True
            self._on_done = on_done

        def _runner():
            err: str | None = None
            fresh: list[dict] | None = None
            try:
                fresh = fetch_partner_list()
                if fresh is None:
                    err = "Could not reach MiClub (check credentials and connection)."
            except Exception as e:
                log.exception("partner fetch crashed")
                err = str(e)
            finally:
                self._in_progress = False
                cb = self._on_done
                self._on_done = None
                if cb is not None:
                    try:
                        cb(fresh, err)
                    except Exception:
                        log.exception("partner fetch on_done callback failed")

        threading.Thread(target=_runner, daemon=True).start()
        return True
