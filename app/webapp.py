"""TeeOff desktop app — modern web UI hosted in a chrome-less Edge app window.

The interface is static HTML/CSS/JS in app/webui/, served by a tiny local HTTP server
bound to 127.0.0.1 (never exposed to the network). The Python backend is reached via a
small JSON API under /api/ (see app/api.py). The window is just a console — the real
booking runs headless via the Windows scheduled task (booker/), open window or not.
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api
from .paths import DATA_DIR

WEBUI_DIR = Path(__file__).parent / "webui"
EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_edge() -> str | None:
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence the default stderr logging
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            return self._api("GET", path)
        return self._static(path)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            return self._api("POST", path, raw)
        self._send(404, b"not found", "text/plain")

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEBUI_DIR / rel).resolve()
        # Path-traversal guard: must stay inside WEBUI_DIR.
        if not (target == WEBUI_DIR or WEBUI_DIR in target.parents) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _api(self, method: str, path: str, raw: bytes = b"") -> None:
        route = path[len("/api/"):]
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            result = api.dispatch(method, route, payload)
            self._send(200, json.dumps(result).encode("utf-8"), "application/json")
        except Exception as e:  # noqa: BLE001 — report any handler error as JSON
            self._send(500, json.dumps({"error": str(e)}).encode("utf-8"), "application/json")


def _self_heal() -> None:
    """On launch, repair the scheduled task if the install moved/was reinstalled."""
    try:
        from .scheduler import ensure_task_current
        ensure_task_current()
    except Exception:
        pass


def main() -> None:
    threading.Thread(target=_self_heal, daemon=True).start()
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    edge = _find_edge()
    if edge:
        profile = DATA_DIR / "webview-profile"
        profile.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([
            edge,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1200,820",
        ])
        from . import _runtime
        _runtime.set_edge_proc(proc)
        proc.wait()  # block until the app window is closed
    else:
        import webbrowser
        webbrowser.open(url)
        try:
            input()  # keep the server alive (dev fallback when Edge is absent)
        except EOFError:
            pass
    server.shutdown()


if __name__ == "__main__":
    main()
