"""Static constants. Runtime config lives in app/settings.py via resolve()."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# .env still loaded for dev convenience (env vars override settings.json values via app/settings.resolve)
load_dotenv(Path(__file__).parent.parent / ".env")

from app.settings import ResolvedConfig, resolve  # noqa: E402

ADELAIDE = ZoneInfo("Australia/Adelaide")
SLOT_INTERVAL_MIN = 6
FIRE_HOUR_LOCAL = 19  # 7pm Adelaide — daily TIMESHEET unlocks 15 days before play at 19:00:03.5xx
FIRE_MINUTE_LOCAL = 0
FIRE_SECOND_LOCAL = 3
FIRE_MICROSECOND_LOCAL = 500_000
POLL_INTERVAL_SECONDS = 0.25

# Type alias preserved for back-compat
Config = ResolvedConfig


def load_config() -> ResolvedConfig:
    return resolve()
