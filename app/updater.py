"""In-app updater: check GitHub Releases for a newer code-only update and apply it.

Distribution model
------------------
Updates are published as a GitHub Release on GITHUB_OWNER/GITHUB_REPO. Each release is
tagged ``vX.Y.Z`` and carries:
  * ``teeoff-update.zip`` — the new ``app/`` and ``booker/`` code only (NO bundled python,
    NO credentials), and
  * ``manifest.json`` — ``{"version": "X.Y.Z", "sha256": "<hex of the zip>"}``.

The app polls the *latest* release, compares its tag to ``app.version.__version__``, and if
newer downloads the zip, verifies its SHA-256, swaps the new code over the install dir, and
restarts. This avoids re-downloading the large, unsigned full installer — so there is **no
SmartScreen prompt** and updates are tiny and instant.

The bundled Python and third-party libraries are NOT updatable this way; for those rare
changes, ship a fresh ``TeeOff-Setup.exe``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_DIR
from .version import __version__

log = logging.getLogger(__name__)

GITHUB_OWNER = "Trizzoto"
GITHUB_REPO = "teeoff"
API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
UPDATE_ASSET = "teeoff-update.zip"
MANIFEST_ASSET = "manifest.json"
USER_AGENT = f"TeeOff/{__version__}"

# The dir containing app/ and booker/ — i.e. the install root.
INSTALL_DIR = Path(__file__).resolve().parent.parent

# Windows process-creation flags.
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


@dataclass
class UpdateInfo:
    version: str
    url: str
    notes: str
    sha256: str | None


def _parse_version(s: str) -> tuple[int, ...]:
    """Dotted-int parse that ignores any pre-release/build suffix so a release-candidate
    is NOT seen as newer than the final: 'v1.2.0-rc1' -> (1,2,0), '1.2.10' -> (1,2,10)."""
    core = str(s).strip().lstrip("vV").split("-")[0].split("+")[0]
    out: list[int] = []
    for part in core.split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out) or (0,)


def _is_newer(candidate: str, current: str) -> bool:
    a, b = _parse_version(candidate), _parse_version(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_for_update(timeout: float = 8.0) -> UpdateInfo | None:
    """Return UpdateInfo if a newer release exists, else None. Never raises."""
    try:
        data = _get_json(API_LATEST, timeout=timeout)
    except Exception as e:
        log.info("update check failed (offline or no release yet): %s", e)
        return None
    tag = str(data.get("tag_name") or "")
    if not tag or not _is_newer(tag, __version__):
        return None
    url = None
    manifest_url = None
    for a in data.get("assets") or []:
        name = a.get("name")
        if name == UPDATE_ASSET:
            url = a.get("browser_download_url")
        elif name == MANIFEST_ASSET:
            manifest_url = a.get("browser_download_url")
    if not url:
        log.warning("latest release %s has no %s asset", tag, UPDATE_ASSET)
        return None
    sha = None
    if manifest_url:
        # A manifest is advertised — the checksum is REQUIRED. If we can't read it (rate
        # limit / transient error), refuse rather than silently installing unverified code.
        try:
            sha = (_get_json(manifest_url, timeout=timeout) or {}).get("sha256")
        except Exception:
            sha = None
        if not sha:
            log.warning("release %s advertises a manifest but its sha256 is unavailable — "
                        "refusing to offer an unverified update", tag)
            return None
    return UpdateInfo(version=tag.lstrip("vV"), url=url,
                      notes=str(data.get("body") or "").strip(), sha256=sha)


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def download_and_stage(info: UpdateInfo) -> Path:
    """Download + verify + extract the update. Returns the staging dir that contains the
    new app/ and booker/. Raises on failure (caller surfaces the error)."""
    work = DATA_DIR / "update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / UPDATE_ASSET
    _download(info.url, zip_path)
    if info.sha256 and _sha256(zip_path).lower() != info.sha256.lower():
        raise RuntimeError("downloaded update failed checksum verification")
    staging = work / "staging"
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(staging)
    for required in ("app", "booker"):
        if not (staging / required).exists():
            raise RuntimeError(f"update package is missing {required}/ — aborting")
    return staging


# Standalone applier — written to DATA_DIR and run by a detached interpreter so it is NOT
# itself overwritten while it replaces app/ and booker/. Imports nothing from app/.
_APPLIER_SRC = r'''
import sys, time, shutil, subprocess
from pathlib import Path

install = Path(sys.argv[1])
staging = Path(sys.argv[2])
SENTINELS = {"app": "version.py", "booker": "main.py"}

time.sleep(2.0)  # let the GUI fully exit so no files are locked


def swap(name):
    src = staging / name
    if not src.exists():
        return  # nothing staged for this component — leave the existing one in place
    dst = install / name
    new = install / (name + ".new")
    bak = install / (name + ".old")
    # Recovery: if a previous attempt died between moving dst aside and installing new,
    # dst may be missing while the good code sits in .old — restore it before retrying.
    if not dst.exists() and bak.exists():
        try:
            bak.rename(dst)
        except Exception:
            pass
    for _ in range(12):
        try:
            # 1. Build the new tree in a temp sibling FIRST. dst is untouched, so a
            #    partway copytree failure here can NEVER damage the live code.
            shutil.rmtree(new, ignore_errors=True)
            shutil.copytree(src, new)
            # 2. Validate the new tree before swapping it in.
            if not (new / SENTINELS[name]).exists():
                shutil.rmtree(new, ignore_errors=True)
                return  # bad/incomplete package — keep the current code
            # 3. Swap: move current aside, move the validated new into place, drop old.
            shutil.rmtree(bak, ignore_errors=True)
            if dst.exists():
                dst.rename(bak)
            new.rename(dst)
            shutil.rmtree(bak, ignore_errors=True)
            return
        except Exception:
            # Never leave dst missing: restore from backup if the swap half-happened.
            try:
                if not dst.exists() and bak.exists():
                    bak.rename(dst)
            except Exception:
                pass
            shutil.rmtree(new, ignore_errors=True)
            time.sleep(0.5)


for _name in ("app", "booker"):
    swap(_name)

py  = install / "python" / "python.exe"
pyw = install / "python" / "pythonw.exe"
runner = pyw if pyw.exists() else py

# Refresh the scheduled task in case the booker/scheduler changed in this update.
try:
    subprocess.run([str(py if py.exists() else runner), "-m", "app.scheduler", "register"],
                   cwd=str(install), creationflags=0x08000000, timeout=60)
except Exception:
    pass

# Relaunch the GUI on the new version.
try:
    subprocess.Popen([str(runner), "-m", "app.gui"], cwd=str(install), creationflags=0x00000008)
except Exception:
    pass
'''


def apply_and_restart(staging: Path) -> None:
    """Spawn a detached helper that waits for THIS process to exit, swaps in the new code,
    refreshes the task, and relaunches the GUI. The caller MUST then exit the app promptly."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    applier = DATA_DIR / "_apply_update.py"
    applier.write_text(_APPLIER_SRC, encoding="utf-8")
    py = INSTALL_DIR / "python" / "python.exe"
    pyw = INSTALL_DIR / "python" / "pythonw.exe"
    runner = pyw if pyw.exists() else (py if py.exists() else Path(sys.executable))
    subprocess.Popen(
        [str(runner), str(applier), str(INSTALL_DIR), str(staging)],
        cwd=str(DATA_DIR),
        creationflags=_DETACHED_PROCESS | _CREATE_NO_WINDOW,
        close_fds=True,
    )
