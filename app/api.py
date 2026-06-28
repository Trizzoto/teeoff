"""JSON API bridge for the web UI.

The web frontend (app/webui/) calls these routes via fetch('/api/<route>'). Each
route maps to the existing Python logic (scheduler/settings/bookings/updater) — the
same code the old GUI used. The actual booking still runs headless via booker/.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from . import updater
from .bookings import fetch_existing_bookings, load_cache, save_cache
from .paths import LAST_RUN_PATH, LOGS_DIR
from .planning import calendar_days, recent_runs, upcoming_fires
from .scheduler import get_info, register, set_paused
from .settings import DAY_NAMES, load_settings, save_settings
from .version import __version__


def dispatch(method: str, route: str, payload: dict[str, Any]) -> Any:
    handler = _ROUTES.get(route)
    if handler is None:
        raise ValueError(f"unknown api route: {route!r}")
    return handler(payload or {})


# --- helpers ---------------------------------------------------------------

def _status(info: dict | None = None) -> dict:
    info = info if info is not None else get_info()
    return {
        "registered": bool(info.get("registered")),
        "state": info.get("State"),
        "paused": str(info.get("State", "")).lower() == "disabled",
        "triggers": len(info.get("Triggers", []) or []),
        "next_run": info.get("NextRunTime"),
        "error": info.get("error"),
    }


def _last_run() -> dict | None:
    try:
        if LAST_RUN_PATH.exists():
            return json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# --- read routes -----------------------------------------------------------

def _ping(_p: dict) -> dict:
    return {"ok": True, "app": "TeeOff", "version": __version__}


def _dashboard(_p: dict) -> dict:
    s = load_settings()
    cache = load_cache() or {}
    live = cache.get("bookings", []) or []
    return {
        "version": __version__,
        "status": _status(),
        "last_run": _last_run(),
        "email_enabled": bool(s.get("email", {}).get("enabled")),
        "upcoming": upcoming_fires(s),
        "bookings_synced": ({
            "at": cache.get("fetched_at"),
            "count": len(live),
            "user": cache.get("user"),
            "error": cache.get("error"),
        } if cache else None),
    }


def _calendar(_p: dict) -> dict:
    s = load_settings()
    cache = load_cache() or {}
    return calendar_days(s, cache.get("bookings", []) or [])


def _settings_get(_p: dict) -> dict:
    s = load_settings()
    # Never send the password to the UI; just whether one is set.
    return {
        "days": s.get("days", {}),
        "club": {"name": s.get("club", {}).get("name", "")},
        "credentials": {"username": s.get("credentials", {}).get("username", ""),
                        "has_password": bool(s.get("credentials", {}).get("password"))},
        "email": {k: v for k, v in s.get("email", {}).items() if k != "smtp_app_password"}
                 | {"has_app_password": bool(s.get("email", {}).get("smtp_app_password"))},
        "partners": s.get("partners", []),
        "one_offs": s.get("one_offs", []),
        "booking": s.get("booking", {}),
    }


# --- action routes ---------------------------------------------------------

def _save_days(payload: dict) -> dict:
    """payload: {"days": {monday: {enabled, target_time}, ...}}. Saves + re-registers."""
    s = copy.deepcopy(load_settings())
    incoming = payload.get("days", {})
    for d in DAY_NAMES:
        if d in incoming:
            cur = s["days"].setdefault(d, {})
            if "enabled" in incoming[d]:
                cur["enabled"] = bool(incoming[d]["enabled"])
            if "target_time" in incoming[d]:
                cur["target_time"] = str(incoming[d]["target_time"])
    save_settings(s)
    ok, msg = register(s)
    return {"ok": ok, "message": msg, "status": _status(), "upcoming": upcoming_fires(s)}


def _set_paused(payload: dict) -> dict:
    ok, msg = set_paused(bool(payload.get("paused")))
    return {"ok": ok, "message": msg, "status": _status()}


def _refresh_bookings(_p: dict) -> dict:
    """Blocking on-site bookings fetch (login + scrape). Persists + returns the data."""
    data = fetch_existing_bookings()
    live = data.get("bookings", []) or []
    if "error" not in data:
        save_cache(data)  # persist so the dashboard/calendar read the fresh bookings
    s = load_settings()
    return {
        "ok": "error" not in data,
        "error": data.get("error"),
        "bookings_synced": {"at": data.get("fetched_at"), "count": len(live), "user": data.get("user")},
        "calendar": calendar_days(s, live),
    }


def _check_update(_p: dict) -> dict:
    info = updater.check_for_update()
    if info is None:
        return {"update": None, "version": __version__}
    return {"version": __version__, "update": {"version": info.version, "notes": info.notes}}


def _apply_update(_p: dict) -> dict:
    info = updater.check_for_update()
    if info is None:
        return {"ok": False, "error": "You're already up to date."}
    try:
        staging = updater.download_and_stage(info)  # download + sha256 verify + extract
    except Exception as e:
        return {"ok": False, "error": f"Download failed: {e}"}
    updater.apply_and_restart(staging)  # spawn the detached applier (swaps + relaunches)
    # Close the window shortly after replying, so the applier can swap files + reopen.
    from . import _runtime
    threading.Timer(0.8, _runtime.request_quit).start()
    return {"ok": True, "version": info.version}


def _save_account(payload: dict) -> dict:
    s = copy.deepcopy(load_settings())
    cred = s.setdefault("credentials", {})
    if "username" in payload:
        cred["username"] = str(payload["username"]).strip()
    if payload.get("password"):  # only change when a non-empty value is supplied
        cred["password"] = str(payload["password"])
    save_settings(s)
    return {"ok": True}


def _save_email(payload: dict) -> dict:
    s = copy.deepcopy(load_settings())
    em = s.setdefault("email", {})
    if "enabled" in payload:
        em["enabled"] = bool(payload["enabled"])
    if "smtp_user" in payload:
        em["smtp_user"] = str(payload["smtp_user"]).strip()
    if "notify_to" in payload:
        em["notify_to"] = str(payload["notify_to"]).strip()
    if payload.get("smtp_app_password"):
        em["smtp_app_password"] = str(payload["smtp_app_password"])
    save_settings(s)
    return {"ok": True, "email_enabled": bool(em.get("enabled"))}


def _send_test_email(_p: dict) -> dict:
    from booker.config import load_config
    from booker.notifier import send_summary
    cfg = load_config()
    if not cfg.smtp_user or not cfg.smtp_app_password or not cfg.notify_to:
        return {"ok": False, "error": "Email isn't fully set up yet — fill in all the email fields and save first."}
    ok = send_summary(cfg, "TeeOff test email",
                      "This is a test from TeeOff. If you received this, email alerts are working.")
    return {"ok": ok, "to": cfg.notify_to,
            "error": None if ok else "Send failed — check the address and app password."}


def _refresh_partners(_p: dict) -> dict:
    from .partners import fetch_partner_list, merge_into_settings
    fresh = fetch_partner_list()
    if fresh is None:
        return {"ok": False, "error": "Couldn't reach the golf site — check the login and connection."}
    s = copy.deepcopy(load_settings())
    merge_into_settings(s, fresh)
    save_settings(s)
    return {"ok": True, "partners": s.get("partners", [])}


def _save_partners(payload: dict) -> dict:
    s = copy.deepcopy(load_settings())
    incoming = {str(p.get("id")): p for p in payload.get("partners", [])}
    for p in s.get("partners", []):
        pid = str(p.get("id"))
        if pid in incoming and not p.get("is_self"):
            pd = incoming[pid].get("playing_days", [])
            p["playing_days"] = [d for d in pd if d in DAY_NAMES]
    save_settings(s)
    return {"ok": True}


def _activity(_p: dict) -> dict:
    return {"runs": recent_runs(), "last_run": _last_run()}


def _booker_log(_p: dict) -> dict:
    """The rolling booker.log (tail), shown in-app on the Activity page."""
    p = LOGS_DIR / "booker.log"
    try:
        if p.exists():
            return {"text": p.read_text(encoding="utf-8", errors="replace")[-24000:]}
    except Exception as e:
        return {"text": "", "error": str(e)}
    return {"text": ""}


_ROUTES = {
    "ping": _ping,
    "dashboard": _dashboard,
    "calendar": _calendar,
    "settings": _settings_get,
    "save_days": _save_days,
    "set_paused": _set_paused,
    "refresh_bookings": _refresh_bookings,
    "check_update": _check_update,
    "apply_update": _apply_update,
    "save_account": _save_account,
    "save_email": _save_email,
    "send_test_email": _send_test_email,
    "refresh_partners": _refresh_partners,
    "save_partners": _save_partners,
    "activity": _activity,
    "booker_log": _booker_log,
}
