"""HTML parsing for MiClub pages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


@dataclass
class EventLink:
    title: str
    event_id: int
    resource_id: int
    href: str


@dataclass
class SlotRow:
    row_id: int
    hour: int
    minute: int
    is_available: bool
    available_count: int  # how many of the 4 cells are free

    @property
    def slot_time(self) -> time:
        return time(self.hour, self.minute)


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)?", re.I)


def parse_event_list(html: str) -> list[EventLink]:
    """Legacy HTML parser. Kept for the mock's HTML eventList; real site uses the JSON API."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[EventLink] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "event.msp" not in href:
            continue
        q = parse_qs(urlparse(href).query)
        eid = q.get("booking_event_id", [None])[0]
        rid = q.get("booking_resource_id", [None])[0]
        if not eid or not rid:
            continue
        title = (a.get_text() or "").strip()
        out.append(EventLink(title=title, event_id=int(eid), resource_id=int(rid), href=href))
    seen, uniq = set(), []
    for e in out:
        if e.event_id in seen:
            continue
        seen.add(e.event_id)
        uniq.append(e)
    return uniq


@dataclass
class JsonEvent:
    event_id: int
    title: str
    event_date: str  # ISO YYYY-MM-DD
    status: str  # "Open", "Locked", ...
    resource_id: int
    open_time_ms: int | None  # epoch ms when bookings unlock (None if already open)

    def to_event_link(self) -> EventLink:
        return EventLink(
            title=self.title,
            event_id=self.event_id,
            resource_id=self.resource_id,
            href=f"/members/bookings/open/event.msp?booking_event_id={self.event_id}&booking_resource_id={self.resource_id}",
        )


def parse_spring_events(payload: list[dict]) -> list[JsonEvent]:
    out: list[JsonEvent] = []
    for e in payload:
        try:
            ot = e.get("openTime")
            open_time_ms = int(ot) if isinstance(ot, (int, float)) and ot else None
            out.append(JsonEvent(
                event_id=int(e["bookingEventId"]),
                title=str(e.get("title", "")),
                event_date=str(e.get("eventDate", "")),
                status=str(e.get("eventStatusCodeFriendly", "")),
                resource_id=int(e.get("bookingResourceId", 0)),
                open_time_ms=open_time_ms,
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out


_ROW_ID_RE = re.compile(r"^row-(\d+)$")


def parse_event_page(html: str) -> list[SlotRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[SlotRow] = []
    for div in soup.select(".row-time"):
        classes = set(div.get("class") or [])
        # Real-site booking_row_id is in id="row-12486416". Fallback: 'time-N' class
        # in the mock template uses the same numeric (deterministic per slot).
        row_id: int | None = None
        m_id = _ROW_ID_RE.match(div.get("id", "") or "")
        if m_id:
            row_id = int(m_id.group(1))
        else:
            for c in classes:
                if c.startswith("time-"):
                    try:
                        row_id = int(c[5:])
                    except ValueError:
                        pass
        if row_id is None:
            continue
        text = div.get_text(" ", strip=True)
        m = _TIME_RE.search(text)
        if not m:
            continue
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        is_available = "available" in classes
        # Best-effort cell count: BOOKED labels (mock) or named members (real)
        booked = sum(1 for s in div.stripped_strings if s.upper() == "BOOKED")
        available_count = max(0, 4 - booked)
        rows.append(SlotRow(row_id=row_id, hour=hour, minute=minute, is_available=is_available, available_count=available_count))
    rows.sort(key=lambda r: (r.hour, r.minute))
    return rows


def parse_make_booking_form(html: str) -> dict[str, list[str]]:
    """Extract hidden field name/value pairs from the MakeBooking form on DefaultPartners.msp.

    Returns dict where values are lists (because freeRecord.id appears multiple times).
    """
    soup = BeautifulSoup(html, "html.parser")
    form = None
    for f in soup.find_all("form"):
        action = f.get("action", "")
        if "MakeBooking" in action:
            form = f
            break
    if form is None:
        return {}
    fields: dict[str, list[str]] = {}
    for inp in form.find_all(["input", "select", "textarea"]):
        name = inp.get("name")
        if not name:
            continue
        value = inp.get("value", "")
        fields.setdefault(name, []).append(value)
    return fields


_PARTNER_FIELD = re.compile(r"^freeRecord\.(\d+)\.(.+)$")


def parse_partner_list(html: str) -> list[dict]:
    """Return [{"id", "full_name", "is_self", "idx"}, ...] from DefaultPartners.msp.

    freeRecord.0 is the logged-in member (grandpa); higher indices are his saved
    default partners. The MiClub identifier field is `membership_number` (with
    `id` accepted as a fallback for the mock server). Only records that carry
    both an id and a full_name are returned.
    """
    fields = parse_make_booking_form(html)
    by_idx: dict[int, dict[str, str]] = {}
    for name, values in fields.items():
        m = _PARTNER_FIELD.match(name)
        if not m:
            continue
        idx = int(m.group(1))
        sub = m.group(2)
        by_idx.setdefault(idx, {})[sub] = values[0] if values else ""
    out = []
    for idx in sorted(by_idx.keys()):
        rec = by_idx[idx]
        pid = (rec.get("membership_number") or rec.get("id") or "").strip()
        name = (rec.get("full_name") or "").strip()
        if pid and name:
            out.append({"id": pid, "full_name": name, "is_self": idx == 0, "idx": idx})
    return out
