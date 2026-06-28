# Grandpa Golf Auto-Booker

Auto-books Monday + Wednesday 8:12am tee times at West Beach Parks Golf Club for grandpa.
Fires at exactly **19:00:00.000 Australia/Adelaide** twice a week:

- **Sundays 19:00** → books the **Monday 15 days later** (Mon plays unlock at this moment)
- **Tuesdays 19:00** → books the **Wednesday 15 days later** (Wed plays unlock at this moment)

Each TIMESHEET event unlocks at exactly **19:00:03 ACST, 15 days before play**. The MiClub
Spring API exposes this as an `openTime` epoch-ms field per event.

## How it works

1. **Pre-warm** (~5 min before fire): logs in, fetches the event list (Spring JSON API on real
   site, HTML on mock), picks the single target TIMESHEET, fetches its slot table and
   pre-builds the partner-picker payload for every candidate from 8:12 to 9:00.
2. **Refresh** at T-2.5s: re-fetches the priority 8:12 partner form so any seconds-fresh
   tokens are valid at fire moment.
3. **Fire** at 19:00:03.500 (the server unlocks at 19:00:03.930 — we fire 430ms early so
   the poll loop catches the unlock on the first iteration after). Sends `MakeBooking.msp`
   GET, walking 8:12 → 8:06 → 8:00 → 7:54 → 7:48 → 7:42 → 7:36 → 7:30 → 7:24 → 7:18 → 7:12
   → 7:06 → 7:00 (fallback walks **earlier**, matching grandpa's historical bookings
   which cluster at 7:30–7:54).
4. **Locked-event handling**: if the target event is still Locked at prep time (which is
   the normal case — it unlocks at fire moment), the booker pre-loads a stock partner
   payload from a different open TIMESHEET event, then polls the target's `event.msp` from
   T+0 to discover the row_id the instant it unlocks.
