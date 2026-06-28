"""settings.json schema, defaults, load/save."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

from .paths import SETTINGS_PATH  # noqa: F401  user data dir, NOT the replaceable install dir

PROJECT_ROOT = Path(__file__).parent.parent
# Shipped inside the installer (carries the real login); never committed, never in
# update zips. Used once to seed the user's settings.json on first run.
SEED_PATH = PROJECT_ROOT / "settings.seed.json"

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_WEEKDAY = {name: i for i, name in enumerate(DAY_NAMES)}  # monday=0 .. sunday=6

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 2,
    "club": {
        "name": "West Beach Parks (The Pat)",
        "base_url": "https://golf.westbeachparks.com.au",
        "resource_id": 3000000,
    },
    "credentials": {
        # The REAL login is intentionally NOT in the source. It lives only in the user's
        # local data-dir settings.json, seeded on first install from settings.seed.json
        # (built from .env and shipped inside the private installer — never committed,
        # never included in update zips). See ensure_seeded().
        "username": "",
        "password": "",
    },
    "days": {
        "monday":    {"enabled": True,  "target_time": "08:12"},
        "tuesday":   {"enabled": False, "target_time": "08:12"},
        "wednesday": {"enabled": True,  "target_time": "08:12"},
        "thursday":  {"enabled": False, "target_time": "08:12"},
        "friday":    {"enabled": False, "target_time": "08:12"},
        "saturday":  {"enabled": False, "target_time": "08:12"},
        "sunday":    {"enabled": False, "target_time": "08:12"},
    },
    "booking": {
        "fallback_direction": "earlier",  # "earlier" or "later"
        "fallback_earliest": "07:00",
        "fallback_latest": "09:00",
        "players": 4,
    },
    # Default partners as seen on MiClub's DefaultPartners.msp.
    # Populated on first successful fetch. Each:
    #   {"id": "123", "full_name": "Surname, First", "is_self": true|false,
    #    "playing_days": ["monday", "wednesday", ...]}
    # Booker, on each fire, builds the freeRecord.N payload from only the
    # partners whose playing_days contains the day being booked. Grandpa himself
    # (is_self=true) is always kept regardless.
    "partners": [],
    # One-off bookings the user added via the calendar. Each:
    #   {"play_date": "YYYY-MM-DD", "target_time": "HH:MM", "created_at": "ISO"}
    "one_offs": [],
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_app_password": "",
        "notify_to": "",
    },
    "_internal": {
        "use_mock": False,
        "mock_base_url": "http://127.0.0.1:8000",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Keeps unknown override keys."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_seeded() -> None:
    """First run: if the user has no settings.json yet, seed it from the installer's
    settings.seed.json (which carries the real login, baked into the private installer
    build). No-op if already seeded or there is no seed file. Never raises."""
    try:
        if SETTINGS_PATH.exists() or not SEED_PATH.exists():
            return
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Read settings.json (or fall back to defaults), merged onto defaults so
    missing keys take their default value rather than KeyError-ing."""
    if path is None:
        ensure_seeded()
    p = path or SETTINGS_PATH
    if not p.exists():
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)
    # Normalise a v1 file to v2 BEFORE merging, so every consumer (GUI, scheduler,
    # booker) sees the same shape. Merging first would mask v1 (defaults are v2).
    loaded = _migrate_v1(loaded)
    return _deep_merge(DEFAULT_SETTINGS, loaded)


def save_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    p = path or SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class DayConfig:
    name: str            # "monday" ... "sunday"
    enabled: bool
    target_time: time


@dataclass(frozen=True)
class OneOff:
    play_date: str  # ISO YYYY-MM-DD
    target_time: time
    created_at: str = ""


@dataclass(frozen=True)
class Partner:
    id: str
    full_name: str
    is_self: bool
    playing_days: frozenset[str]  # subset of DAY_NAMES


@dataclass(frozen=True)
class ResolvedConfig:
    """Flat-ish view of settings the booker actually needs at runtime."""

    base_url: str
    resource_id: int
    username: str
    password: str
    days: tuple[DayConfig, ...]  # all 7 days; .enabled tells which are active
    fallback_direction: str
    fallback_earliest: time
    fallback_latest: time
    players: int
    email_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_app_password: str
    notify_to: str
    use_mock: bool
    mock_base_url: str
    one_offs: tuple[OneOff, ...] = ()
    partners: tuple[Partner, ...] = ()

    @property
    def effective_base_url(self) -> str:
        return self.mock_base_url if self.use_mock else self.base_url

    @property
    def play_days(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.days if d.enabled)

    def day(self, name: str) -> DayConfig:
        for d in self.days:
            if d.name == name.lower():
                return d
        raise KeyError(name)

    def target_time_for(self, day_name: str) -> time:
        return self.day(day_name).target_time

    def partner_ids_for_day(self, day_name: str) -> set[str] | None:
        """Return ids of partners playing on the given day (incl. grandpa). Returns
        None when no partner list is configured yet — the booker should then submit
        the raw stock payload without filtering, preserving the old behaviour."""
        if not self.partners:
            return None
        day = day_name.lower()
        return {p.id for p in self.partners if p.is_self or day in p.playing_days}


