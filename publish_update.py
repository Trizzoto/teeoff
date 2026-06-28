"""Publish a code-only TeeOff update to GitHub Releases.

  python publish_update.py 1.2.1     # bump app/version.py to 1.2.1, then publish
  python publish_update.py           # publish at the current app/version.py

Steps:
  1. (optional) write the new version into app/version.py
  2. zip app/ + booker/ (excluding __pycache__/*.pyc) -> dist/teeoff-update.zip
  3. write dist/manifest.json {version, sha256}
  4. gh release create vX dist/teeoff-update.zip dist/manifest.json

Requires the gh CLI authenticated (gh auth status) and the GitHub repo to exist.
The zip contains CODE ONLY — no bundled python, no settings.seed.json, no credentials.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).parent.resolve()
DIST = PROJECT / "dist"
VERSION_PY = PROJECT / "app" / "version.py"
ZIP_PATH = DIST / "teeoff-update.zip"
MANIFEST = DIST / "manifest.json"
INCLUDE = ("app", "booker")  # code only — never python/, never settings.seed.json
GITHUB_OWNER = "Trizzoto"
GITHUB_REPO = "teeoff"


def read_version() -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', VERSION_PY.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("could not read __version__ from app/version.py")
    return m.group(1)


def write_version(v: str) -> None:
    VERSION_PY.write_text(
        '"""Single source of truth for the app version. Bumped by publish_update.py."""\n'
        f'__version__ = "{v}"\n', encoding="utf-8")


def build_zip() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for top in INCLUDE:
            for p in (PROJECT / top).rglob("*"):
                if "__pycache__" in p.parts or p.suffix == ".pyc":
                    continue
                if p.is_file():
                    z.write(p, p.relative_to(PROJECT).as_posix())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    target = sys.argv[1].lstrip("vV") if len(sys.argv) > 1 else read_version()
    tag = f"v{target}"
    # Abort early if this tag already exists — before mutating version.py / dist.
    existing = subprocess.run(
        ["gh", "release", "view", tag, "-R", f"{GITHUB_OWNER}/{GITHUB_REPO}"],
        capture_output=True, text=True)
    if existing.returncode == 0:
        raise SystemExit(f"release {tag} already exists — bump the version, "
                         f"e.g. `python publish_update.py {target.rsplit('.', 1)[0]}.X`")
    if len(sys.argv) > 1:
        write_version(target)
    version = read_version()
    tag = f"v{version}"
    print(f"=== publishing {tag} ===")
    build_zip()
    digest = sha256(ZIP_PATH)
    MANIFEST.write_text(json.dumps({"version": version, "sha256": digest}, indent=2), encoding="utf-8")
    print(f"  {ZIP_PATH.name}  ({ZIP_PATH.stat().st_size // 1024} KB)  sha256={digest[:16]}…")

    cmd = ["gh", "release", "create", tag, str(ZIP_PATH), str(MANIFEST),
           "-R", f"{GITHUB_OWNER}/{GITHUB_REPO}", "-t", f"TeeOff {tag}",
           "-n", f"TeeOff {tag} — code update. Open the app to install."]
    print("running:", " ".join(cmd))
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("gh release create failed — is the repo created and `gh auth status` OK?")
    print(f"\nDONE. Published {tag}. Grandpa's app offers it on next launch / 'Check for updates'.")


if __name__ == "__main__":
    main()