5. **Notify**: emails a summary (or logs to file if SMTP isn't configured).
6. **Idempotency**: writes `logs/booked-<date>-<dow>.flag` per successful booking so a
   retry skips instead of double-booking.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# fill in WBP_USERNAME / WBP_PASSWORD, then SMTP_USER / SMTP_APP_PASSWORD for email
```

### .env

| Variable | Purpose |
|---|---|
| `WBP_USERNAME` | West Beach Parks member number |
| `WBP_PASSWORD` | West Beach Parks password |
| `WBP_BASE_URL` | `https://golf.westbeachparks.com.au` |
| `USE_MOCK` | `true` → hit the local mock; `false` → hit the real site |
| `MOCK_BASE_URL` | `http://127.0.0.1:8000` (use `127.0.0.1`, not `localhost`, to avoid Windows IPv6 fallback) |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` for Gmail |
| `SMTP_USER` | sender Gmail address |
| `SMTP_APP_PASSWORD` | Gmail [app password](https://myaccount.google.com/apppasswords) (NOT the account password) |
| `NOTIFY_TO` | where to send the summary |

## Run

```powershell
# 1. Start the mock server in one terminal
python -m mock

# 2. In another terminal: simulate a fire happening in 10 seconds against mock
.venv\Scripts\python -m booker --target mon --dry-fire-in 10
.venv\Scripts\python -m booker --target wed --dry-fire-in 10

# 3. Production runs — --target is inferred from fire weekday (Sun→mon, Tue→wed)
.venv\Scripts\python -m booker
```

Useful mock helpers (while mock is running):

```powershell
# Reset state; bookings open in 10s from now
curl "http://127.0.0.1:8000/__mock/reset?open_at_seconds_from_now=10"
# Mark Tuesday's 8:12 as fully booked to test fallback
curl "http://127.0.0.1:8000/__mock/prefill?event_id=12369009&hour=8&minute=12"
# Inspect mock state
curl http://127.0.0.1:8000/__mock/status
curl http://127.0.0.1:8000/__mock/log
```

## Distribute to grandpa (installed program)

Build a one-click Windows installer:

```powershell
winget install JRSoftware.InnoSetup        # one-time, provides the Inno Setup compiler
.venv\Scripts\python build_installer.py     # -> dist\TeeOff-Setup.exe
```

`TeeOff-Setup.exe` is a **per-user installer (no admin)** that:

- installs the app to a **fixed** location, `%LOCALAPPDATA%\Programs\TeeOff`, so the
  scheduled task's interpreter path can never go stale by the folder being moved or
  re-unzipped (the original "flashes on screen then disappears" failure);
- creates a Start-menu + desktop **TeeOff** shortcut;
- registers the `GrandpaGolfAutoBooker` task (Sun + Tue 18:55) pointing at the fixed
  install, run **windowless** via `pythonw.exe`;
- installs an uninstaller (Add/Remove Programs) that removes the task + app but
  **preserves user data**.

Send `TeeOff-Setup.exe` to grandpa; he double-clicks it. To ship an update, rebuild and
re-run the installer over the top — his settings/logs survive.

### Runtime data + safety nets

- **User data** (settings, logs, idempotency markers, `last_run.json`) lives in a fixed
  per-user dir, `%LOCALAPPDATA%\TeeOff`, separate from the replaceable install — so updates
  never wipe config. Override with `$env:TEEOFF_DATA_DIR` for testing (see `app/paths.py`).
- **Self-heal:** opening the app re-checks the scheduled task and silently repairs its path
  if the install ever moved (`app.scheduler.ensure_task_current`, called on GUI launch).
- **Never silent:** every booker run — success, failure, or crash — writes `last_run.json`
  + a log under `%LOCALAPPDATA%\TeeOff\logs`, surfaced on the dashboard as a green/red
  "Last run" status. Email is an optional extra channel on top.

Low-level task management (used by the installer): `python -m app.scheduler register |
unregister | info | xml`.

## Live cutover

```powershell
# Safe-mode dry-fire against the real site — does full prep + spin-wait,
# but logs "WOULD-FIRE" instead of actually sending MakeBooking.msp.
.venv\Scripts\python -m booker --target mon --dont-fire --dry-fire-in 30
.venv\Scripts\python -m booker --target wed --dont-fire --dry-fire-in 30

# Read-only recon, no spin-wait
.venv\Scripts\python -m booker.recon
```

1. Confirm safe-mode dry-fire works end-to-end for both targets.
2. Make sure `logs/booked-*.flag` is empty.
3. Confirm laptop is plugged in and on wifi by 18:30 on Sun/Tue.
4. Trust the email summary at ~19:00:15.

## Tested timing

Five back-to-back dry-fires against the mock:

| Run | Drift to target | Send latency | Round-trip |
|---|---|---|---|
| 1 | 0 ms | +2 ms | 6 ms |
| 2 | 0 ms | +1 ms | 6 ms |
| 3 | 0 ms | +2 ms | 6 ms |
| 4 | 0 ms | +1 ms | 8 ms |
| 5 | 0 ms | +1 ms | 6 ms |

All well inside the ±50 ms target. (The real site adds ~80–200 ms of internet RTT.)

## Project layout

```
grandpa golf/
├── booker/                  # the booking script
│   ├── main.py              # orchestrator
│   ├── client.py            # MiClub HTTP client
│   ├── parser.py            # BS4 HTML parsing
│   ├── timing.py            # spin-wait until 19:00:00.000
│   ├── notifier.py          # SMTP email summary
│   └── config.py            # .env loader
├── mock/                    # FastAPI mock of MiClub
│   ├── server.py
│   ├── state.py
│   └── templates/
├── scheduler/
│   └── grandpa-golf.xml     # Windows Task Scheduler config
├── logs/                    # run logs + idempotency markers
├── .env / .env.example
└── requirements.txt
```

## Notes from recon

- Login is `POST /security/login.msp` with `action=login&user=...&password=...`. No CSRF token.
- The event list page (`/views/members/booking/eventList.xhtml`) is **React-rendered** and
  fetches data via `GET /spring/bookings/events/between/D-M-YYYY/D-M-YYYY/<resource_id>?time=<epoch_ms>`.
  That endpoint returns JSON; cookie auth is enough (no JWT required for read).
- The slot grid is the old-school `/members/bookings/open/event.msp?booking_event_id=X&booking_resource_id=3000000`
  page. Each slot is a `<div id="row-12486416" class="row row-time group-G time-T available ...">`
  — the `id="row-N"` carries the **booking_row_id**.
- Partner picker: `GET /members/bookings/open/DefaultPartners.msp?booking_event_id=X&booking_row_id=Y&hasMultipleFees=false`
  works even before bookings open — it returns the booking form with grandpa's saved default
  partners (4 members) pre-filled as 41 hidden fields.
- Final submission: `GET /members/bookings/open/MakeBooking.msp` with all those pre-filled
  hidden fields. We forward exactly what the partner-picker form contained, only injecting
  our chosen `booking_row_id`.
- Bookings open at Sunday 19:00:00 Adelaide for the next Tue + Wed.
