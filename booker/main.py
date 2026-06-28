"""Orchestrator: pre-warm a MiClub session, then fire MakeBooking.msp at exactly
19:00:03.5xx ACST for a chosen TIMESHEET event 15 days out.

The 'target' argument names a day-of-week to book (monday..sunday). Bookings
unlock 15 days before the play day at 19:00:03.5xx ACST, so the booker is
launched on the day BEFORE the same-named day of the prior week
(e.g. play_day=monday → fire on Sunday at 18:55).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from app.settings import DAY_NAMES, ResolvedConfig, fire_weekday, play_weekday, resolve
from app.paths import LAST_RUN_PATH, LOGS_DIR, ensure_dirs

from .client import MiClubClient
from .config import (
    ADELAIDE,
    FIRE_HOUR_LOCAL,
    FIRE_MICROSECOND_LOCAL,
    FIRE_MINUTE_LOCAL,
    FIRE_SECOND_LOCAL,
    POLL_INTERVAL_SECONDS,
    SLOT_INTERVAL_MIN,
    load_config,
)
from .notifier import send_summary
from .parser import EventLink, SlotRow, parse_event_list, parse_event_page, parse_make_booking_form, parse_spring_events
from .timing import wait_until

log = logging.getLogger(__name__)


def _title_friendly(day_name: str) -> str:
    """'monday' → 'Monday'."""
    return day_name.capitalize()


@dataclass
class PreparedBooking:
    target_label: str  # "Monday", "Wednesday", etc.
    target_date: str   # ISO date YYYY-MM-DD
    event: EventLink
    candidate_slots: list[SlotRow] = field(default_factory=list)
    payloads: dict[int, dict[str, list[str]]] = field(default_factory=dict)
    deferred: bool = False
    stock_payload: dict[str, list[str]] | None = None
    event_title_for_payload: str = ""


@dataclass
class FireOutcome:
    label: str
    success: bool
    booked_slot: dtime | None
    attempts: list[str]
    fire_epoch_ms: int


def _marker_path(label: str, target_date: str) -> Path:
    ensure_dirs()
    return LOGS_DIR / f"booked-{target_date}-{label.lower()}.flag"


def _candidate_times(cfg: ResolvedConfig, target_time: dtime) -> list[dtime]:
    """Return slot times in firing-priority order: target first, then walks earlier or later."""
    target_min = target_time.hour * 60 + target_time.minute
    if cfg.fallback_direction == "later":
        end_min = cfg.fallback_latest.hour * 60 + cfg.fallback_latest.minute
        step = SLOT_INTERVAL_MIN
        cond = lambda t: t <= end_min  # noqa: E731
    else:  # default "earlier"
        end_min = cfg.fallback_earliest.hour * 60 + cfg.fallback_earliest.minute
        step = -SLOT_INTERVAL_MIN
        cond = lambda t: t >= end_min  # noqa: E731
    out = []
    t = target_min
    while cond(t):
        out.append(dtime(t // 60, t % 60))
        t += step
    return out


def target_date_for(fire_time: datetime, play_day: str) -> str:
    """Return ISO date of the next-play-day-X that this fire will be racing for.

    Production rule: fire at 19:00 on weekday (X-1), books play_day weekday X exactly
    15 days later. For test fires from arbitrary weekdays, anchor on the NEXT
    fire-weekday from fire_time and add 15 days.
    """
    fire_wd = fire_weekday(play_day)
    days_to_anchor = (fire_wd - fire_time.weekday()) % 7
    anchor = fire_time + timedelta(days=days_to_anchor)
    return (anchor + timedelta(days=15)).date().isoformat()


def production_fire_time(now: datetime) -> datetime:
    """For production runs (Task Scheduler launches us at 18:55), fire = today 19:00:03.500 ACST.
    The server actually unlocks at 19:00:03.6-9xx — we fire ~1.5s early after openTime retargeting
    so the poll loop catches the unlock cleanly. If launched late, fire as soon as possible."""
    today_fire = now.replace(
        hour=FIRE_HOUR_LOCAL, minute=FIRE_MINUTE_LOCAL,
        second=FIRE_SECOND_LOCAL, microsecond=FIRE_MICROSECOND_LOCAL,
    )
    if now > today_fire + timedelta(seconds=5):
        log.warning("launched after %s — firing in 5s as catch-up", today_fire.isoformat(timespec="milliseconds"))
        return now + timedelta(seconds=5)
    return today_fire


def infer_target_from_weekday(fire_time: datetime, play_days: tuple[str, ...]) -> str:
    """Find which configured play_day uses fire_time's weekday as its fire day."""
    wd = fire_time.weekday()
    for d in play_days:
        if fire_weekday(d) == wd:
            return d
    raise SystemExit(
        f"--target not specified and fire weekday is {fire_time.strftime('%A')} "
        f"but no configured play day uses that fire-weekday (configured: {list(play_days)})"
    )


