"""Build TeeOff-Setup.exe end-to-end.

  1. Rebuild the embeddable-python bundle (build_bundle.py -> dist/TeeOff/).
  2. Compile the Inno Setup installer (installer/teeoff.iss -> dist/TeeOff-Setup.exe).

Run: python build_installer.py
Requires Inno Setup (ISCC.exe). Install once: winget install JRSoftware.InnoSetup
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parent.resolve()
ISS = PROJECT / "installer" / "teeoff.iss"

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path:
    for c in ISCC_CANDIDATES:
        if c.exists():
            return c
    raise SystemExit(
        "ISCC.exe (Inno Setup compiler) not found. Install it once with:\n"
        "  winget install JRSoftware.InnoSetup"
    )


def main() -> None:
    print("=== [1/2] building embeddable bundle ===")
    import build_bundle
    build_bundle.main()

    print("\n=== [2/2] compiling Inno Setup installer ===")
    iscc = find_iscc()
    cmd = [str(iscc), f"/DSrcRoot={PROJECT}", str(ISS)]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    out = PROJECT / "dist" / "TeeOff-Setup.exe"
    size_mb = out.stat().st_size / (1024 * 1024) if out.exists() else 0
    print(f"\nDONE. Installer at: {out}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
