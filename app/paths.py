"""Single source of truth for where TeeOff reads/writes runtime data.

The app CODE (python/, app/, booker/) ships in a replaceable install dir that
may be reinstalled, moved, or wiped on update. MUTABLE user data — settings.json,
logs, idempotency markers, last-run status — must live in a FIXED per-user data
dir so it:

  * survives reinstalls/updates (never clobbered by a fresh bundle), and
  * is found regardless of the process's current working directory (the booker
    is launched by Task Scheduler, whose working dir is not guaranteed).

Resolution order:
  1. $TEEOFF_DATA_DIR  (explicit override, used by tests/mock)
  2. %LOCALAPPDATA%\\TeeOff  (normal Windows install)
  3. <project-root>/.teeoff-data  (dev / non-Windows fallback)
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "TeeOff"


def _resolve_data_dir() -> Path:
    override = os.environ.get("TEEOFF_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    # LOCALAPPDATA unset (rare on Windows; or non-Windows dev). NEVER place mutable data
    # inside the install dir — the installer deletes {app} on uninstall, which would wipe
    # settings/logs. Only a raw source checkout (no bundled python/) keeps data beside it.
    root = Path(__file__).resolve().parent.parent
    is_installed_bundle = (root / "python" / "pythonw.exe").exists() or (root / "python" / "python.exe").exists()
    if is_installed_bundle:
        return Path.home() / f".{APP_NAME.lower()}"
    return root / ".teeoff-data"


DATA_DIR = _resolve_data_dir()
LOGS_DIR = DATA_DIR / "logs"
SETTINGS_PATH = DATA_DIR / "settings.json"
LAST_RUN_PATH = DATA_DIR / "last_run.json"


def ensure_dirs() -> None:
    """Create the data + logs dirs if missing. Safe to call repeatedly."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
