"""HTTP session wrapper for MiClub."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)


class MiClubClient:
    def __init__(self, base_url: str, username: str, password: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def get(self, path: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self.session.get(self._url(path), **kwargs)

    def post(self, path: str, data=None, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        return self.session.post(self._url(path), data=data, **kwargs)

    def login(self) -> bool:
        # GET to prime any cookies
        self.get("/security/login.msp")
        resp = self.post(
            "/security/login.msp",
            data={"action": "login", "user": self.username, "password": self.password, "Submit": "Login"},
        )
        ok = resp.status_code in (200, 302) and "login" not in resp.url.lower().rsplit("/", 1)[-1]
        if not ok:
            log.error("login failed: status=%s final_url=%s", resp.status_code, resp.url)
        return ok

    def keep_alive(self) -> None:
        """Touch a cheap authed page to keep session warm."""
        self.get("/cms/")

    def fetch_events_json(self, start_date, end_date, resource_id: int):
        """Call the React frontend's JSON API for the event list. Returns list of dicts."""
        # Date format from observed traffic: D-M-YYYY (single-digit day/month)
        s = f"{start_date.day}-{start_date.month}-{start_date.year}"
        e = f"{end_date.day}-{end_date.month}-{end_date.year}"
        ms = int(time.time() * 1000)
        r = self.get(f"/spring/bookings/events/between/{s}/{e}/{resource_id}?time={ms}")
        r.raise_for_status()
        return r.json()

    def fire_get(self, path: str, params: dict[str, str]) -> tuple[float, requests.Response]:
        """Time-stamped GET; returns (epoch_seconds_before_send, response)."""
        url = self._url(path) + "?" + urlencode(params, doseq=True)
        start = time.time()
        resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        return start, resp

    def clone_for_fire(self) -> "MiClubClient":
        """Return a fresh client sharing cookies — used so each fire thread has its own
        TCP connection pool, avoiding stale-connection penalties from the shared prep session."""
        c = MiClubClient(self.base_url, self.username, self.password, timeout=self.timeout)
        c.session.cookies.update(self.session.cookies)
        c.session.headers.update(self.session.headers)
        return c
