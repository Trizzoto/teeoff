"""Precise wait until a target datetime."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from .config import ADELAIDE, FIRE_HOUR_LOCAL, FIRE_MINUTE_LOCAL


def next_fire_time(now: datetime | None = None) -> datetime:
    """Return the next Sunday 19:00:00 Adelaide >= now."""
    if now is None:
        now = datetime.now(ADELAIDE)
    today_fire = now.replace(hour=FIRE_HOUR_LOCAL, minute=FIRE_MINUTE_LOCAL, second=0, microsecond=0)
    # Sunday is weekday 6
    days_until_sunday = (6 - now.weekday()) % 7
    candidate = today_fire + timedelta(days=days_until_sunday)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def wait_until(target: datetime, *, sleep_until_lead_seconds: float = 0.5) -> float:
    """Sleep until ~target, then spin-wait the last bit. Returns actual fire time epoch (seconds)."""
    target_epoch = target.timestamp()
    # Coarse sleep
    while True:
        now_epoch = time.time()
        delta = target_epoch - now_epoch
        if delta <= sleep_until_lead_seconds:
            break
        time.sleep(min(delta - sleep_until_lead_seconds, 5.0))
    # Spin-wait the last bit
    while time.time() < target_epoch:
        pass
    return time.time()
