"""In-memory mock state. Resets every restart."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from zoneinfo import ZoneInfo

ADELAIDE = ZoneInfo("Australia/Adelaide")

# Fake fixture credentials — the mock accepts ANY non-empty login (see server.py).
# Never put the real West Beach Parks login here: this file is committed/public.
MOCK_USERNAME = "testmember"
MOCK_PASSWORD = "testpass"
MOCK_MEMBERSHIP_ID = "33661"
MOCK_PERSON_ID = "33660"

DEFAULT_PARTNERS = [
    {"id": "33660", "name": "Test Member One", "golflink": "1001"},
    {"id": "11111", "name": "Partner Two", "golflink": "111"},
    {"id": "22222", "name": "Partner Three", "golflink": "222"},
    {"id": "33333", "name": "Partner Four", "golflink": "333"},
]

EVENT_MONDAY_ID = 12369007
EVENT_WEDNESDAY_ID = 12369010
RESOURCE_ID = 3000000
ROW_ID_BASE = 12486405
SLOT_START_TIME = (7, 6)
SLOT_END_TIME = (9, 30)
SLOT_INTERVAL_MIN = 6


def slot_times() -> list[tuple[int, int]]:
    sh, sm = SLOT_START_TIME
    eh, em = SLOT_END_TIME
    start = sh * 60 + sm
    end = eh * 60 + em
    return [(t // 60, t % 60) for t in range(start, end + 1, SLOT_INTERVAL_MIN)]


@dataclass
class Slot:
    row_id: int
    hour: int
    minute: int
    cells: list[str | None] = field(default_factory=lambda: [None, None, None, None])  # member_id per cell

    def time_label(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d} am"

    @property
    def available_count(self) -> int:
        return sum(1 for c in self.cells if c is None)

    @property
    def is_available(self) -> bool:
        return self.available_count > 0


@dataclass
class Event:
    event_id: int
    title: str
    date_label: str  # e.g. "Tue June 02"
    slots: list[Slot]

    def find_slot(self, hour: int, minute: int) -> Slot | None:
        for s in self.slots:
            if s.hour == hour and s.minute == minute:
                return s
        return None

    def find_slot_by_row_id(self, row_id: int) -> Slot | None:
        for s in self.slots:
            if s.row_id == row_id:
                return s
        return None


def _make_event(event_id: int, title: str, date_label: str) -> Event:
    slots = []
    for i, (h, m) in enumerate(slot_times()):
        slots.append(Slot(row_id=ROW_ID_BASE + i, hour=h, minute=m))
    return Event(event_id=event_id, title=title, date_label=date_label, slots=slots)


@dataclass
class MockState:
    events: dict[int, Event]
    open_at: datetime  # bookings unlock at this time (Adelaide)
    lock: RLock = field(default_factory=RLock)
    request_log: list[dict] = field(default_factory=list)

    def log_request(self, method: str, path: str, payload: dict | None = None) -> None:
        now = datetime.now(ADELAIDE)
        with self.lock:
            self.request_log.append({
                "ts": now.isoformat(timespec="milliseconds"),
                "method": method,
                "path": path,
                "payload_keys": list(payload.keys()) if payload else None,
            })

    def is_open(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(ADELAIDE)
        return now >= self.open_at


def fresh_state(open_at: datetime | None = None) -> MockState:
    if open_at is None:
        open_at = datetime.now(ADELAIDE) + timedelta(seconds=10)
    return MockState(
        events={
            EVENT_MONDAY_ID: _make_event(EVENT_MONDAY_ID, "MONDAY TIMESHEET", "Mon June 01"),
            EVENT_WEDNESDAY_ID: _make_event(EVENT_WEDNESDAY_ID, "WEDNESDAY TIMESHEET", "Wed June 03"),
        },
        open_at=open_at,
    )


state: MockState = fresh_state()
