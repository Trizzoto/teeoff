"""Legacy entry point.

The interface moved to the web shell (app.webapp) in v1.3.0. This module is kept only
so older desktop shortcuts and any pre-1.3 in-app updater — which relaunch
`python -m app.gui` — still land on the new UI. It no longer imports customtkinter, so
the bundle no longer needs customtkinter or Tcl/Tk.
"""
from __future__ import annotations


def main() -> None:
    from . import webapp
    webapp.main()


if __name__ == "__main__":
    main()