def find_one_off_for_today(cfg, fire_time: datetime) -> dict | None:
    """If a one-off booking's fire date is today, return target info from it.
    Returns {"target": "wednesday", "target_date": "2026-06-18", "target_time_hhmm": "07:36"} or None."""
    today = fire_time.date()
    for oo in cfg.one_offs:
        try:
            play_date = datetime.strptime(oo.play_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if play_date - timedelta(days=15) == today:
            day_name = DAY_NAMES[play_date.weekday()]
            return {
                "target": day_name,
                "target_date": oo.play_date,
                "target_time_hhmm": oo.target_time.strftime("%H:%M"),
                "play_date": play_date,
            }
    return None


def _pick_target_event(client: MiClubClient, fire_time: datetime, play_day: str, cfg: ResolvedConfig, target_date_override: str | None = None) -> tuple[EventLink | None, int | None]:
    title_match = play_day.upper()
    target_iso = target_date_override or target_date_for(fire_time, play_day)
    label = _title_friendly(play_day)
    log.info("[%s] target_date=%s", label, target_iso)

    if not cfg.use_mock:
        start = fire_time.date()
        end = start + timedelta(days=20)
        try:
            payload = client.fetch_events_json(start, end, cfg.resource_id)
        except Exception as e:
            log.exception("Spring events API failed; falling back to HTML parse: %s", e)
            payload = None
        if payload is not None:
            spring = parse_spring_events(payload)
            log.info("Spring API returned %d events", len(spring))
            for ev in spring:
                if ev.event_date == target_iso and title_match in ev.title.upper() and "TIMESHEET" in ev.title.upper():
                    return ev.to_event_link(), ev.open_time_ms
            return None, None

    resp = client.get("/views/members/booking/eventList.xhtml")
    events = parse_event_list(resp.text)
    for e in events:
        if title_match in e.title.upper() and "TIMESHEET" in e.title.upper():
            return e, None
    return None, None


_FREE_RECORD_FIELD = re.compile(r"^freeRecord\.(\d+)\.(.+)$")


def _apply_partner_filter(fields: dict[str, list[str]], enabled_ids: set[str] | None) -> dict[str, list[str]]:
    """If enabled_ids is given, drop freeRecord.N entries whose .id isn't in the set
    and re-number the remaining entries from 0. freeRecord.0 (grandpa himself)
    is always preserved regardless of the set.

    enabled_ids=None means "no partner list configured" — fields pass through
    unchanged, preserving the old behaviour.
    """
    if enabled_ids is None:
        return {k: list(v) for k, v in fields.items()}

    out: dict[str, list[str]] = {}
    groups: dict[int, dict[str, str]] = {}
    for name, values in fields.items():
        m = _FREE_RECORD_FIELD.match(name)
        if m:
            idx = int(m.group(1))
            sub = m.group(2)
            groups.setdefault(idx, {})[sub] = values[0] if values else ""
        else:
            out[name] = list(values)

    kept: list[dict[str, str]] = []
    for idx in sorted(groups.keys()):
        rec = groups[idx]
        rec_id = rec.get("membership_number") or rec.get("id") or ""
        if idx == 0 or rec_id in enabled_ids:
            kept.append(rec)

    for new_idx, rec in enumerate(kept):
        for sub, val in rec.items():
            out[f"freeRecord.{new_idx}.{sub}"] = [val]
    return out


def _build_payload_for_row(payload_fields: dict[str, list[str]], row_id: int,
                           enabled_partner_ids: set[str] | None = None) -> dict[str, list[str]]:
    p = _apply_partner_filter(payload_fields, enabled_partner_ids)
    p["booking_row_id"] = [str(row_id)]
    return p


def _refresh_primary_payload(client: MiClubClient, p: PreparedBooking, cfg: ResolvedConfig) -> None:
    if p.deferred or not p.candidate_slots:
        return
    row = p.candidate_slots[0]
    url = (
        f"/members/bookings/open/DefaultPartners.msp"
        f"?booking_event_id={p.event.event_id}"
        f"&booking_row_id={row.row_id}"
        f"&hasMultipleFees=false"
    )
    try:
        resp = client.get(url, timeout=2.0)
        fields = parse_make_booking_form(resp.text)
        if fields:
            p.payloads[row.row_id] = _build_payload_for_row(fields, row.row_id, cfg.partner_ids_for_day(p.target_label))
            log.info("[%s] refreshed primary payload (%02d:%02d, %d fields)", p.target_label, row.hour, row.minute, len(fields))
        else:
            log.warning("[%s] refresh got no fields, keeping old payload", p.target_label)
    except Exception as e:
        log.warning("[%s] refresh failed (%s) — keeping old payload", p.target_label, e)


def _prepare(client: MiClubClient, label: str, event: EventLink, target_date: str, cfg: ResolvedConfig, target_time_for_run: dtime) -> PreparedBooking:
    log.info("[%s] fetching event page %s", label, event.event_id)
    resp = client.get(f"/members/bookings/open/event.msp?booking_event_id={event.event_id}&booking_resource_id={event.resource_id}")
    rows = parse_event_page(resp.text)
    if not rows:
        log.info("[%s] event %s is Locked (no slot grid yet) — will discover row_id at fire time", label, event.event_id)
        return PreparedBooking(target_label=label, target_date=target_date, event=event, deferred=True)
    candidates_times = _candidate_times(cfg, target_time_for_run)
    candidates_rows: list[SlotRow] = []
    for ct in candidates_times:
        for r in rows:
            if r.hour == ct.hour and r.minute == ct.minute:
                candidates_rows.append(r)
                break
    if not candidates_rows:
        log.warning("[%s] no candidate slots found on event page", label)
    payloads: dict[int, dict[str, list[str]]] = {}
    enabled_ids = cfg.partner_ids_for_day(label)
    for row in candidates_rows:
        url = f"/members/bookings/open/DefaultPartners.msp?booking_event_id={event.event_id}&booking_row_id={row.row_id}&hasMultipleFees=false"
        p = client.get(url)
        fields = parse_make_booking_form(p.text)
        if fields:
            payloads[row.row_id] = _build_payload_for_row(fields, row.row_id, enabled_ids)
    log.info("[%s] prepared %d candidate slot(s)", label, len(payloads))
    return PreparedBooking(
        target_label=label, target_date=target_date, event=event,
        candidate_slots=candidates_rows, payloads=payloads,
    )


def _adapt_stock_payload(stock: dict[str, list[str]], event_id: int, row_id: int, title: str,
                         enabled_partner_ids: set[str] | None = None) -> dict[str, list[str]]:
    p = _apply_partner_filter(stock, enabled_partner_ids)
    p["booking_event_id"] = [str(event_id)]
    p["booking_row_id"] = [str(row_id)]
    if title:
        p["title"] = [title]
    return p


def _build_stock_payload_from_open_event(client: MiClubClient, cfg: ResolvedConfig) -> dict[str, list[str]] | None:
    try:
        end = datetime.now(ADELAIDE).date() + timedelta(days=15)
        start = datetime.now(ADELAIDE).date()
        spring = parse_spring_events(client.fetch_events_json(start, end, cfg.resource_id))
    except Exception:
        return None
    for ev in spring:
        if ev.status != "Open" or "TIMESHEET" not in ev.title.upper():
            continue
        r = client.get(f"/members/bookings/open/event.msp?booking_event_id={ev.event_id}&booking_resource_id={ev.resource_id}")
        rows = parse_event_page(r.text)
        if not rows:
            continue
        any_row = next((r for r in rows if r.is_available), rows[0])
        url = f"/members/bookings/open/DefaultPartners.msp?booking_event_id={ev.event_id}&booking_row_id={any_row.row_id}&hasMultipleFees=false"
        p = client.get(url)
        fields = parse_make_booking_form(p.text)
        if fields:
            log.info("built stock payload from %s (event %s, row %s): %d fields",
                     ev.event_date, ev.event_id, any_row.row_id, len(fields))
            return {k: list(v) for k, v in fields.items()}
    return None


def _fire(client: MiClubClient, prepared: PreparedBooking, target_epoch: float, cfg: ResolvedConfig, target_time_for_run: dtime, dont_fire: bool = False) -> FireOutcome:
    attempts: list[str] = []
    booked: dtime | None = None
    success = False
    fire_start = time.time()

    if prepared.deferred:
        deadline_for_event = time.time() + 5.0
        rows: list[SlotRow] = []
        poll_count = 0
        while time.time() < deadline_for_event:
            poll_start = time.time()
            r = client.get(
                f"/members/bookings/open/event.msp?booking_event_id={prepared.event.event_id}"
                f"&booking_resource_id={prepared.event.resource_id}"
            )
            poll_count += 1
            rows = parse_event_page(r.text)
            if rows:
                break
            sleep_for = POLL_INTERVAL_SECONDS - (time.time() - poll_start)
            if sleep_for > 0:
                time.sleep(sleep_for)
        if not rows:
            attempts.append(f"deferred event never unlocked within 5s (polled {poll_count}x)")
            return FireOutcome(label=prepared.target_label, success=False, booked_slot=None, attempts=attempts, fire_epoch_ms=int(fire_start * 1000))
        for ct in _candidate_times(cfg, target_time_for_run):
            for r in rows:
                if r.hour == ct.hour and r.minute == ct.minute:
                    prepared.candidate_slots.append(r)
                    break
        if prepared.stock_payload is None:
            attempts.append("deferred event: no stock payload available")
            return FireOutcome(label=prepared.target_label, success=False, booked_slot=None, attempts=attempts, fire_epoch_ms=int(fire_start * 1000))
        for r in prepared.candidate_slots:
            prepared.payloads[r.row_id] = _adapt_stock_payload(
                prepared.stock_payload, prepared.event.event_id, r.row_id, prepared.event_title_for_payload,
                cfg.partner_ids_for_day(prepared.target_label),
            )
        unlock_ms = int((time.time() - target_epoch) * 1000)
        attempts.append(f"discovered {len(prepared.candidate_slots)} candidate row(s) +{unlock_ms}ms after unlock (poll x{poll_count})")

    for row in prepared.candidate_slots:
        payload = prepared.payloads.get(row.row_id)
        if payload is None:
            attempts.append(f"{row.hour:02d}:{row.minute:02d} skipped (no payload)")
            continue
        if dont_fire:
            now_t = time.time()
            attempts.append(f"{row.hour:02d}:{row.minute:02d} WOULD-FIRE send=+{int((now_t - target_epoch) * 1000)}ms row_id={row.row_id} fields={len(payload)}")
            booked = dtime(row.hour, row.minute)
            success = True
            break
        start, resp = client.fire_get("/members/bookings/open/MakeBooking.msp", payload)
        end = time.time()
        ms_after_target = int((start - target_epoch) * 1000)
        rtt_ms = int((end - start) * 1000)
        text = resp.text
        has_error = ("errorReason" in text) or ("Member is already booked" in text)
        is_success = resp.status_code == 200 and not has_error
        if is_success:
            attempts.append(f"{row.hour:02d}:{row.minute:02d} OK send=+{ms_after_target}ms rtt={rtt_ms}ms")
            booked = dtime(row.hour, row.minute)
            success = True
            break
        reason = ""
        if "errorReason" in text:
            import re as _re
            m = _re.search(r'class="errorReason"[^>]*>\s*([^<]+)', text)
            if m:
                reason = " (" + m.group(1).strip()[:60] + ")"
        attempts.append(f"{row.hour:02d}:{row.minute:02d} fail status={resp.status_code}{reason} send=+{ms_after_target}ms rtt={rtt_ms}ms")
    return FireOutcome(
        label=prepared.target_label, success=success, booked_slot=booked,
        attempts=attempts, fire_epoch_ms=int(fire_start * 1000),
    )


def run(*, target: str | None = None, dry_fire_in_seconds: float | None = None, dont_fire: bool = False) -> FireOutcome:
    cfg = load_config()
    log.info("config: base_url=%s use_mock=%s play_days=%s", cfg.effective_base_url, cfg.use_mock, list(cfg.play_days))

    now = datetime.now(ADELAIDE)
    if dry_fire_in_seconds is not None:
        fire_time = now + timedelta(seconds=dry_fire_in_seconds)
    else:
        fire_time = production_fire_time(now)
    log.info("now=%s fire_at=%s (in %.1fs)", now, fire_time, (fire_time - now).total_seconds())

    one_off_override = None
    if target is None:
        one_off_override = find_one_off_for_today(cfg, fire_time)
        if one_off_override is not None:
            target = one_off_override["target"]
            log.info("one-off booking triggered for today: play=%s date=%s time=%s",
                     target, one_off_override["target_date"], one_off_override["target_time_hhmm"])
        else:
            target = infer_target_from_weekday(fire_time, cfg.play_days)
    target = target.lower()
    if target not in DAY_NAMES:
        raise SystemExit(f"invalid --target {target!r} (must be one of: {DAY_NAMES})")

    label = _title_friendly(target)
    if one_off_override is not None:
        target_date = one_off_override["target_date"]
    else:
        target_date = target_date_for(fire_time, target)

    client = MiClubClient(cfg.effective_base_url, cfg.username, cfg.password)
    log.info("logging in...")
    if not client.login():
        outcome = FireOutcome(label="login", success=False, booked_slot=None, attempts=["login failed"], fire_epoch_ms=int(time.time() * 1000))
        _summarize_and_notify(cfg, fire_time, [outcome])
        return outcome
    log.info("logged in")

    if _marker_path(label, target_date).exists():
        log.info("[%s] already booked %s — skipping (marker present)", label, target_date)
        outcome = FireOutcome(label=label, success=True, booked_slot=None, attempts=[f"skipped: marker for {target_date} exists"], fire_epoch_ms=int(time.time() * 1000))
        _summarize_and_notify(cfg, fire_time, [outcome])
        return outcome

    event, open_time_ms = _pick_target_event(client, fire_time, target, cfg)
    if event is None:
        outcome = FireOutcome(label=label, success=False, booked_slot=None, attempts=[f"no {label.upper()} TIMESHEET found for {target_date}"], fire_epoch_ms=int(time.time() * 1000))
        _summarize_and_notify(cfg, fire_time, [outcome])
        return outcome
    log.info("[%s] event %s: %s", label, event.event_id, event.title)

    if open_time_ms is not None and dry_fire_in_seconds is None:
        canonical = datetime.fromtimestamp(open_time_ms / 1000.0, tz=ADELAIDE)
        new_fire_time = canonical - timedelta(milliseconds=1500)
        if abs((new_fire_time - fire_time).total_seconds()) > 1.0:
            log.info("retargeting fire_time from %s -> %s (server openTime - 1500ms)",
                     fire_time.isoformat(timespec="milliseconds"),
                     new_fire_time.isoformat(timespec="milliseconds"))
            fire_time = new_fire_time

    if one_off_override is not None:
        # One-off can specify its own target time independent of the recurring per-day time
        hh, mm = one_off_override["target_time_hhmm"].split(":")
        target_time_for_run = dtime(int(hh), int(mm))
    else:
        target_time_for_run = cfg.target_time_for(target)
    log.info("[%s] target time = %s", label, target_time_for_run.strftime("%H:%M"))
    prepared = _prepare(client, label, event, target_date=target_date, cfg=cfg, target_time_for_run=target_time_for_run)
    if prepared.deferred:
        log.info("[%s] event is Locked; sourcing stock payload from a currently-Open event", label)
        prepared.stock_payload = _build_stock_payload_from_open_event(client, cfg)
        prepared.event_title_for_payload = event.title
        if prepared.stock_payload is None:
            log.warning("[%s] no stock payload available — fire-time discovery will fail", label)

    refresh_deadline = fire_time - timedelta(seconds=2.5)
    while datetime.now(ADELAIDE) < refresh_deadline:
        delta = (refresh_deadline - datetime.now(ADELAIDE)).total_seconds()
        if delta > 30.0:
            client.keep_alive()
            time.sleep(15.0)
        else:
            time.sleep(min(max(delta, 0.1), 1.0))

    log.info("refreshing primary payload at T-%.1fs", (fire_time - datetime.now(ADELAIDE)).total_seconds())
    _refresh_primary_payload(client, prepared, cfg)

    while True:
        delta = (fire_time - datetime.now(ADELAIDE)).total_seconds()
        if delta < 1.0:
            break
        time.sleep(min(delta - 1.0, 0.5))

    target_epoch = fire_time.timestamp()
    log.info("spin-waiting to %s", fire_time.isoformat(timespec="milliseconds"))
    actual = wait_until(fire_time, sleep_until_lead_seconds=0.3)
    log.info("fire! drift=%dms", int((actual - target_epoch) * 1000))

    fire_client = client.clone_for_fire()
    outcome = _fire(fire_client, prepared, target_epoch, cfg, target_time_for_run, dont_fire=dont_fire)

    if not dont_fire and outcome.success and outcome.booked_slot is not None:
        try:
            marker = _marker_path(prepared.target_label, prepared.target_date)
            marker.write_text(f"{outcome.booked_slot.strftime('%H:%M')}\n", encoding="utf-8")
        except Exception:
            log.exception("failed to write idempotency marker (booking still succeeded)")

    _summarize_and_notify(cfg, fire_time, [outcome])
    return outcome


def _write_last_run(status: str, summary: str, detail: str = "") -> None:
    """Persist a machine-readable last-run record the GUI surfaces on its dashboard.
    This is the zero-config safety net: even with email off, opening the app shows
    whether the most recent run succeeded, failed, or crashed. Best-effort — never raises."""
    try:
        ensure_dirs()
        rec = {
            "time_local": datetime.now().isoformat(timespec="seconds"),
            "time_adelaide": datetime.now(ADELAIDE).isoformat(timespec="seconds"),
            "status": status,  # "ok" | "fail" | "crash"
            "summary": summary,
            "detail": detail[:4000],
        }
        LAST_RUN_PATH.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    except Exception:
        log.exception("failed to write last_run.json")


def _summarize_and_notify(cfg: ResolvedConfig, fire_time: datetime, outcomes: list[FireOutcome]) -> None:
    lines = [f"Tee Off booking summary — fire target {fire_time.isoformat(timespec='milliseconds')}", ""]
    all_ok = all(o.success for o in outcomes)
    overall = "SUCCESS" if all_ok else "PARTIAL/FAIL"
    for o in outcomes:
        status = "OK" if o.success else "FAIL"
        slot = o.booked_slot.strftime("%H:%M") if o.booked_slot else "—"
        lines.append(f"[{o.label}] {status} — booked {slot}")
        for a in o.attempts:
            lines.append(f"  - {a}")
        lines.append("")
    body = "\n".join(lines)
    log.info("\n%s", body)
    try:
        ensure_dirs()
        (LOGS_DIR / f"run-{fire_time.strftime('%Y%m%d-%H%M%S')}.log").write_text(body, encoding="utf-8")
        (LOGS_DIR / "latest.txt").write_text(body, encoding="utf-8")
    except Exception:
        log.exception("failed to write run log")
    summary = "; ".join(
        f"{o.label} {'OK ' + (o.booked_slot.strftime('%H:%M') if o.booked_slot else '') if o.success else 'FAILED'}".strip()
        for o in outcomes
    )
    _write_last_run("ok" if all_ok else "fail", summary, body)
    if cfg.email_enabled:
        send_summary(cfg, f"Tee Off booking {overall}", body)


def _notify_fatal(subject: str, body: str) -> None:
    """Best-effort email on a fatal/early failure. Must not raise. Loads config in
    its own guard so a config problem can't suppress the notification of OTHER crashes."""
    try:
        cfg = load_config()
    except Exception:
        return
    if not cfg.email_enabled:
        return
    try:
        send_summary(cfg, subject, body)
    except Exception:
        log.exception("fatal-path notify failed")


def _record_fatal(exc: BaseException, kind: str) -> None:
    """Persist an absolute-path crash record + last-run status + attempt notify, for
    any failure that escapes before the normal summary path runs. Never raises.
    kind: 'crash' (unexpected exception) or 'error' (orderly misconfigured exit)."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    stamp = datetime.now()
    body = (f"Tee Off booker {kind.upper()} at {stamp.isoformat(timespec='seconds')}\n\n{tb}")
    try:
        ensure_dirs()
        (LOGS_DIR / f"crash-{stamp.strftime('%Y%m%d-%H%M%S')}.log").write_text(body, encoding="utf-8")
    except Exception:
        log.exception("failed to write crash log")
    _write_last_run("crash", f"{kind}: {str(exc)[:200]}", body)
    _notify_fatal(f"Tee Off booker {kind.upper()}", body)


def _main() -> None:
    try:
        ensure_dirs()
    except Exception:
        pass  # data dir creation is best-effort; per-handler writes are individually guarded
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(LOGS_DIR / "booker.log", encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        handlers=handlers)
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=None,
                    help="play day: monday, tuesday, ..., sunday. If omitted, inferred from fire weekday and configured play_days.")
    ap.add_argument("--dry-fire-in", type=float, default=None, help="fire N seconds from now (for testing)")
    ap.add_argument("--dont-fire", action="store_true", help="full prep + spin-wait but DON'T send MakeBooking.msp")
    args = ap.parse_args()
    try:
        run(target=args.target, dry_fire_in_seconds=args.dry_fire_in, dont_fire=args.dont_fire)
    except SystemExit as e:
        # Orderly "misconfigured" exits raised inside run() (no matching play day,
        # invalid target). Record them so grandpa sees a reason, then propagate.
        if e.code not in (0, None):
            log.error("booker exited with error: %s", e)
            _record_fatal(e, "error")
        raise
    except BaseException as e:  # noqa: BLE001 — last-resort guard: a crash MUST leave a trace
        log.exception("booker crashed before completing")
        _record_fatal(e, "crash")
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
