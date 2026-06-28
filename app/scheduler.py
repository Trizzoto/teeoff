"""Windows Task Scheduler registration via PowerShell.

The booker is launched at 18:55 ACST on the day BEFORE each enabled play day's
same-named-day-of-the-prior-week. The booker itself reads settings.json to know
which play day to book and what time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .settings import DAY_NAMES, fire_weekday, load_settings

TASK_NAME = "GrandpaGolfAutoBooker"

DAY_TO_XML_TAG = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}


def _project_root() -> Path:
    return Path(__file__).parent.parent


def _interpreter(windowless: bool) -> Path:
    """Resolve the interpreter that should run the booker, from the CURRENT install
    location. Prefer the bundled python/ (grandpa's install), then .venv (dev), then
    the running interpreter. windowless=True picks pythonw.exe so a scheduled run
    shows no console window — and never 'flashes' a console on an early error."""
    name = "pythonw.exe" if windowless else "python.exe"
    root = _project_root()
    bundled = root / "python" / name
    if bundled.exists():
        return bundled
    venv = root / ".venv" / "Scripts" / name
    if venv.exists():
        return venv
    cand = Path(sys.executable).with_name(name)
    return cand if cand.exists() else Path(sys.executable)


def _task_python() -> Path:
    """The (windowless) interpreter the scheduled task should launch."""
    return _interpreter(windowless=True)


def _next_weekday(weekday: int, after: datetime | None = None) -> datetime:
    """Return next datetime whose weekday matches, at 18:55:00. If today already matches and
    18:55 hasn't passed, return today; otherwise return next week."""
    now = after or datetime.now()
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(hour=18, minute=55, second=0, microsecond=0)
    if days_ahead == 0 and now > candidate:
        candidate = candidate + timedelta(days=7)
    return candidate


def build_xml(settings: dict[str, Any] | None = None, project_root: Path | None = None) -> str:
    s = settings if settings is not None else load_settings()
    root = project_root or _project_root()
    python_exe = _task_python()

    # v2 schema: "days" map with per-day enabled+target_time
    days_map = s.get("days") or {}
    play_days: list[str] = [d for d in DAY_NAMES if days_map.get(d, {}).get("enabled", False)]
    # Back-compat: v1 had "booking.play_days"
    if not play_days and "play_days" in s.get("booking", {}):
        play_days = list(s["booking"]["play_days"])
    fire_days = sorted({fire_weekday(d) for d in play_days})

    triggers_xml_parts = []
    for wd in fire_days:
        start_boundary = _next_weekday(wd).strftime("%Y-%m-%dT%H:%M:%S")
        day_tag = DAY_TO_XML_TAG[wd]
        triggers_xml_parts.append(
            f"""    <CalendarTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <{day_tag} />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>"""
        )

    # One-time triggers for any one-off bookings still in the future
    from datetime import date as _date, datetime as _dt, timedelta as _td
    today = _date.today()
    for oo in s.get("one_offs", []):
        try:
            play_date = _date.fromisoformat(oo["play_date"])
        except (ValueError, KeyError, TypeError):
            continue
        fire_date = play_date - _td(days=15)
        # Skip if fire moment already passed
        fire_dt = _dt.combine(fire_date, _dt.min.time().replace(hour=18, minute=55))
        if fire_dt <= _dt.now():
            continue
        sb = fire_dt.strftime("%Y-%m-%dT%H:%M:%S")
        triggers_xml_parts.append(
            f"""    <TimeTrigger>
      <StartBoundary>{sb}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>"""
        )

    triggers_block = "\n".join(triggers_xml_parts) if triggers_xml_parts else (
        "    <TimeTrigger><StartBoundary>2099-01-01T00:00:00</StartBoundary><Enabled>false</Enabled></TimeTrigger>"
    )
    description = (
        "Auto-book grandpa's tee times. Books one TIMESHEET slot 15 days out at the configured "
        "time on each enabled play day."
    )

    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
{triggers_block}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>4</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>-m booker</Arguments>
      <WorkingDirectory>{root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
    return xml


_CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW (Windows)


def _ps(command: str) -> subprocess.CompletedProcess:
    """Run PowerShell silently — no console window pop-up on Windows."""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=30,
        creationflags=_CREATE_NO_WINDOW,
    )


def register(settings: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Register (or replace) the scheduled task. Returns (success, message)."""
    xml = build_xml(settings)
    ps_script = (
        f"$xml = @'\n{xml}\n'@; "
        f"Unregister-ScheduledTask -TaskName {TASK_NAME} -Confirm:$false -ErrorAction SilentlyContinue | Out-Null; "
        f"Register-ScheduledTask -Xml $xml -TaskName '{TASK_NAME}' -User $env:USERNAME | Out-Null; "
        f"Write-Output 'OK'"
    )
    result = _ps(ps_script)
    if result.returncode == 0 and "OK" in result.stdout:
        return True, f"Registered. Triggers: {_describe_triggers(settings)}"
    err = (result.stderr or result.stdout or "unknown error").strip()
    return False, f"Register failed: {err[:300]}"


def unregister() -> tuple[bool, str]:
    result = _ps(f"Unregister-ScheduledTask -TaskName {TASK_NAME} -Confirm:$false -ErrorAction Stop; 'OK'")
    if result.returncode == 0:
        return True, "Unregistered."
    err = (result.stderr or result.stdout or "").strip()
    return False, f"Unregister failed: {err[:300]}"


def set_paused(paused: bool) -> tuple[bool, str]:
    verb = "Disable-ScheduledTask" if paused else "Enable-ScheduledTask"
    result = _ps(f"{verb} -TaskName {TASK_NAME} | Out-Null; 'OK'")
    if result.returncode == 0:
        return True, ("Paused (task disabled)." if paused else "Resumed (task enabled).")
    err = (result.stderr or result.stdout or "").strip()
    return False, f"{verb} failed: {err[:300]}"


def get_info() -> dict[str, Any]:
    """Return a dict of state, NextRunTime ISO, LastRunTime ISO, LastTaskResult, triggers."""
    ps = (
        f"$t = Get-ScheduledTask -TaskName {TASK_NAME} -ErrorAction Stop; "
        f"$i = Get-ScheduledTaskInfo -TaskName {TASK_NAME}; "
        f"$a = @($t.Actions)[0]; "
        # NextRunTime/LastRunTime are $null for a paused (Disabled) or all-past-trigger
        # task; calling .ToString() on $null throws and breaks the whole JSON, making the
        # task look unregistered. Guard both so a paused task still parses.
        f"$nr = if ($i.NextRunTime) {{ $i.NextRunTime.ToString('o') }} else {{ $null }}; "
        f"$lr = if ($i.LastRunTime) {{ $i.LastRunTime.ToString('o') }} else {{ $null }}; "
        f"$trigs = @(); foreach ($tr in $t.Triggers) {{ $trigs += @{{ StartBoundary = $tr.StartBoundary; DaysOfWeek = $tr.DaysOfWeek; Enabled = $tr.Enabled }} }}; "
        f"$out = @{{ State = $t.State.ToString(); NextRunTime = $nr; "
        f"LastRunTime = $lr; LastTaskResult = $i.LastTaskResult; "
        f"Command = $a.Execute; WorkingDirectory = $a.WorkingDirectory; "
        f"Triggers = $trigs }}; "
        f"$out | ConvertTo-Json -Depth 4 -Compress"
    )
    result = _ps(ps)
    if result.returncode != 0:
        return {"registered": False, "error": (result.stderr or result.stdout or "").strip()[:300]}
    try:
        d = json.loads(result.stdout.strip())
        d["registered"] = True
        return d
    except Exception as e:
        return {"registered": False, "error": f"failed to parse PS output: {e}"}


def _same_path(a: str, b: str) -> bool:
    norm = lambda s: os.path.normcase(os.path.normpath((s or "").strip().strip('"')))  # noqa: E731
    return norm(a) == norm(b)


def ensure_task_current(settings: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Self-heal the scheduled task if the install moved/was reinstalled.

    The task bakes an absolute interpreter path + working dir at register time. If
    the app folder is later moved, renamed, or replaced by a fresh install, those
    paths go stale and the 18:55 launch fails silently (the original 'flash and
    die' bug). Called on every GUI launch: if the registered Command/WorkingDirectory
    no longer match the current install — or the interpreter no longer exists — we
    silently re-register so the task always points at a real, current path.

    Returns (healed, message). Only re-registers when there is a registered task
    that has drifted; if nothing is registered yet, it's a no-op (False)."""
    info = get_info()
    if not info.get("registered"):
        # No task at all — e.g. an install-time registration that silently failed.
        # Create it now so simply opening the app always yields a working schedule.
        ok, msg = register(settings)
        return ok, (f"registered task (was missing): {msg}" if ok
                    else f"register (was missing) failed: {msg}")
    if str(info.get("State", "")).lower() == "disabled":
        # User deliberately paused the task — re-registering would re-enable it
        # (build_xml always emits <Enabled>true</Enabled>). Respect the pause.
        return False, "task is paused — skipping self-heal"
    want_cmd = str(_task_python())
    want_wd = str(_project_root())
    have_cmd = info.get("Command") or ""
    have_wd = info.get("WorkingDirectory") or ""
    cmd_exists = bool(have_cmd) and Path(have_cmd.strip().strip('"')).exists()
    if _same_path(have_cmd, want_cmd) and _same_path(have_wd, want_wd) and cmd_exists:
        return False, "task path is current"
    ok, msg = register(settings)
    if ok:
        return True, f"self-healed task path -> {want_cmd}"
    return False, f"self-heal re-register failed: {msg}"


def _describe_triggers(settings: dict[str, Any] | None) -> str:
    s = settings if settings is not None else load_settings()
    days_map = s.get("days") or {}
    days = [d for d in DAY_NAMES if days_map.get(d, {}).get("enabled", False)]
    if not days and "play_days" in s.get("booking", {}):
        days = list(s["booking"]["play_days"])
    parts = []
    for d in days:
        fire_wd = fire_weekday(d)
        fire_day_name = DAY_TO_XML_TAG[fire_wd]
        parts.append(f"{fire_day_name} 18:55 -> books {d.capitalize()}")
    return "; ".join(parts) if parts else "(no play days enabled)"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["register", "unregister", "pause", "resume", "info", "xml"])
    args = ap.parse_args()
    if args.action == "register":
        ok, msg = register()
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.action == "unregister":
        ok, msg = unregister()
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.action == "pause":
        ok, msg = set_paused(True)
        print(msg)
    elif args.action == "resume":
        ok, msg = set_paused(False)
        print(msg)
    elif args.action == "info":
        print(json.dumps(get_info(), indent=2))
    elif args.action == "xml":
        print(build_xml())