def _migrate_v1(s: dict[str, Any]) -> dict[str, Any]:
    """Upgrade v1 schema (single target_time + play_days list) to v2 (per-day map)."""
    if s.get("version", 1) >= 2:
        return s
    legacy_target = s.get("booking", {}).get("target_time", "08:12")
    legacy_days = s.get("booking", {}).get("play_days", [])
    days = {}
    for d in DAY_NAMES:
        days[d] = {"enabled": d in legacy_days, "target_time": legacy_target}
    s["days"] = days
    s["version"] = 2
    return s


def resolve(settings: dict[str, Any] | None = None) -> ResolvedConfig:
    s = settings if settings is not None else load_settings()
    s = _migrate_v1(s)

    def _env_override(key: str, current: str) -> str:
        v = os.environ.get(key)
        return v if v is not None and v != "" else current

    use_mock_raw = s["_internal"].get("use_mock", False)
    use_mock = bool(use_mock_raw)
    env_use_mock = os.environ.get("USE_MOCK")
    if env_use_mock is not None:
        use_mock = env_use_mock.strip().lower() in ("1", "true", "yes", "y", "on")

    days = tuple(
        DayConfig(name=d, enabled=bool(s["days"][d]["enabled"]),
                  target_time=parse_hhmm(s["days"][d]["target_time"]))
        for d in DAY_NAMES
    )

    one_offs: list[OneOff] = []
    for oo in s.get("one_offs", []):
        try:
            one_offs.append(OneOff(
                play_date=str(oo["play_date"]),
                target_time=parse_hhmm(oo["target_time"]),
                created_at=str(oo.get("created_at", "")),
            ))
        except (KeyError, ValueError):
            continue

    partners: list[Partner] = []
    all_days_frozen = frozenset(DAY_NAMES)
    for p in s.get("partners", []):
        try:
            # Migration: v32 format had "enabled" boolean. True → plays all 7 days,
            # False → plays no days. New format uses playing_days directly.
            if "playing_days" in p:
                days = frozenset(d for d in p["playing_days"] if d in DAY_NAMES)
            elif "enabled" in p:
                days = all_days_frozen if bool(p["enabled"]) else frozenset()
            else:
                days = all_days_frozen
            is_self = bool(p.get("is_self", False))
            if is_self:
                days = all_days_frozen  # grandpa always plays
            partners.append(Partner(
                id=str(p["id"]),
                full_name=str(p["full_name"]),
                is_self=is_self,
                playing_days=days,
            ))
        except (KeyError, TypeError):
            continue

    return ResolvedConfig(
        base_url=_env_override("WBP_BASE_URL", s["club"]["base_url"]),
        resource_id=int(s["club"]["resource_id"]),
        username=_env_override("WBP_USERNAME", s["credentials"]["username"]),
        password=_env_override("WBP_PASSWORD", s["credentials"]["password"]),
        days=days,
        fallback_direction=s["booking"]["fallback_direction"],
        fallback_earliest=parse_hhmm(s["booking"]["fallback_earliest"]),
        fallback_latest=parse_hhmm(s["booking"]["fallback_latest"]),
        players=int(s["booking"]["players"]),
        email_enabled=bool(s["email"]["enabled"]),
        smtp_host=s["email"]["smtp_host"],
        smtp_port=int(s["email"]["smtp_port"]),
        smtp_user=_env_override("SMTP_USER", s["email"]["smtp_user"]),
        smtp_app_password=_env_override("SMTP_APP_PASSWORD", s["email"]["smtp_app_password"]),
        notify_to=_env_override("NOTIFY_TO", s["email"]["notify_to"]),
        use_mock=use_mock,
        mock_base_url=_env_override("MOCK_BASE_URL", s["_internal"]["mock_base_url"]),
        one_offs=tuple(one_offs),
        partners=tuple(partners),
    )


def play_weekday(day_name: str) -> int:
    return DAY_WEEKDAY[day_name.lower()]


def fire_weekday(play_day_name: str) -> int:
    """The day-of-week (0=Mon..6=Sun) that the booker should fire on for a given play day.
    Bookings open 15 days before play, so fire = (play - 1) mod 7."""
    return (play_weekday(play_day_name) - 1) % 7
