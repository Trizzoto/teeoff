"""FastAPI mock of MiClub for grandpa-golf booker testing.

Routes (mirroring the real site):
  GET  /security/login.msp                   - login form
  POST /security/login.msp                   - login submit, sets session cookie
  GET  /cms/                                 - members home
  GET  /views/members/booking/eventList.xhtml - event list
  GET  /members/bookings/open/event.msp      - slot table (?booking_event_id=&booking_resource_id=)
  GET  /members/bookings/open/DefaultPartners.msp - partner picker
  GET  /members/bookings/open/MakeBooking.msp - final booking submit
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import state as st

LOGIN_LATENCY_SECONDS = 5.0  # mimic the slow login screen

app = FastAPI(title="MiClub Mock")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSIONS: dict[str, str] = {}  # cookie -> username


def _is_authed(request: Request) -> bool:
    cookie = request.cookies.get("JSESSIONID")
    return cookie in SESSIONS


def _require_auth(request: Request) -> None:
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="login required")


@app.get("/security/login.msp", response_class=HTMLResponse)
async def login_get(request: Request):
    st.state.log_request("GET", "/security/login.msp")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/security/login.msp")
async def login_post(
    request: Request,
    action: str = Form(...),
    user: str = Form(...),
    password: str = Form(...),
):
    st.state.log_request("POST", "/security/login.msp", {"action": action, "user": user})
    await asyncio.sleep(LOGIN_LATENCY_SECONDS)  # the slow login
    if action != "login" or not user or not password:  # test double: accept any non-empty login
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
            status_code=401,
        )
    token = secrets.token_hex(16)
    SESSIONS[token] = user
    resp = RedirectResponse(url="/cms/", status_code=status.HTTP_302_FOUND)
    resp.set_cookie("JSESSIONID", token, httponly=True)
    return resp


@app.get("/cms/", response_class=HTMLResponse)
async def members_home(request: Request):
    _require_auth(request)
    st.state.log_request("GET", "/cms/")
    return templates.TemplateResponse("members_home.html", {"request": request})


@app.get("/views/members/booking/eventList.xhtml", response_class=HTMLResponse)
async def event_list(request: Request):
    _require_auth(request)
    st.state.log_request("GET", "/views/members/booking/eventList.xhtml")
    return templates.TemplateResponse(
        "event_list.html",
        {
            "request": request,
            "events": list(st.state.events.values()),
            "resource_id": st.RESOURCE_ID,
        },
    )


@app.get("/members/bookings/open/event.msp", response_class=HTMLResponse)
async def event_page(request: Request, booking_event_id: int, booking_resource_id: int):
    _require_auth(request)
    st.state.log_request(
        "GET",
        "/members/bookings/open/event.msp",
        {"booking_event_id": booking_event_id},
    )
    event = st.state.events.get(booking_event_id)
    if not event or booking_resource_id != st.RESOURCE_ID:
        raise HTTPException(status_code=404, detail="event not found")
    rows_csv = ",".join(f"row-{s.row_id}" for s in event.slots)
    is_open = st.state.is_open()
    return templates.TemplateResponse(
        "event.html",
        {
            "request": request,
            "event": event,
            "rows_csv": rows_csv,
            "resource_id": st.RESOURCE_ID,
            "ts": datetime.now(st.ADELAIDE).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "is_open": is_open,
        },
    )


@app.get("/members/bookings/open/DefaultPartners.msp", response_class=HTMLResponse)
async def default_partners(
    request: Request,
    booking_event_id: int,
    booking_row_id: int,
    hasMultipleFees: str = "false",  # noqa: N803 (matches real param name)
):
    _require_auth(request)
    st.state.log_request(
        "GET",
        "/members/bookings/open/DefaultPartners.msp",
        {"booking_event_id": booking_event_id, "booking_row_id": booking_row_id},
    )
    event = st.state.events.get(booking_event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    slot = event.find_slot_by_row_id(booking_row_id)
    if not slot:
        raise HTTPException(status_code=404, detail="row not found")
    return templates.TemplateResponse(
        "default_partners.html",
        {
            "request": request,
            "event_id": booking_event_id,
            "row_id": booking_row_id,
            "times_free": slot.available_count,
            "event_title": event.title,
            "partners": st.DEFAULT_PARTNERS,
        },
    )


@app.get("/members/bookings/open/MakeBooking.msp", response_class=HTMLResponse)
async def make_booking(
    request: Request,
    doAction: str,  # noqa: N803
    booking_event_id: int,
    booking_row_id: int,
):
    _require_auth(request)
    qp = dict(request.query_params)
    st.state.log_request("GET", "/members/bookings/open/MakeBooking.msp", qp)

    if doAction not in ("bookGroup", "FAST_BOOK"):
        raise HTTPException(status_code=400, detail=f"unknown doAction: {doAction}")
    if not st.state.is_open():
        return templates.TemplateResponse(
            "make_booking_failure.html",
            {"request": request, "reason": "Bookings are not yet open."},
            status_code=403,
        )

    event = st.state.events.get(booking_event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    slot = event.find_slot_by_row_id(booking_row_id)
    if not slot:
        raise HTTPException(status_code=404, detail="row not found")

    # Pull member IDs out of the form (memberId_0, memberId_1, ...)
    member_ids = sorted(
        ((int(k.split("_")[1]), v) for k, v in qp.items() if k.startswith("memberId_")),
        key=lambda kv: kv[0],
    )
    player_ids = [v for _, v in member_ids]

    with st.state.lock:
        free = [i for i, c in enumerate(slot.cells) if c is None]
        if len(free) < len(player_ids):
            return templates.TemplateResponse(
                "make_booking_failure.html",
                {"request": request, "reason": "Booking Row is locked by another user"},
                status_code=409,
            )
        # check no double-book: if any of the player_ids are already in this event, reject
        already = {pid for s in event.slots for pid in s.cells if pid}
        if any(pid in already for pid in player_ids):
            return templates.TemplateResponse(
                "make_booking_failure.html",
                {
                    "request": request,
                    "reason": "This booking time has already been booked.",
                },
                status_code=409,
            )
        for idx, pid in zip(free, player_ids):
            slot.cells[idx] = pid

    players = [p for p in st.DEFAULT_PARTNERS if p["id"] in player_ids]
    return templates.TemplateResponse(
        "make_booking_success.html",
        {
            "request": request,
            "event_title": event.title,
            "time_label": slot.time_label(),
            "players": players,
        },
    )


# --- Test helpers ---------------------------------------------------------

@app.get("/__mock/reset")
async def mock_reset(open_at_seconds_from_now: float = 10.0):
    """Reset state and configure when bookings open."""
    from datetime import timedelta
    new_open_at = datetime.now(st.ADELAIDE) + timedelta(seconds=open_at_seconds_from_now)
    st.state = st.fresh_state(open_at=new_open_at)
    SESSIONS.clear()
    return {"reset": True, "open_at": st.state.open_at.isoformat(timespec="milliseconds")}


@app.get("/__mock/prefill")
async def mock_prefill(event_id: int, hour: int, minute: int, cells_filled: int = 4):
    """Pre-fill a slot with placeholder member IDs to test fallback behaviour."""
    event = st.state.events.get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    slot = event.find_slot(hour, minute)
    if not slot:
        raise HTTPException(status_code=404, detail="slot not found")
    for i in range(min(cells_filled, 4)):
        slot.cells[i] = f"placeholder-{i}"
    return {"event_id": event_id, "slot": slot.time_label(), "cells": slot.cells}


@app.get("/__mock/log")
async def mock_log():
    return {"open_at": st.state.open_at.isoformat(timespec="milliseconds"), "requests": st.state.request_log}


@app.get("/__mock/status")
async def mock_status():
    now = datetime.now(st.ADELAIDE)
    bookings = {}
    for eid, ev in st.state.events.items():
        bookings[ev.title] = [
            {"time": s.time_label(), "row_id": s.row_id, "cells": s.cells}
            for s in ev.slots if any(c is not None for c in s.cells)
        ]
    return {"now": now.isoformat(timespec="milliseconds"), "open_at": st.state.open_at.isoformat(timespec="milliseconds"), "bookings": bookings}
