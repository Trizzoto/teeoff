"""Shared runtime hooks between the web server (webapp) and the API (api), kept in a
tiny module to avoid an import cycle. The API can ask the running app to close its
window (e.g. to apply an update) without importing webapp."""
from __future__ import annotations

_edge_proc = None  # the Edge app-window process, set by webapp.main()


def set_edge_proc(proc) -> None:
    global _edge_proc
    _edge_proc = proc


def request_quit() -> None:
    """Close the app window — the server then shuts down and the process exits."""
    p = _edge_proc
    if p is not None:
        try:
            p.terminate()
        except Exception:
            pass
