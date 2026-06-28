"""Read-only recon against the real (or mock) site. By design, never touches MakeBooking.msp.

Usage:
    python -m booker.recon
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .client import MiClubClient
from .config import ADELAIDE, load_config
from .parser import parse_event_list, parse_event_page, parse_make_booking_form

# Hard guard: even if some bug routes a request here, refuse.
_FORBIDDEN_PATH_FRAGMENTS = ("MakeBooking", "makebooking")

log = logging.getLogger(__name__)


class ReadOnlyClient(MiClubClient):
    """Wraps MiClubClient and raises if any request path mentions MakeBooking."""

    def _check(self, path: str) -> None:
        for frag in _FORBIDDEN_PATH_FRAGMENTS:
            if frag in path:
                raise RuntimeError(f"recon refuses to call {path}")

    def get(self, path: str, **kwargs):  # type: ignore[override]
        self._check(path)
        return super().get(path, **kwargs)

    def post(self, path: str, data=None, **kwargs):  # type: ignore[override]
        self._check(path)
        return super().post(path, data, **kwargs)

    def fire_get(self, path, params):  # type: ignore[override]
        raise RuntimeError("recon disabled fire_get entirely")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = load_config()
    log.info("RECON MODE — read-only. base_url=%s use_mock=%s", cfg.effective_base_url, cfg.use_mock)
    log.info("This script will NOT call MakeBooking.msp under any circumstance.")

    client = ReadOnlyClient(cfg.effective_base_url, cfg.username, cfg.password)
    if not client.login():
        log.error("login failed; aborting")
        return
    log.info("login OK")

    # The real site renders eventList via React, so we call the JSON API directly.
    today_d = datetime.now(ADELAIDE).date()
    end_d = today_d + timedelta(days=15)
    log.info("calling Spring events API for %s..%s", today_d, end_d)
    try:
        payload = client.fetch_events_json(today_d, end_d, cfg.resource_id)
    except Exception as e:
        log.error("Spring API call failed: %s", e)
        payload = []
    from .parser import parse_spring_events
    json_events = parse_spring_events(payload)
    log.info("parsed %d events from Spring API", len(json_events))
    for e in json_events[:30]:
        log.info("  %s status=%-7s id=%-9d %r", e.event_date, e.status, e.event_id, e.title[:40])
    # Build a list of EventLink for downstream code reuse
    events = [e.to_event_link() for e in json_events]

    # Mon plays unlock Sundays 19:00 (15 days prior). Wed plays unlock Tuesdays 19:00.
    # For the recon, target the next upcoming Mon and Wed plays from the next Sun/Tue 19:00.
    now = datetime.now(ADELAIDE)
    days_to_sun = (6 - now.weekday()) % 7
    days_to_tue = (1 - now.weekday()) % 7
    target_mon = ((now + timedelta(days=days_to_sun)) + timedelta(days=15)).date()
    target_wed = ((now + timedelta(days=days_to_tue)) + timedelta(days=15)).date()
    log.info("targeting Mon=%s Wed=%s", target_mon, target_wed)
    mon_iso, wed_iso = target_mon.isoformat(), target_wed.isoformat()

    targets: list[tuple[str, object]] = []
    for je in json_events:
        if je.event_date == mon_iso and "MONDAY" in je.title.upper() and "TIMESHEET" in je.title.upper():
            if not any(lbl == "Monday" for lbl, _ in targets):
                targets.append(("Monday", je.to_event_link()))
        elif je.event_date == wed_iso and "WEDNESDAY" in je.title.upper() and "TIMESHEET" in je.title.upper():
            if not any(lbl == "Wednesday" for lbl, _ in targets):
                targets.append(("Wednesday", je.to_event_link()))

    for label, ev in targets:
        log.info("--- [%s] event %s ---", label, ev.event_id)  # type: ignore[attr-defined]
        path = f"/members/bookings/open/event.msp?booking_event_id={ev.event_id}&booking_resource_id={ev.resource_id}"  # type: ignore[attr-defined]
        r = client.get(path)
        log.info("[%s] event page status=%s size=%d", label, r.status_code, len(r.text))
        slots = parse_event_page(r.text)
        log.info("[%s] parsed %d slot rows", label, len(slots))
        # Show the band 8:00 to 9:00
        for s in slots:
            if 8 <= s.hour <= 9 and (s.hour, s.minute) <= (9, 0):
                log.info("  row_id=%-8d %02d:%02d available=%s count=%d",
                         s.row_id, s.hour, s.minute, s.is_available, s.available_count)

        # Probe the partner-picker URL with the 8:12 row_id IF found.
        target_slot = next((s for s in slots if s.hour == 8 and s.minute == 12), None)
        if target_slot is None:
            log.warning("[%s] no 8:12 slot found", label)
            continue
        if not target_slot.is_available:
            log.info("[%s] 8:12 not 'available' — skipping partner probe", label)
            continue
        dp_path = f"/members/bookings/open/DefaultPartners.msp?booking_event_id={ev.event_id}&booking_row_id={target_slot.row_id}&hasMultipleFees=false"  # type: ignore[attr-defined]
        r = client.get(dp_path)
        log.info("[%s] partner picker status=%s size=%d", label, r.status_code, len(r.text))
        fields = parse_make_booking_form(r.text)
        log.info("[%s] partner form has %d distinct field name(s):", label, len(fields))
        for name in list(fields.keys())[:30]:
            log.info("    %s (×%d)", name, len(fields[name]))

    log.info("recon complete — NO bookings made")


if __name__ == "__main__":
    main()
