"""Build a sharable folder + zip:
    GrandpaGolf/
      python/                  <- embeddable Python 3.11
      app/, booker/            <- our code
      settings.json            <- default settings (grandpa's creds preloaded)
      Start Grandpa Golf.bat   <- double-click launcher
      README.txt
      logs/                    <- created on first run

Run: python build_bundle.py
Output: dist/GrandpaGolf.zip and dist/GrandpaGolf/
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT = Path(__file__).parent
DIST = PROJECT / "dist"
STAGE = DIST / "TeeOff"

PYTHON_VERSION = "3.11.9"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_ZIP = DIST / f"python-{PYTHON_VERSION}-embed.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
GET_PIP = DIST / "get-pip.py"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def download(url: str, target: Path) -> None:
    if target.exists():
        print(f"  already have {target.name}")
        return
    print(f"  downloading {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, target)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    if STAGE.exists():
        step(f"clean {STAGE}")
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    step("download Python embeddable")
    download(PYTHON_URL, PYTHON_ZIP)
    step("download get-pip.py")
    download(GET_PIP_URL, GET_PIP)

    step("extract embeddable Python")
    python_dir = STAGE / "python"
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(PYTHON_ZIP) as z:
        z.extractall(python_dir)

    step("configure python311._pth (enable site-packages + parent project dir + Lib for tkinter)")
    pth_path = python_dir / "python311._pth"
    pth_path.write_text(
        "python311.zip\n"
        ".\n"
        "..\n"
        ".\\Lib\n"
        ".\\Lib\\site-packages\n"
        "import site\n",
        encoding="utf-8",
    )

    step("bootstrap pip")
    subprocess.run([str(python_dir / "python.exe"), str(GET_PIP)], check=True)

    step("copy tkinter + Tcl/Tk from system Python (embeddable doesn't include them)")
    sys_py = Path(r"C:\Users\ruuva\AppData\Local\Programs\Python\Python311")
    if not sys_py.exists():
        raise SystemExit(
            f"Need a regular Python 3.11 install at {sys_py} to copy tkinter from. "
            "Install python 3.11.9 from python.org and rerun."
        )
    # DLLs needed by tkinter
    for fname in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        src = sys_py / "DLLs" / fname
        if src.exists():
            shutil.copy2(src, python_dir / fname)
    # tkinter Python package
    tk_pkg_src = sys_py / "Lib" / "tkinter"
    tk_pkg_dst = python_dir / "Lib" / "tkinter"
    if tk_pkg_dst.exists():
        shutil.rmtree(tk_pkg_dst)
    shutil.copytree(tk_pkg_src, tk_pkg_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Tcl/Tk runtime files
    tcl_src = sys_py / "tcl"
    tcl_dst = python_dir / "tcl"
    if tcl_dst.exists():
        shutil.rmtree(tcl_dst)
    shutil.copytree(tcl_src, tcl_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    step("install runtime dependencies into python/Lib/site-packages")
    subprocess.run(
        [str(python_dir / "python.exe"), "-m", "pip", "install",
         "-r", str(PROJECT / "runtime-requirements.txt"),
         "--no-warn-script-location"],
        check=True,
    )

    step("regenerate icon, badge & nav assets (Pillow -> PNG/ICO)")
    for script_name in ("make_icon.py", "make_badges.py", "make_sidebar_icons.py"):
        script = PROJECT / "scripts" / script_name
        if not script.exists():
            continue
        try:
            subprocess.run([sys.executable, str(script)], check=True, cwd=str(PROJECT))
        except Exception as e:
            print(f"  WARNING: {script_name} failed ({e}); using cached assets")

    step("copy project code")
    for d in ("booker", "app"):
        shutil.copytree(PROJECT / d, STAGE / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    step("write settings.default.json + settings.seed.json")
    # Do NOT ship a live settings.json: runtime settings live in the fixed per-user data
    # dir (app/paths.py), so reinstall/update never wipes grandpa's days/partners/one-offs.
    #  - settings.default.json : reference template, NO credentials (safe, committable).
    #  - settings.seed.json    : carries the real login from .env, used once to seed the
    #    user's settings.json on first install. Lives ONLY inside the installer — it is
    #    gitignored and is never included in update zips. (Build it from .env.)
    import copy as _copy
    from dotenv import dotenv_values
    from app.settings import DEFAULT_SETTINGS
    (STAGE / "settings.default.json").write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding="utf-8")
    env = dotenv_values(PROJECT / ".env")
    seed = _copy.deepcopy(DEFAULT_SETTINGS)
    seed["credentials"]["username"] = env.get("WBP_USERNAME") or os.environ.get("WBP_USERNAME") or ""
    seed["credentials"]["password"] = env.get("WBP_PASSWORD") or os.environ.get("WBP_PASSWORD") or ""
    (STAGE / "settings.seed.json").write_text(json.dumps(seed, indent=2), encoding="utf-8")
    if not seed["credentials"]["username"] or not seed["credentials"]["password"]:
        print("  WARNING: WBP_USERNAME/WBP_PASSWORD missing from .env — installer will have NO login to seed!")

    step("create logs/ folder")
    (STAGE / "logs").mkdir()
    (STAGE / "logs" / ".gitkeep").write_text("", encoding="utf-8")

    step("write Start Tee Off.bat")
    (STAGE / "Start Tee Off.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "start \"\" \"%~dp0python\\pythonw.exe\" -m app.webapp\r\n",
        encoding="utf-8",
    )

    step("write README.txt")
    (STAGE / "README.txt").write_text(
        "TEE OFF\r\n"
        "=======\r\n"
        "Grandpa's automatic golf tee-time booker.\r\n"
        "\r\n"
        "1. Double-click \"Start Tee Off.bat\" to open the app.\r\n"
        "2. On the Schedule tab, verify your member number/password and which days you want to book.\r\n"
        "3. Click \"Save & install schedule\" to install the weekly schedule.\r\n"
        "4. Leave the laptop awake/plugged in on nights before play days (the schedule fires at 6:55pm).\r\n"
        "\r\n"
        "The app does not have to stay open. The Windows Task Scheduler runs the bot at the right time.\r\n"
        "\r\n"
        "If something goes wrong, check the Status tab or open the logs folder.\r\n",
        encoding="utf-8",
    )

    step("zip bundle")
    zip_target = DIST / "TeeOff.zip"
    if zip_target.exists():
        zip_target.unlink()
    shutil.make_archive(str(zip_target.with_suffix("")), "zip", DIST, "TeeOff")
    size_mb = zip_target.stat().st_size / (1024 * 1024)

    print(f"\nDONE. Bundle at: {zip_target}  ({size_mb:.1f} MB)")
    print(f"Unzipped staging at: {STAGE}")


if __name__ == "__main__":
    main()
