"""Grandpa Golf desktop app — sidebar dashboard UI.

Architecture notes:
  * UI thread is never blocked by subprocess / network: a single background worker
    polls scheduler state every 20s and pushes results onto a queue the UI reads.
  * All views are CTkFrame subclasses; the App swaps the visible one.
  * Settings persisted to settings.json via app.settings.save_settings.
"""
from __future__ import annotations

import calendar as _calendar
import json
import logging
import math
import os
import queue
import re
import smtplib
import subprocess
import sys
import threading
import time as _time
from copy import deepcopy
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from tkinter import Canvas, StringVar, IntVar, BooleanVar, messagebox

import tkinter as tk
import customtkinter as ctk

from . import updater
from .bookings import BookingsFetcher
from .partners import PartnerFetcher, merge_into_settings
from .scheduler import TASK_NAME, ensure_task_current, get_info, register, set_paused
from .version import __version__
from .settings import (
    DAY_NAMES, DEFAULT_SETTINGS, fire_weekday, load_settings, save_settings,
)

log = logging.getLogger(__name__)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("green")

from .paths import DATA_DIR, LAST_RUN_PATH, LOGS_DIR  # noqa: F401  fixed user-data dir (survives reinstalls)

PROJECT_ROOT = Path(__file__).parent.parent
LATEST_TXT = LOGS_DIR / "latest.txt"
ASSETS_DIR = Path(__file__).parent / "assets"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_PNG_96 = ASSETS_DIR / "icon-96.png"
ICON_PNG_128 = ASSETS_DIR / "icon-128.png"
BADGES_DIR = ASSETS_DIR / "badges"
NAV_DIR = ASSETS_DIR / "nav"

SLOT_TIME_OPTIONS = [f"{h:02d}:{m:02d}" for h in range(6, 11) for m in (0, 6, 12, 18, 24, 30, 36, 42, 48, 54)]
DAY_FRIENDLY = {d: d.capitalize() for d in DAY_NAMES}
DAY_ABBREV = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ============================================================================
# Theme — green / golf course palette
# ============================================================================

# Fairway green primary, forest accent. Cards stay clean white/dark to keep
# content legible against the green chrome.

def color_app_bg():            return ("#f4f8f5", "#0f1612")        # main content area
def color_sidebar_bg():        return ("#e6efe9", "#0a1410")        # subtle green-tinted sidebar
def color_card_bg():           return ("#ffffff", "#1a221d")
def color_card_border():       return ("#d4e3d8", "#1f3328")
def color_card_subtle_bg():    return ("#f0f6f2", "#141d18")        # for nested rows
def color_text_secondary():    return ("#5e6b62", "#8fa395")
def color_accent():            return ("#1f7a4d", "#3fb950")        # fairway green
def color_accent_hover():      return ("#185d3a", "#34a04c")
def color_success():           return ("#1f7a4d", "#3fb950")
def color_planned():           return ("#3066c4", "#5085d8")        # cool blue for planned (distinct from booked-green)
def color_warning():           return ("#9a6700", "#d29922")
def color_danger():            return ("#cf222e", "#f85149")


def appearance_color(t):
    """Resolve a (light, dark) tuple based on current appearance mode."""
    mode = ctk.get_appearance_mode()
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return t[0] if mode == "Light" else t[1]
    return t


# ============================================================================
# Status badge — circular icon used in the calendar (booked / planned / failed)
# ============================================================================

class StatusBadge(ctk.CTkLabel):
    """Pre-rendered PNG badge (booked / failed / planned) with drop-shadow + glyph.
    Falls back to a coloured-dot label if the asset is missing."""

    _photo_cache: dict[Path, tk.PhotoImage] = {}
    _available_sizes = (14, 16, 18, 22, 24, 28, 32)

    def __init__(self, parent, kind: str, size: int = 24, bg=None, **kwargs):
        # Snap to a size we've rendered (nearest)
        nearest = min(self._available_sizes, key=lambda x: abs(x - size))
        path = BADGES_DIR / f"{kind}-{nearest}.png"
        if path.exists():
            if path not in self._photo_cache:
                self._photo_cache[path] = tk.PhotoImage(file=str(path))
            super().__init__(parent, image=self._photo_cache[path], text="", **kwargs)
        else:
            colors = {
                "booked":  color_success(),
                "failed":  color_danger(),
                "planned": color_text_secondary(),
            }
            super().__init__(parent, text="●", text_color=colors.get(kind, color_text_secondary()),
                             font=ctk.CTkFont(size=max(14, size - 4)), **kwargs)


# ============================================================================
# Flaming golf ball — pre-rendered PNG (much higher quality than canvas draw)
# ============================================================================

class FlamingBallIcon(ctk.CTkLabel):
    """Loads the pre-rendered icon-{size}.png from app/assets/.

    Falls back to a canvas drawing if the asset is missing (defensive)."""

    _photo_cache: dict[Path, tk.PhotoImage] = {}

    def __init__(self, parent, size: int = 96, **kwargs):
        candidate = ASSETS_DIR / f"icon-{size}.png"
        if not candidate.exists():
            # Try the closest size we have
            available = sorted(p for p in ASSETS_DIR.glob("icon-*.png"))
            candidate = available[0] if available else None
        if candidate is not None and candidate.exists():
            if candidate not in self._photo_cache:
                self._photo_cache[candidate] = tk.PhotoImage(file=str(candidate))
            super().__init__(parent, image=self._photo_cache[candidate], text="", **kwargs)
        else:
            # Asset missing — render a placeholder
            super().__init__(parent, text="🔥⛳", font=ctk.CTkFont(size=int(size / 2)),
                             text_color=color_accent(), **kwargs)


# ============================================================================
# (legacy) canvas-drawn flaming ball — kept available for tinkering / fallback
# ============================================================================

class GolfBall(Canvas):
    """Hand-drawn flaming golf ball on a Tk Canvas.
    The PNG-based FlamingBallIcon above looks dramatically better; we keep
    this around as a fallback if the assets folder is missing."""

    def __init__(self, parent, size: int = 72, bg=None, **kwargs):
        bg_color = appearance_color(bg) if bg is not None else appearance_color(color_sidebar_bg())
        super().__init__(parent, width=size, height=size, bg=bg_color,
                         highlightthickness=0, bd=0, **kwargs)
        self._size = size
        self._draw()

    def _draw(self) -> None:
        s = self._size
        cx = s / 2
        # Push the ball slightly below the canvas centre so flames have room above
        ball_cy = s * 0.58
        ball_r = s * 0.34

        # Flame palette
        red = "#d52a0e"
        orange = "#ff6a13"
        yellow = "#ffc433"
        bright = "#fff7a6"

        # --- LAYER 1: outer red flame envelope (background) ---
        self._flame(cx, ball_cy, s, scale=1.0, color=red)

        # --- LAYER 2: inner orange flame ---
        self._flame(cx, ball_cy, s, scale=0.75, color=orange, jitter_seed=11)

        # --- LAYER 3: yellow core flame ---
        self._flame(cx, ball_cy, s, scale=0.50, color=yellow, jitter_seed=23)

        # --- LAYER 4: bright white-yellow hot spots ---
        self.create_oval(cx - s*0.05, ball_cy - s*0.25,
                         cx + s*0.05, ball_cy - s*0.10,
                         fill=bright, outline="")
        self.create_oval(cx - s*0.12, ball_cy - s*0.05,
                         cx - s*0.04, ball_cy + s*0.05,
                         fill=bright, outline="")

        # --- LAYER 5: ball shadow ---
        sh = max(1, s // 28)
        self.create_oval(cx - ball_r + sh, ball_cy - ball_r + sh,
                         cx + ball_r + sh, ball_cy + ball_r + sh,
                         fill="#000000", outline="", stipple="gray12")

        # --- LAYER 6: ball body ---
        self.create_oval(cx - ball_r, ball_cy - ball_r,
                         cx + ball_r, ball_cy + ball_r,
                         fill="#ffffff", outline="#a8a8a8", width=max(1, s // 48))

        # --- LAYER 7: ball highlight (top-left) ---
        hl_r = ball_r * 0.55
        self.create_oval(cx - ball_r * 0.6, ball_cy - ball_r * 0.55,
                         cx - ball_r * 0.6 + hl_r, ball_cy - ball_r * 0.55 + hl_r,
                         fill="#fbfbfb", outline="")

        # --- LAYER 8: dimples ---
        dimple_r = max(1, s // 26)
        dimple_color = "#dcdcdc"
        # ring of 6 around the visible face
        for i in range(6):
            ang = math.radians(45 + i * 60)
            x = cx + ball_r * 0.55 * math.cos(ang)
            y = ball_cy + ball_r * 0.55 * math.sin(ang)
            self.create_oval(x - dimple_r, y - dimple_r,
                             x + dimple_r, y + dimple_r,
                             fill=dimple_color, outline="")
        # inner small cluster
        for dx, dy in [(-0.12, -0.10), (0.15, 0.05), (0.0, 0.15)]:
            x = cx + ball_r * dx
            y = ball_cy + ball_r * dy
            self.create_oval(x - dimple_r, y - dimple_r,
                             x + dimple_r, y + dimple_r,
                             fill=dimple_color, outline="")

        # --- LAYER 9: foreground flame tongues licking up over the ball edges ---
        # Two small bright orange tongues at the top corners of the ball
        for sign in (-1, 1):
            base_x = cx + sign * ball_r * 0.55
            tip_x = cx + sign * ball_r * 0.85
            self.create_polygon([
                base_x - s*0.04, ball_cy - ball_r * 0.5,
                base_x + s*0.04, ball_cy - ball_r * 0.8,
                tip_x,           ball_cy - ball_r * 1.05,
                base_x + s*0.06, ball_cy - ball_r * 0.55,
                base_x,          ball_cy - ball_r * 0.4,
            ], fill=orange, outline="", smooth=True)
        # main central tongue at the top
        self.create_polygon([
            cx - s*0.06, ball_cy - ball_r * 0.95,
            cx - s*0.02, ball_cy - ball_r * 1.25,
            cx + s*0.04, ball_cy - ball_r * 1.45,
            cx + s*0.08, ball_cy - ball_r * 1.15,
            cx + s*0.06, ball_cy - ball_r * 0.9,
        ], fill=yellow, outline="", smooth=True)

    def _flame(self, cx, cy, s, scale, color, jitter_seed=None):
        """Draw a stylised flame envelope centred at (cx, cy), scale 0..1."""
        # Asymmetric teardrop with multiple tongues at the top
        h = s * 0.55 * scale       # vertical reach above cy
        w = s * 0.42 * scale       # half-width at base
        pts = [
            # Base (around the ball, fat)
            cx - w,           cy + s * 0.22 * scale,
            cx - w * 1.05,    cy + s * 0.05 * scale,
            cx - w * 0.95,    cy - h * 0.15,
            # Left tongue
            cx - w * 0.75,    cy - h * 0.50,
            cx - w * 0.45,    cy - h * 0.30,
            cx - w * 0.55,    cy - h * 0.70,
            cx - w * 0.25,    cy - h * 0.55,
            # Main central tongue (tallest)
            cx - w * 0.10,    cy - h * 0.90,
            cx + w * 0.05,    cy - h * 1.00,
            cx + w * 0.18,    cy - h * 0.85,
            # Right tongue
            cx + w * 0.30,    cy - h * 0.95,
            cx + w * 0.30,    cy - h * 0.55,
            cx + w * 0.55,    cy - h * 0.75,
            cx + w * 0.50,    cy - h * 0.30,
            cx + w * 0.80,    cy - h * 0.45,
            cx + w * 0.95,    cy - h * 0.10,
            # Base right
            cx + w * 1.05,    cy + s * 0.05 * scale,
            cx + w,           cy + s * 0.22 * scale,
        ]
        self.create_polygon(pts, fill=color, outline="", smooth=True)


# ============================================================================
# Reusable widgets
# ============================================================================

class Card(ctk.CTkFrame):
    def __init__(self, parent, title: str | None = None, **kwargs):
        super().__init__(parent, fg_color=color_card_bg(), corner_radius=10,
                         border_width=1, border_color=color_card_border(), **kwargs)
        if title:
            ctk.CTkLabel(self, text=title, anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(
                fill="x", padx=16, pady=(12, 4))


class SectionHeader(ctk.CTkLabel):
    def __init__(self, parent, text: str, **kwargs):
        super().__init__(parent, text=text, anchor="w",
                         font=ctk.CTkFont(size=22, weight="bold"), **kwargs)


class Subtle(ctk.CTkLabel):
    def __init__(self, parent, text: str, **kwargs):
        super().__init__(parent, text=text, anchor="w", text_color=color_text_secondary(), **kwargs)


# ============================================================================
# Status fetcher (background thread, non-blocking)
# ============================================================================

class StatusFetcher:
    def __init__(self, refresh_seconds: float = 20.0) -> None:
        self.refresh_seconds = refresh_seconds
        self.queue: queue.Queue[dict] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self._wake = threading.Event()

    def start(self) -> None:
        self.thread.start()

    def request_refresh(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self.stop_event.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                info = get_info()
            except Exception as e:
                info = {"registered": False, "error": str(e)}
            self.queue.put(info)
            self._wake.wait(timeout=self.refresh_seconds)
            self._wake.clear()


# ============================================================================
# Sidebar
# ============================================================================

class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, on_nav, **kwargs):
        super().__init__(parent, fg_color=color_sidebar_bg(), corner_radius=0, width=210, **kwargs)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.on_nav = on_nav

        # Brand header — flaming golf ball over the name + subtitle
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(20, 18))
        ball = FlamingBallIcon(header, size=96)
        ball.pack(pady=(0, 6))
        ctk.CTkLabel(header, text="Tee Off",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=color_accent()).pack()
        ctk.CTkLabel(header, text="Golf Booker",
                     font=ctk.CTkFont(size=11),
                     text_color=color_text_secondary()).pack()

        self.items = [
            ("dashboard", "Dashboard"),
            ("schedule", "Schedule"),
            ("account", "Account"),
            ("test", "Test"),
            ("logs", "Logs"),
        ]
        self.buttons: dict[str, ctk.CTkButton] = {}
        self.icon_photos: dict[str, dict[str, tk.PhotoImage]] = {}
        for i, (key, label) in enumerate(self.items, start=1):
            # Load active/inactive icons for this nav item
            self.icon_photos[key] = {}
            for state in ("active", "inactive"):
                p = NAV_DIR / f"{key}-{state}.png"
                if p.exists():
                    self.icon_photos[key][state] = tk.PhotoImage(file=str(p))
            inactive_img = self.icon_photos[key].get("inactive")
            btn = ctk.CTkButton(
                self, text=label, anchor="w", height=36,
                font=ctk.CTkFont(size=13),
                fg_color="transparent", text_color=("#1a2a20", "#e8efe9"),
                hover_color=("#d8e6dd", "#15231b"),
                image=inactive_img, compound="left",
                command=lambda k=key: self.on_nav(k),
            )
            btn.grid(row=i, column=0, sticky="ew", padx=8, pady=2)
            self.buttons[key] = btn

        # status pill area at bottom
        self.grid_rowconfigure(99, weight=1)
        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=100, column=0, sticky="sew", padx=12, pady=12)
        self.status_dot = ctk.CTkLabel(self.status_frame, text="●", text_color=color_text_secondary(),
                                       font=ctk.CTkFont(size=14))
        self.status_dot.grid(row=0, column=0, sticky="w")
        self.status_text = ctk.CTkLabel(self.status_frame, text="Loading…", anchor="w",
                                        font=ctk.CTkFont(size=11))
        self.status_text.grid(row=0, column=1, sticky="w", padx=(6, 0))

    def set_active(self, key: str) -> None:
        for k, btn in self.buttons.items():
            icons = self.icon_photos.get(k, {})
            if k == key:
                btn.configure(fg_color=("#c8dccf", "#1f3328"),
                              text_color=color_accent(),
                              image=icons.get("active") or icons.get("inactive"))
            else:
                btn.configure(fg_color="transparent",
                              text_color=("#1a2a20", "#e8efe9"),
                              image=icons.get("inactive") or icons.get("active"))

    def set_status(self, dot_color, text: str) -> None:
        self.status_dot.configure(text_color=dot_color)
        self.status_text.configure(text=text)


# ============================================================================
# Views
# ============================================================================

class View(ctk.CTkScrollableFrame):
    def __init__(self, app: "App"):
        super().__init__(app.main_area, fg_color="transparent")
        self.app = app

    def on_show(self) -> None:
        pass

    def on_status_update(self, info: dict) -> None:
        pass


class DashboardView(View):
    def __init__(self, app):
        super().__init__(app)
        # Two-column grid: left = status + next bookings stack (fixed width),
        # right = calendar (grows to fill).
        self.grid_columnconfigure(0, weight=0, minsize=360)
        self.grid_columnconfigure(1, weight=1, minsize=480)
        self.grid_rowconfigure(1, weight=1)

        SectionHeader(self, "Dashboard").grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 12))

        # === LEFT COLUMN ===
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(4, 8))
        left.grid_columnconfigure(0, weight=1)

        # Status card
        self.status_card = Card(left, title="Status")
        self.status_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        status_row = ctk.CTkFrame(self.status_card, fg_color="transparent")
        status_row.pack(fill="x", padx=16, pady=(2, 4))
        self.status_dot = ctk.CTkLabel(status_row, text="●", text_color=color_text_secondary(),
                                       font=ctk.CTkFont(size=18))
        self.status_dot.pack(side="left")
        self.status_label = ctk.CTkLabel(status_row, text="Checking schedule…", anchor="w",
                                         font=ctk.CTkFont(size=15, weight="bold"))
        self.status_label.pack(side="left", padx=(8, 0))
        self.status_sub = Subtle(self.status_card, text="")
        self.status_sub.pack(fill="x", padx=16, pady=(0, 8))

        # Last-run result — the zero-config safety net. Even with email off, opening
        # the app shows whether the most recent scheduled run booked, failed, or crashed.
        self.lastrun_row = ctk.CTkFrame(self.status_card, fg_color="transparent")
        self.lastrun_row.pack(fill="x", padx=16, pady=(0, 6))
        self.lastrun_dot = ctk.CTkLabel(self.lastrun_row, text="●",
                                        text_color=color_text_secondary(), font=ctk.CTkFont(size=14))
        self.lastrun_dot.pack(side="left", anchor="n")
        self.lastrun_label = ctk.CTkLabel(self.lastrun_row, text="Checking last run…", anchor="w",
                                          justify="left", wraplength=300, font=ctk.CTkFont(size=12))
        self.lastrun_label.pack(side="left", padx=(6, 0))

        # Warning shown when there is no remote safety net (email alerts disabled).
        self.email_warn = ctk.CTkLabel(self.status_card, text="", anchor="w", justify="left",
                                       wraplength=320, text_color=color_warning(),
                                       font=ctk.CTkFont(size=11))
        self.email_warn.pack(fill="x", padx=16, pady=(0, 8))

        # Manual "check for updates" (auto-check also runs on launch).
        self.btn_update = ctk.CTkButton(self.status_card, text="Check for updates", height=28,
                                        fg_color="transparent", border_width=1,
                                        command=lambda: self.app._check_updates_async(manual=True))
        self.btn_update.pack(fill="x", padx=16, pady=(0, 12))

        # Grid-based 50/50 button row so CTkButton's default width can't override our layout.
        action_row = ctk.CTkFrame(self.status_card, fg_color="transparent")
        action_row.pack(fill="x", padx=16, pady=(0, 14))
        action_row.grid_columnconfigure(0, weight=1, uniform="btn")
        action_row.grid_columnconfigure(1, weight=1, uniform="btn")
        self.btn_pause = ctk.CTkButton(action_row, text="Pause", width=0,
                                       command=self._toggle_pause)
        self.btn_pause.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_refresh = ctk.CTkButton(action_row, text="Refresh", width=0,
                                         fg_color="transparent", border_width=1,
                                         command=self.app.status_fetcher.request_refresh)
        self.btn_refresh.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # Next bookings card
        self.next_card = Card(left, title="Next bookings")
        self.next_card.grid(row=1, column=0, sticky="ew")
        self.next_list = ctk.CTkFrame(self.next_card, fg_color="transparent")
        self.next_list.pack(fill="x", padx=12, pady=(0, 12))

        # === RIGHT COLUMN ===
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 4))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self.calendar_card = CalendarCard(right, self.app)
        self.calendar_card.grid(row=0, column=0, sticky="nsew")

        # Sync-status row below the calendar
        self.sync_row = ctk.CTkFrame(right, fg_color="transparent")
        self.sync_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.sync_label = ctk.CTkLabel(self.sync_row, text="Pulling bookings from website…",
                                       text_color=color_text_secondary(), anchor="w",
                                       font=ctk.CTkFont(size=11))
        self.sync_label.pack(side="left")
        self.sync_btn = ctk.CTkButton(self.sync_row, text="Refresh from website", width=160,
                                      fg_color="transparent", border_width=1,
                                      command=self.app.bookings_fetcher.request_refresh)
        self.sync_btn.pack(side="right")

        self.calendar_card.refresh()

        # (Last booking summary no longer needs its own card — the calendar shows it,
        # and the popup on any green cell shows full details.)
        self.last_label = None  # kept for back-compat with on_status_update
        self._refresh_last_run()

    def _refresh_last_run(self) -> None:
        """Read the booker's last_run.json and surface OK/FAILED/CRASHED prominently.
        This is what makes a silent overnight failure visible the next time the app
        is opened — critical because email alerts are off by default."""
        rec = None
        try:
            if LAST_RUN_PATH.exists():
                rec = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
        except Exception:
            rec = None
        if not rec:
            self.lastrun_dot.configure(text_color=color_text_secondary())
            self.lastrun_label.configure(text="No booking run recorded yet.")
        else:
            status = (rec.get("status") or "").lower()
            when = rec.get("time_adelaide") or rec.get("time_local") or ""
            try:
                when = datetime.fromisoformat(when).strftime("%a %d %b %I:%M %p").replace(" 0", " ")
            except Exception:
                pass
            summary = (rec.get("summary") or "").strip()
            if status == "ok":
                self.lastrun_dot.configure(text_color=color_success())
                self.lastrun_label.configure(text=f"Last run OK — {summary}\n{when}".rstrip())
            elif status == "fail":
                self.lastrun_dot.configure(text_color=color_danger())
                self.lastrun_label.configure(text=f"Last run FAILED — {summary}\n{when}".rstrip())
            else:
                self.lastrun_dot.configure(text_color=color_danger())
                self.lastrun_label.configure(
                    text=f"Last run CRASHED — {summary}\n{when}\nSee the Logs tab.".rstrip())
        email_on = bool(self.app.settings.get("email", {}).get("enabled"))
        self.email_warn.configure(
            text="" if email_on else "⚠ Email alerts are off — results only show here in the app.")

    def _toggle_pause(self) -> None:
        info = self.app.last_info
        if not info.get("registered"):
            messagebox.showinfo("Not installed", "No schedule installed yet. Go to Schedule to install.")
            return
        currently_disabled = info.get("State", "").lower() == "disabled"

        def _runner():
            ok, msg = set_paused(not currently_disabled)
            self.after(0, lambda: (messagebox.showinfo("Schedule", msg if ok else f"Failed: {msg}"),
                                   self.app.status_fetcher.request_refresh()))
        threading.Thread(target=_runner, daemon=True).start()
        # Optimistically update the button label
        self.btn_pause.configure(text="Resume" if not currently_disabled else "Pause")

    def on_status_update(self, info: dict) -> None:
        # Status card
        if not info.get("registered"):
            self.status_dot.configure(text_color=color_warning())
            self.status_label.configure(text="Not installed")
            self.status_sub.configure(text="Click \"Save & install\" on the Schedule tab to set up.")
            self.btn_pause.configure(state="disabled")
        else:
            state = info.get("State", "?")
            if state.lower() == "disabled":
                self.status_dot.configure(text_color=color_warning())
                self.status_label.configure(text="Paused")
                self.btn_pause.configure(text="Resume")
            elif state.lower() == "running":
                self.status_dot.configure(text_color=color_accent())
                self.status_label.configure(text="Running now")
                self.btn_pause.configure(text="Pause")
            else:
                self.status_dot.configure(text_color=color_success())
                self.status_label.configure(text="Ready")
                self.btn_pause.configure(text="Pause")
            self.btn_pause.configure(state="normal")

            n_triggers = len(info.get("Triggers", []))
            self.status_sub.configure(text=f"{n_triggers} weekly trigger(s) installed")

        # Next fires
        for w in self.next_list.winfo_children():
            w.destroy()
        upcoming = compute_upcoming_fires(self.app.settings, count=4)
        if not upcoming or not info.get("registered"):
            ctk.CTkLabel(self.next_list, text="(no upcoming bookings — turn on at least one day on the Schedule tab)",
                         text_color=color_text_secondary(), anchor="w").pack(fill="x", padx=4, pady=8)
        else:
            for f in upcoming:
                self._render_upcoming(f)

        # Last-run safety-net status (OK / FAILED / CRASHED)
        self._refresh_last_run()

        # Last booking — only update if the legacy label still exists
        if self.last_label is not None:
            if LATEST_TXT.exists():
                body = LATEST_TXT.read_text(encoding="utf-8", errors="replace").strip()
                self.last_label.configure(text=body[:1500] if body else "(empty)")
            else:
                self.last_label.configure(text="(no bookings yet)")
        # NOTE: don't refresh the calendar here. Status updates come every 20s and
        # rebuilding 42 cells caused a visible flicker. Calendar redraws only when
        # something that affects its data changes:
        #   - month nav buttons (CalendarCard._shift / _today)
        #   - bookings fetcher posts new data (on_bookings_update)
        #   - one-off added/removed (DayDetailDialog._reschedule_in_background)
        #   - settings saved on the Schedule tab

    def on_bookings_update(self, data: dict) -> None:
        bookings = data.get("bookings", [])
        self.calendar_card.live_bookings = bookings
        self.calendar_card.refresh()
        user = data.get("user") or "(name pending)"
        fetched_at = data.get("fetched_at", "")
        if fetched_at:
            try:
                t = datetime.fromisoformat(fetched_at).strftime("%I:%M %p").lstrip("0")
            except Exception:
                t = fetched_at
            self.sync_label.configure(text=f"On-site bookings synced at {t} — {len(bookings)} found ({user})")
        else:
            self.sync_label.configure(text=f"{len(bookings)} on-site bookings found ({user})")

    def _render_upcoming(self, f: dict) -> None:
        row = ctk.CTkFrame(self.next_list, fg_color=color_card_subtle_bg(),
                           corner_radius=8, border_width=1, border_color=color_card_border())
        row.pack(fill="x", padx=4, pady=4)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=12, pady=10)

        ctk.CTkLabel(left, text=f["fire_pretty"], anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(left, text=f"Books {f['play_pretty']} at {f['target_time']}",
                     anchor="w", text_color=color_text_secondary()).pack(anchor="w")

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", padx=12)
        ctk.CTkLabel(right, text=f["in_str"], font=ctk.CTkFont(size=12),
                     text_color=color_accent()).pack(anchor="e")


class ScheduleView(View):
    def __init__(self, app):
        super().__init__(app)
        self.grid_columnconfigure(0, weight=1)

        SectionHeader(self, "Schedule").grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 4))
        Subtle(self, "Pick which days to book and what time to target on each.").grid(
            row=1, column=0, sticky="ew", padx=4, pady=(0, 12))

        # Days card
        self.days_card = Card(self, title="Days & target times")
        self.days_card.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        grid = ctk.CTkFrame(self.days_card, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(grid, text="Day", anchor="w", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=color_text_secondary()).grid(row=0, column=1, sticky="w", padx=(8, 24))
        ctk.CTkLabel(grid, text="Target time", anchor="w", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=color_text_secondary()).grid(row=0, column=2, sticky="w", padx=(0, 24))
        ctk.CTkLabel(grid, text="Fires on", anchor="w", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=color_text_secondary()).grid(row=0, column=3, sticky="w")

        self.var_day_enabled: dict[str, BooleanVar] = {}
        self.var_day_time: dict[str, StringVar] = {}
        FIRE_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        for i, d in enumerate(DAY_NAMES, start=1):
            day_setting = app.settings["days"][d]
            v_en = BooleanVar(value=day_setting["enabled"])
            self.var_day_enabled[d] = v_en
            v_time = StringVar(value=day_setting["target_time"])
            self.var_day_time[d] = v_time

            cb = ctk.CTkCheckBox(grid, text="", variable=v_en, width=24,
                                 command=self._on_day_toggle)
            cb.grid(row=i, column=0, sticky="w", pady=4)
            ctk.CTkLabel(grid, text=DAY_FRIENDLY[d], anchor="w", width=110).grid(row=i, column=1, sticky="w", padx=(8, 24))
            ctk.CTkOptionMenu(grid, values=SLOT_TIME_OPTIONS, variable=v_time, width=100).grid(row=i, column=2, sticky="w", padx=(0, 24))
            fire_dow = fire_weekday(d)
            fire_name = FIRE_NAMES[fire_dow]
            ctk.CTkLabel(grid, text=f"{fire_name} 6:55 PM", anchor="w",
                         text_color=color_text_secondary()).grid(row=i, column=3, sticky="w")

        # Fallback card
        self.fb_card = Card(self, title="If the target slot is taken")
        self.fb_card.grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        fb_grid = ctk.CTkFrame(self.fb_card, fg_color="transparent")
        fb_grid.pack(fill="x", padx=16, pady=(0, 12))

        self.var_fb_dir = StringVar(value=app.settings["booking"]["fallback_direction"])
        ctk.CTkLabel(fb_grid, text="Walk", anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkRadioButton(fb_grid, text="earlier", variable=self.var_fb_dir, value="earlier").grid(row=0, column=1, sticky="w", padx=(0, 12))
        ctk.CTkRadioButton(fb_grid, text="later", variable=self.var_fb_dir, value="later").grid(row=0, column=2, sticky="w")

        self.var_fb_earliest = StringVar(value=app.settings["booking"]["fallback_earliest"])
        self.var_fb_latest = StringVar(value=app.settings["booking"]["fallback_latest"])
        ctk.CTkLabel(fb_grid, text="Stop at earliest", anchor="w").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkOptionMenu(fb_grid, values=SLOT_TIME_OPTIONS, variable=self.var_fb_earliest, width=100).grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(fb_grid, text="latest", anchor="w").grid(row=1, column=2, sticky="w", padx=(12, 6))
        ctk.CTkOptionMenu(fb_grid, values=SLOT_TIME_OPTIONS, variable=self.var_fb_latest, width=100).grid(row=1, column=3, sticky="w")

        # Playing partners (replaces the old, never-wired-up "Group size" dropdown).
        # Pulled live from MiClub so grandpa doesn't have to retype names — he just
        # ticks who plays this round. Default = all enabled, matching his MiClub list.
        self.partners_card = Card(self, title="Playing partners")
        self.partners_card.grid(row=4, column=0, sticky="ew", padx=4, pady=4)
        self.partners_body = ctk.CTkFrame(self.partners_card, fg_color="transparent")
        self.partners_body.pack(fill="x", padx=16, pady=(0, 12))
        self.var_partner_day_enabled: dict[tuple[str, str], BooleanVar] = {}
        self._partner_render()

        # Save button
        save_btn = ctk.CTkButton(self, text="Save & install schedule", height=44,
                                 font=ctk.CTkFont(size=14, weight="bold"), command=self._save)
        save_btn.grid(row=5, column=0, sticky="ew", padx=4, pady=(16, 16))

    def _on_day_toggle(self) -> None:
        # Day columns are derived from enabled days, so re-render the matrix
        # whenever any day checkbox flips.
        if hasattr(self, "partners_body"):
            self._partner_render()

    def _partner_render(self) -> None:
        for w in self.partners_body.winfo_children():
            w.destroy()
        self.var_partner_day_enabled = {}  # (pid, day_name) → BooleanVar

        partners = self.app.settings.get("partners", [])
        # Day columns come from whatever's currently ticked in the days grid above
        # so the matrix never wastes space on days he isn't playing.
        enabled_days = [d for d in DAY_NAMES if self.var_day_enabled[d].get()]

        if not partners:
            ctk.CTkLabel(self.partners_body,
                         text="No partner list loaded yet. Click Refresh to pull grandpa's default\n"
                              "partners from the website.",
                         text_color=color_text_secondary(), anchor="w",
                         justify="left").pack(fill="x", pady=(0, 8))
        elif not enabled_days:
            ctk.CTkLabel(self.partners_body,
                         text="Tick at least one day above to choose who plays on it.",
                         text_color=color_text_secondary(), anchor="w").pack(fill="x", pady=(0, 8))
        else:
            ctk.CTkLabel(self.partners_body,
                         text="Tick each partner on the days they're playing. Untick on days they sit out.",
                         text_color=color_text_secondary(), anchor="w",
                         font=ctk.CTkFont(size=11)).pack(fill="x", pady=(0, 8))

            grid = ctk.CTkFrame(self.partners_body, fg_color="transparent")
            grid.pack(fill="x")

            # Header row
            ctk.CTkLabel(grid, text="", width=160).grid(row=0, column=0, sticky="w")
            short = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
                     "thursday": "Thu", "friday": "Fri", "saturday": "Sat", "sunday": "Sun"}
            for c, d in enumerate(enabled_days, start=1):
                ctk.CTkLabel(grid, text=short[d], width=42,
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=color_text_secondary()).grid(row=0, column=c, padx=2, pady=(0, 4))

            # One row per partner
            for r, p in enumerate(partners, start=1):
                pid = p["id"]
                is_self = bool(p.get("is_self"))
                label_text = p["full_name"]
                if is_self:
                    label_text += "  (you)"
                ctk.CTkLabel(grid, text=label_text, anchor="w", width=160).grid(
                    row=r, column=0, sticky="w", pady=2)

                saved_days = set(p.get("playing_days") or [])
                # v32 migration: if no playing_days but an "enabled" key exists,
                # treat True as "all days", False as "no days"
                if "playing_days" not in p and "enabled" in p:
                    saved_days = set(DAY_NAMES) if bool(p["enabled"]) else set()

                for c, d in enumerate(enabled_days, start=1):
                    v = BooleanVar(value=(True if is_self else (d in saved_days)))
                    self.var_partner_day_enabled[(pid, d)] = v
                    cb = ctk.CTkCheckBox(grid, text="", variable=v, width=24,
                                         state=("disabled" if is_self else "normal"))
                    cb.grid(row=r, column=c, padx=2, pady=2)

        # Refresh row (always visible)
        refresh_row = ctk.CTkFrame(self.partners_body, fg_color="transparent")
        refresh_row.pack(fill="x", pady=(8, 0))
        self.partner_status_lbl = ctk.CTkLabel(refresh_row, text="", anchor="w",
                                               text_color=color_text_secondary(),
                                               font=ctk.CTkFont(size=11))
        self.partner_status_lbl.pack(side="left")
        self.partner_refresh_btn = ctk.CTkButton(
            refresh_row, text="Refresh partner list", width=160, height=28,
            fg_color="transparent", border_width=1, command=self._partner_refresh)
        self.partner_refresh_btn.pack(side="right")

    def _partner_refresh(self) -> None:
        if self.app.partner_fetcher.in_progress:
            return
        self.partner_refresh_btn.configure(state="disabled", text="Refreshing…")
        self.partner_status_lbl.configure(text="Talking to the website…")

        def _done(fresh, err):
            def _apply():
                self.partner_refresh_btn.configure(state="normal", text="Refresh partner list")
                if err or fresh is None:
                    self.partner_status_lbl.configure(text=err or "No partner list returned.")
                    return
                if merge_into_settings(self.app.settings, fresh):
                    save_settings(self.app.settings)
                self.partner_status_lbl.configure(text=f"Loaded {len(fresh)} from website.")
                self._partner_render()
            self.after(0, _apply)
        self.app.partner_fetcher.fetch(_done)

    def on_partner_data_updated(self) -> None:
        """Called by App when a background partner fetch finishes (first-launch auto-fetch)."""
        self._partner_render()

    def _save(self) -> None:
        new_settings = deepcopy(self.app.settings)
        for d in DAY_NAMES:
            new_settings["days"][d]["enabled"] = bool(self.var_day_enabled[d].get())
            new_settings["days"][d]["target_time"] = self.var_day_time[d].get()
        new_settings["booking"]["fallback_direction"] = self.var_fb_dir.get()
        new_settings["booking"]["fallback_earliest"] = self.var_fb_earliest.get()
        new_settings["booking"]["fallback_latest"] = self.var_fb_latest.get()
        # Persist per-day partner ticks. Grandpa (is_self) always plays every day.
        enabled_days_for_partners = [d for d in DAY_NAMES if self.var_day_enabled[d].get()]
        for p in new_settings.get("partners", []):
            if p.get("is_self"):
                p["playing_days"] = list(DAY_NAMES)
                p.pop("enabled", None)
                continue
            existing = set(p.get("playing_days") or [])
            for d in enabled_days_for_partners:
                v = self.var_partner_day_enabled.get((p["id"], d))
                if v is None:
                    continue
                if v.get():
                    existing.add(d)
                else:
                    existing.discard(d)
            p["playing_days"] = [d for d in DAY_NAMES if d in existing]
            p.pop("enabled", None)

        enabled = [d for d in DAY_NAMES if new_settings["days"][d]["enabled"]]
        if not enabled:
            messagebox.showwarning("No days selected", "Pick at least one day to book.")
            return

        save_settings(new_settings)
        self.app.settings = new_settings
        # Calendar's planned blue dots may have changed — refresh once
        dash = self.app.views.get("dashboard")
        if dash is not None:
            dash.calendar_card.refresh()

        def _runner():
            ok, msg = register(new_settings)
            self.after(0, lambda: (
                messagebox.showinfo("Saved", "Settings saved.\n\n" + msg) if ok else messagebox.showerror("Schedule failed", msg),
                self.app.status_fetcher.request_refresh(),
            ))
        threading.Thread(target=_runner, daemon=True).start()


class AccountView(View):
    def __init__(self, app):
        super().__init__(app)
        self.grid_columnconfigure(0, weight=1)

        SectionHeader(self, "Account").grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))

        # Club credentials
        self.club_card = Card(self, title="Club credentials")
        self.club_card.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        club_grid = ctk.CTkFrame(self.club_card, fg_color="transparent")
        club_grid.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(club_grid, text=app.settings["club"]["name"], anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ctk.CTkLabel(club_grid, text="Member number", anchor="w", width=130).grid(row=1, column=0, sticky="w", pady=4)
        self.var_username = StringVar(value=app.settings["credentials"]["username"])
        ctk.CTkEntry(club_grid, textvariable=self.var_username, width=200).grid(row=1, column=1, sticky="w")

        ctk.CTkLabel(club_grid, text="Password", anchor="w", width=130).grid(row=2, column=0, sticky="w", pady=4)
        self.var_password = StringVar(value=app.settings["credentials"]["password"])
        self.pw_entry = ctk.CTkEntry(club_grid, textvariable=self.var_password, show="*", width=200)
        self.pw_entry.grid(row=2, column=1, sticky="w")
        self.var_show = BooleanVar(value=False)
        ctk.CTkCheckBox(club_grid, text="Show", variable=self.var_show, width=20,
                        command=self._toggle_pw).grid(row=2, column=2, sticky="w", padx=(8, 0))

        # Email
        self.email_card = Card(self, title="Email notifications")
        self.email_card.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        em_grid = ctk.CTkFrame(self.email_card, fg_color="transparent")
        em_grid.pack(fill="x", padx=16, pady=(0, 12))

        self.var_em_enabled = BooleanVar(value=app.settings["email"]["enabled"])
        ctk.CTkCheckBox(em_grid, text="Send a summary email after each booking",
                        variable=self.var_em_enabled).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ctk.CTkLabel(em_grid, text="Send to", anchor="w", width=130).grid(row=1, column=0, sticky="w", pady=4)
        self.var_notify_to = StringVar(value=app.settings["email"]["notify_to"])
        ctk.CTkEntry(em_grid, textvariable=self.var_notify_to, width=260).grid(row=1, column=1, columnspan=2, sticky="w")

        ctk.CTkLabel(em_grid, text="Gmail (FROM)", anchor="w", width=130).grid(row=2, column=0, sticky="w", pady=4)
        self.var_smtp_user = StringVar(value=app.settings["email"]["smtp_user"])
        ctk.CTkEntry(em_grid, textvariable=self.var_smtp_user, width=260).grid(row=2, column=1, columnspan=2, sticky="w")

        ctk.CTkLabel(em_grid, text="App password", anchor="w", width=130).grid(row=3, column=0, sticky="w", pady=4)
        self.var_smtp_pw = StringVar(value=app.settings["email"]["smtp_app_password"])
        ctk.CTkEntry(em_grid, textvariable=self.var_smtp_pw, show="*", width=180).grid(row=3, column=1, sticky="w")
        ctk.CTkButton(em_grid, text="Test email", width=100, command=self._send_test).grid(row=3, column=2, sticky="w", padx=(8, 0))

        Subtle(em_grid, text="Gmail app passwords: https://myaccount.google.com/apppasswords").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        save_btn = ctk.CTkButton(self, text="Save settings", height=44,
                                 font=ctk.CTkFont(size=14, weight="bold"), command=self._save)
        save_btn.grid(row=3, column=0, sticky="ew", padx=4, pady=(16, 16))

    def _toggle_pw(self) -> None:
        self.pw_entry.configure(show="" if self.var_show.get() else "*")

    def _save(self) -> None:
        new_settings = deepcopy(self.app.settings)
        new_settings["credentials"]["username"] = self.var_username.get().strip()
        new_settings["credentials"]["password"] = self.var_password.get()
        new_settings["email"]["enabled"] = bool(self.var_em_enabled.get())
        new_settings["email"]["notify_to"] = self.var_notify_to.get().strip()
        new_settings["email"]["smtp_user"] = self.var_smtp_user.get().strip()
        new_settings["email"]["smtp_app_password"] = self.var_smtp_pw.get().strip().replace(" ", "")
        save_settings(new_settings)
        self.app.settings = new_settings
        messagebox.showinfo("Saved", "Account settings saved.")

    def _send_test(self) -> None:
        smtp_user = self.var_smtp_user.get().strip()
        smtp_pw = self.var_smtp_pw.get().strip().replace(" ", "")
        notify_to = self.var_notify_to.get().strip() or smtp_user
        if not smtp_user or not smtp_pw:
            messagebox.showwarning("Email not configured", "Fill in Gmail address and app password first.")
            return

        def _send():
            try:
                msg = EmailMessage()
                msg["Subject"] = "Tee Off — test email"
                msg["From"] = smtp_user
                msg["To"] = notify_to
                msg.set_content("This is a test from the Tee Off desktop app.\n\nEmail settings work.")
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
                    s.starttls()
                    s.login(smtp_user, smtp_pw)
                    s.send_message(msg)
                self.after(0, lambda: messagebox.showinfo("Email sent", f"Test email sent to {notify_to}. Check your inbox."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Email failed", f"Could not send: {e}"))

        threading.Thread(target=_send, daemon=True).start()


class TestView(View):
    def __init__(self, app):
        super().__init__(app)
        self.grid_columnconfigure(0, weight=1)

        SectionHeader(self, "Test").grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 4))
        Subtle(self, "Runs the full booking flow against the live site BUT WILL NOT actually book anything.").grid(
            row=1, column=0, sticky="ew", padx=4, pady=(0, 12))

        controls = Card(self)
        controls.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        inner = ctk.CTkFrame(controls, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(inner, text="Target day").pack(side="left", padx=(0, 12))
        self.var_target = StringVar(value="wednesday")
        ctk.CTkOptionMenu(inner, values=DAY_NAMES, variable=self.var_target, width=140).pack(side="left", padx=(0, 16))
        self.btn = ctk.CTkButton(inner, text="Run safe test", width=160, command=self._run)
        self.btn.pack(side="left")

        out_card = Card(self, title="Output")
        out_card.grid(row=3, column=0, sticky="nsew", padx=4, pady=(4, 16))
        self.grid_rowconfigure(3, weight=1)
        self.output = ctk.CTkTextbox(out_card, height=380, font=("Consolas", 11))
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _run(self) -> None:
        self.btn.configure(state="disabled", text="Running…")
        self.output.delete("1.0", "end")
        target = self.var_target.get()
        self.output.insert("end", f"Running safe-mode test for {target}...\n\n")

        def _runner():
            try:
                python_exe = python_for_subprocess()
                cmd = [str(python_exe), "-m", "booker", "--target", target, "--dont-fire", "--dry-fire-in", "20"]
                env = os.environ.copy()
                env["USE_MOCK"] = "false"
                proc = subprocess.Popen(
                    cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, env=env,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.after(0, lambda l=line: (self.output.insert("end", l), self.output.see("end")))
                proc.wait(timeout=180)
            except Exception as e:
                self.after(0, lambda: self.output.insert("end", f"\n[error] {e}\n"))
            finally:
                self.after(0, lambda: self.btn.configure(state="normal", text="Run safe test"))

        threading.Thread(target=_runner, daemon=True).start()


class LogsView(View):
    def __init__(self, app):
        super().__init__(app)
        self.grid_columnconfigure(0, weight=1)
        SectionHeader(self, "Logs").grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 8))
        ctk.CTkButton(row, text="Open logs folder", command=self._open).pack(side="left")
        ctk.CTkButton(row, text="Refresh", fg_color="transparent", border_width=1,
                      command=self._refresh).pack(side="left", padx=(8, 0))

        self.history_card = Card(self, title="Recent runs")
        self.history_card.grid(row=2, column=0, sticky="nsew", padx=4, pady=(4, 16))
        self.grid_rowconfigure(2, weight=1)
        self.list_frame = ctk.CTkScrollableFrame(self.history_card, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 12))

    def on_show(self) -> None:
        self._refresh()

    def _open(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(LOGS_DIR))  # type: ignore[attr-defined]

    def _refresh(self) -> None:
        for w in self.list_frame.winfo_children():
            w.destroy()
        if not LOGS_DIR.exists():
            ctk.CTkLabel(self.list_frame, text="(no logs yet)", text_color=color_text_secondary()).pack(anchor="w", padx=8, pady=8)
            return
        log_files = sorted(LOGS_DIR.glob("run-*.log"), reverse=True)[:30]
        if not log_files:
            ctk.CTkLabel(self.list_frame, text="(no logs yet)", text_color=color_text_secondary()).pack(anchor="w", padx=8, pady=8)
            return
        for lf in log_files:
            try:
                txt = lf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            success = "OK —" in txt or "OK send=" in txt
            color = color_success() if success else color_danger()
            first_outcome = next((line for line in txt.splitlines() if line.startswith("[")), "(unknown)")
            row = ctk.CTkFrame(self.list_frame, fg_color=color_card_subtle_bg(),
                               corner_radius=8, border_width=1, border_color=color_card_border())
            row.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(row, text="●", text_color=color, font=ctk.CTkFont(size=14)).pack(side="left", padx=(12, 8), pady=8)
            text_frame = ctk.CTkFrame(row, fg_color="transparent")
            text_frame.pack(side="left", fill="x", expand=True, pady=8)
            ctk.CTkLabel(text_frame, text=lf.stem.replace("run-", ""), anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(text_frame, text=first_outcome, anchor="w",
                         text_color=color_text_secondary(), font=("Consolas", 10)).pack(anchor="w")


# ============================================================================
# Helpers
# ============================================================================

def compute_upcoming_fires(settings: dict, count: int = 4) -> list[dict]:
    """Return next N scheduled fire times based on enabled play days."""
    out: list[dict] = []
    now = datetime.now()
    for d in DAY_NAMES:
        if not settings["days"][d]["enabled"]:
            continue
        target_t = settings["days"][d]["target_time"]
        fire_wd = fire_weekday(d)
        days_ahead = (fire_wd - now.weekday()) % 7
        fire_dt = (now + timedelta(days=days_ahead)).replace(hour=18, minute=55, second=0, microsecond=0)
        if days_ahead == 0 and now > fire_dt:
            fire_dt += timedelta(days=7)
        play_dt = fire_dt + timedelta(days=15)
        out.append({
            "fire_dt": fire_dt,
            "play_dt": play_dt,
            "play_day": d,
            "target_time": target_t,
        })
    out.sort(key=lambda x: x["fire_dt"])
    out = out[:count]
    for f in out:
        delta = f["fire_dt"] - now
        days = delta.days
        hours = (delta.seconds // 3600)
        mins = (delta.seconds % 3600) // 60
        if days > 0:
            in_str = f"in {days}d {hours}h"
        elif hours > 0:
            in_str = f"in {hours}h {mins}m"
        else:
            in_str = f"in {mins}m"
        f["fire_pretty"] = f["fire_dt"].strftime("%a %d %b, %-I:%M %p") if sys.platform != "win32" else f["fire_dt"].strftime("%a %d %b, %#I:%M %p")
        f["play_pretty"] = f["play_dt"].strftime("%a %d %b")
        f["in_str"] = in_str
    return out


def python_for_subprocess() -> Path:
    bundled = PROJECT_ROOT / "python" / "python.exe"
    if bundled.exists():
        return bundled
    venv = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return venv
    return Path(sys.executable)


# ============================================================================
# Booking-event discovery for the calendar
# ============================================================================

_MARKER_RE = re.compile(r"^booked-(\d{4}-\d{2}-\d{2})-(\w+)\.flag$")
_LOG_DATE_FROM_NAME = re.compile(r"^run-(\d{8})-(\d{6})$")


def collect_calendar_events(live_bookings: list[dict] | None = None) -> dict[date, list[dict]]:
    """Walk logs/ and the schedule to build a date -> events map.

    Each event is {"kind": "booked|failed|planned", "label": str, "detail": str}.
    `live_bookings` are pulled from the actual website (bookings cache); they take
    priority over marker files because they reflect real server state.
    """
    events: dict[date, list[dict]] = {}

    # Past successes from idempotency markers (this app's own bookings)
    if LOGS_DIR.exists():
        for f in LOGS_DIR.glob("booked-*.flag"):
            m = _MARKER_RE.match(f.name)
            if not m:
                continue
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            day_name = m.group(2).capitalize()
            slot = f.read_text(encoding="utf-8", errors="replace").strip() or "?"
            events.setdefault(d, []).append({
                "kind": "booked",
                "label": f"{day_name} {slot}",
                "detail": f"Booked {slot} on {d.strftime('%a %d %b %Y')}",
                "source": "marker",
            })

    # Live bookings from the site (overrides marker info — these are ground truth)
    if live_bookings:
        for b in live_bookings:
            try:
                d = date.fromisoformat(b["date"])
            except (ValueError, KeyError):
                continue
            slot = b.get("time", "?")
            partners = b.get("partners", [])
            detail = f"On-site booking: {slot} on {d.strftime('%a %d %b %Y')}"
            if partners:
                detail += "\n  with " + ", ".join(p for p in partners if p)
            # Remove any marker-source entry on this date — site truth wins
            day_events = events.setdefault(d, [])
            day_events[:] = [e for e in day_events if e.get("source") != "marker"]
            day_events.append({
                "kind": "booked",
                "label": slot,
                "detail": detail,
                "source": "live",
            })

    # Past failures: scan logs for [Day] FAIL lines (skip ones that match a booked marker)
    if LOGS_DIR.exists():
        booked_dates = set(events.keys())
        for lf in LOGS_DIR.glob("run-*.log"):
            try:
                txt = lf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # Try to find fire target ISO datetime in the body
            m = re.search(r"fire target (\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):", txt)
            if not m:
                continue
            try:
                fire_date = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            play_date = fire_date + timedelta(days=15)
            if play_date in booked_dates:
                continue  # we know it succeeded
            if "FAIL" in txt:
                # Find which day-of-week label
                day_match = re.search(r"\[(\w+)\] FAIL", txt)
                day_label = day_match.group(1) if day_match else "?"
                events.setdefault(play_date, []).append({
                    "kind": "failed",
                    "label": f"{day_label} FAIL",
                    "detail": f"Booking attempt failed on {play_date.strftime('%a %d %b %Y')}",
                })

    return events


def planned_play_dates(settings: dict, until: date | None = None,
                       weeks_ahead: int = 10) -> dict[date, dict]:
    """Return {play_date: {"day": name, "target_time": "HH:MM", "fire_dt": datetime}}
    for enabled days whose fire is still in the future.

    Bookings open 15 days before play at ~19:00:03. We only flag a date as
    'planned' if the fire moment for it is still ahead of now — otherwise it's
    either already booked (green) or already missed.

    Generates every enabled weekday from today up to and including `until`. When
    `until` is None it falls back to `weeks_ahead` weeks from today. The calendar
    passes the last visible day so dots appear for whatever month is in view,
    however far in the future.
    """
    out: dict[date, dict] = {}
    now = datetime.now()
    today = now.date()
    if until is None:
        until = today + timedelta(weeks=weeks_ahead)
    if until < today:
        return out
    for d_name in DAY_NAMES:
        if not settings["days"][d_name].get("enabled"):
            continue
        target_time = settings["days"][d_name]["target_time"]
        day_wd = DAY_NAMES.index(d_name)
        days_ahead = (day_wd - today.weekday()) % 7
        play_date = today + timedelta(days=days_ahead)
        while play_date <= until:
            fire_dt = datetime.combine(play_date - timedelta(days=15),
                                       datetime.min.time().replace(hour=18, minute=55))
            if fire_dt > now:
                out[play_date] = {"day": d_name, "target_time": target_time, "fire_dt": fire_dt}
            play_date += timedelta(weeks=1)
    return out


# ============================================================================
# Calendar widget
# ============================================================================

class DayDetailDialog(ctk.CTkToplevel):
    """Popup window for one calendar day. Shows events + supports one-off scheduling."""

    def __init__(self, parent, day: date, events: list[dict], planned_info: dict | None, app: "App"):
        super().__init__(parent)
        self.title(day.strftime("%A %d %B %Y"))
        self.geometry("480x420")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.app = app
        self.day = day
        self.events = events
        self.planned_info = planned_info
        self.adding_one_off = False  # toggled when form is shown

        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        self.button_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.button_bar.pack(fill="x", padx=16, pady=12)

        self._render()

    # --- helpers ---
    def _existing_one_off(self) -> dict | None:
        for oo in self.app.settings.get("one_offs", []):
            if oo.get("play_date") == self.day.isoformat():
                return oo
        return None

    def _can_add_one_off(self) -> tuple[bool, str]:
        """Return (allowed, why_not_msg)."""
        if any(e["kind"] == "booked" for e in self.events):
            return False, "This day is already booked on the website."
        if self.planned_info:
            return False, "This day is already covered by your recurring schedule."
        today = date.today()
        fire_date = self.day - timedelta(days=15)
        if fire_date < today:
            return False, "Bookings for this day have already opened — book it on the website directly."
        if fire_date == today and datetime.now().hour >= 19:
            return False, "Today's fire time has passed."
        if self._existing_one_off() is not None:
            return False, "A one-off booking is already scheduled for this day."
        return True, ""

    # --- render ---
    def _clear(self) -> None:
        for w in self.container.winfo_children():
            w.destroy()
        for w in self.button_bar.winfo_children():
            w.destroy()

    def _render(self) -> None:
        self._clear()
        # Title
        ctk.CTkLabel(self.container, text=self.day.strftime("%A %d %B %Y"), anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(fill="x", pady=(0, 8))

        # Existing events
        if not self.events and not self.planned_info and not self._existing_one_off():
            ctk.CTkLabel(self.container,
                         text="Nothing scheduled or booked on this day.",
                         text_color=color_text_secondary(), anchor="w").pack(fill="x", pady=(0, 6))
        for ev in self.events:
            self._render_event_card(ev)

        # Planned recurring
        has_booked = any(e["kind"] == "booked" for e in self.events)
        if self.planned_info and not has_booked:
            self._render_planned_card(self.planned_info)

        # One-off (if exists)
        existing_oo = self._existing_one_off()
        if existing_oo is not None:
            self._render_one_off_card(existing_oo)

        # Add-one-off form OR Add-one-off button
        if self.adding_one_off:
            self._render_add_form()
        else:
            allowed, why = self._can_add_one_off()
            if allowed:
                ctk.CTkButton(self.button_bar, text="+ Add one-off booking", width=200,
                              command=self._start_add).pack(side="left")
            elif why and not existing_oo and not has_booked and not self.planned_info:
                ctk.CTkLabel(self.container, text=why, anchor="w",
                             text_color=color_text_secondary(), wraplength=420).pack(fill="x", pady=(8, 0))
            ctk.CTkButton(self.button_bar, text="Close", width=100,
                          fg_color="transparent", border_width=1,
                          command=self.destroy).pack(side="right")

    def _render_event_card(self, ev: dict) -> None:
        kind = ev["kind"]
        color = CalendarCard.DOT_COLORS.get(kind, color_text_secondary())
        title_text = {"booked": "✓ Booked", "failed": "✗ Booking failed", "planned": "• Planned"}.get(kind, kind.capitalize())
        card = ctk.CTkFrame(self.container, fg_color=color_card_subtle_bg(),
                            corner_radius=8, border_width=1, border_color=color_card_border())
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent"); head.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="●", text_color=color, font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(head, text=title_text, anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(6, 0))
        if ev.get("detail"):
            ctk.CTkLabel(card, text=ev["detail"], anchor="w", justify="left",
                         text_color=color_text_secondary(), wraplength=420).pack(
                fill="x", padx=14, pady=(0, 8))

    def _render_planned_card(self, p: dict) -> None:
        card = ctk.CTkFrame(self.container, fg_color=color_card_subtle_bg(),
                            corner_radius=8, border_width=1, border_color=color_card_border())
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent"); head.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="●", text_color=CalendarCard.DOT_COLORS["planned"],
                     font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(head, text="Planned (recurring)", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(6, 0))
        fire_dt: datetime = p["fire_dt"]
        fmt_time = fire_dt.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
        txt = (f"Target time: {p['target_time']}\n"
               f"Booker will fire: {fmt_time}\n"
               f"Day: {p['day'].capitalize()}")
        ctk.CTkLabel(card, text=txt, anchor="w", justify="left",
                     text_color=color_text_secondary()).pack(fill="x", padx=14, pady=(0, 8))

    def _render_one_off_card(self, oo: dict) -> None:
        fire_date = self.day - timedelta(days=15)
        fire_dt = datetime.combine(fire_date, datetime.min.time().replace(hour=18, minute=55))
        card = ctk.CTkFrame(self.container, fg_color=color_card_subtle_bg(),
                            corner_radius=8, border_width=1, border_color=color_card_border())
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent"); head.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(head, text="●", text_color=CalendarCard.DOT_COLORS["planned"],
                     font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(head, text="Planned (one-off)", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(6, 0))
        fmt = fire_dt.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
        info = (f"Target time: {oo['target_time']}\n"
                f"Booker will fire: {fmt}")
        ctk.CTkLabel(card, text=info, anchor="w", justify="left",
                     text_color=color_text_secondary()).pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkButton(card, text="Remove one-off", width=140, fg_color="transparent",
                      border_width=1, text_color=color_danger(),
                      command=self._remove_one_off).pack(anchor="w", padx=14, pady=(0, 10))

    def _render_add_form(self) -> None:
        fire_date = self.day - timedelta(days=15)
        card = ctk.CTkFrame(self.container, fg_color=color_card_subtle_bg(),
                            corner_radius=8, border_width=1, border_color=color_accent())
        card.pack(fill="x", pady=4)
        ctk.CTkLabel(card, text="Schedule one-off booking", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(card,
                     text=f"Will fire {fire_date.strftime('%a %d %b')} at 6:55 PM",
                     anchor="w", text_color=color_text_secondary()).pack(fill="x", padx=14)

        row = ctk.CTkFrame(card, fg_color="transparent"); row.pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(row, text="Target time").pack(side="left", padx=(0, 12))
        self.var_one_off_time = StringVar(value="08:12")
        ctk.CTkOptionMenu(row, values=SLOT_TIME_OPTIONS, variable=self.var_one_off_time,
                          width=120).pack(side="left")

        ctk.CTkButton(self.button_bar, text="Schedule", width=120,
                      command=self._save_one_off).pack(side="left")
        ctk.CTkButton(self.button_bar, text="Cancel", width=100, fg_color="transparent",
                      border_width=1, command=self._cancel_add).pack(side="left", padx=(8, 0))
        ctk.CTkButton(self.button_bar, text="Close", width=100, fg_color="transparent",
                      border_width=1, command=self.destroy).pack(side="right")

    # --- actions ---
    def _start_add(self) -> None:
        self.adding_one_off = True
        self._render()

    def _cancel_add(self) -> None:
        self.adding_one_off = False
        self._render()

    def _save_one_off(self) -> None:
        target_time = self.var_one_off_time.get()
        new_settings = deepcopy(self.app.settings)
        new_settings.setdefault("one_offs", []).append({
            "play_date": self.day.isoformat(),
            "target_time": target_time,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        save_settings(new_settings)
        self.app.settings = new_settings
        self.adding_one_off = False
        self._reschedule_in_background(success_msg=f"One-off scheduled for {self.day.strftime('%a %d %b')} at {target_time}.")

    def _remove_one_off(self) -> None:
        new_settings = deepcopy(self.app.settings)
        new_settings["one_offs"] = [
            oo for oo in new_settings.get("one_offs", [])
            if oo.get("play_date") != self.day.isoformat()
        ]
        save_settings(new_settings)
        self.app.settings = new_settings
        self._reschedule_in_background(success_msg="One-off removed.")

    def _reschedule_in_background(self, success_msg: str) -> None:
        def _runner():
            ok, msg = register(self.app.settings)
            def _after():
                if ok:
                    messagebox.showinfo("Updated", success_msg + "\n\n" + msg)
                else:
                    messagebox.showerror("Schedule update failed", msg)
                self.app.status_fetcher.request_refresh()
                # Refresh the calendar in the dashboard
                dash = self.app.views.get("dashboard")
                if dash is not None:
                    dash.calendar_card.refresh()
                self.destroy()
            self.after(0, _after)
        threading.Thread(target=_runner, daemon=True).start()


class CalendarCard(Card):
    """Month-grid calendar with color-coded dots for booked / planned / failed."""

    DOT_COLORS = {
        "booked":  ("#1f7a4d", "#3fb950"),   # green (matches theme)
        "planned": ("#3066c4", "#5085d8"),   # cool blue (distinct from booked-green)
        "failed":  ("#cf222e", "#f85149"),   # red
    }
    WEEKDAY_HEADERS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, parent, app: "App"):
        super().__init__(parent)
        self.app = app
        today = date.today()
        self.year = today.year
        self.month = today.month
        self.live_bookings: list[dict] = []

        # Header with month label and nav arrows
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 6))
        self.month_label = ctk.CTkLabel(header, text="",
                                        font=ctk.CTkFont(size=15, weight="bold"))
        self.month_label.pack(side="left")
        ctk.CTkButton(header, text="›", width=32, fg_color="transparent",
                      border_width=1, command=lambda: self._shift(1)).pack(side="right")
        ctk.CTkButton(header, text="‹", width=32, fg_color="transparent",
                      border_width=1, command=lambda: self._shift(-1)).pack(side="right", padx=(0, 4))
        ctk.CTkButton(header, text="Today", width=68, fg_color="transparent",
                      border_width=1, command=self._today).pack(side="right", padx=(0, 8))

        # Legend with badges matching the calendar cells
        legend = ctk.CTkFrame(self, fg_color="transparent")
        legend.pack(fill="x", padx=16, pady=(0, 4))
        for kind, label in [("booked", "Booked"), ("planned", "Planned"), ("failed", "Failed")]:
            StatusBadge(legend, kind, size=16, bg=color_card_bg()).pack(side="left", padx=(0, 4))
            ctk.CTkLabel(legend, text=label, font=ctk.CTkFont(size=11),
                         text_color=color_text_secondary()).pack(side="left", padx=(0, 14))

        # Grid container
        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for c in range(7):
            self.grid_frame.grid_columnconfigure(c, weight=1, uniform="cal")

    def _shift(self, months: int) -> None:
        m = self.month + months
        y = self.year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self.month, self.year = m, y
        self.refresh()

    def _today(self) -> None:
        t = date.today()
        self.year, self.month = t.year, t.month
        self.refresh()

    def refresh(self) -> None:
        for w in self.grid_frame.winfo_children():
            w.destroy()

        # Month label
        month_name = _calendar.month_name[self.month]
        self.month_label.configure(text=f"{month_name} {self.year}")

        # Weekday header row
        for c, name in enumerate(self.WEEKDAY_HEADERS):
            ctk.CTkLabel(self.grid_frame, text=name, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=color_text_secondary(), anchor="center").grid(
                row=0, column=c, sticky="ew", pady=(0, 4))

        # Build a list of weeks of date objects, weeks starting Monday
        cal = _calendar.Calendar(firstweekday=0)  # Monday
        month_weeks = cal.monthdatescalendar(self.year, self.month)

        # Data sources
        past_events = collect_calendar_events(live_bookings=self.live_bookings)
        planned = planned_play_dates(self.app.settings, until=month_weeks[-1][-1])
        today = date.today()

        # One-off bookings live in settings, not in planned_play_dates — collect their
        # play dates so they get a "planned" dot just like recurring days.
        one_off_dates: set[date] = set()
        for oo in self.app.settings.get("one_offs", []):
            try:
                one_off_dates.add(date.fromisoformat(oo["play_date"]))
            except (ValueError, KeyError, TypeError):
                continue

        normal_bg_in = ("#fbfdfb", "#141a16")
        normal_bg_out = ("#eef2ef", "#0d1411")
        hover_bg = ("#e1efe7", "#1d2c24")
        past_bg = ("#eef2ef", "#0d1411")              # muted/greyed-out past days
        past_hover = ("#e1efe7", "#1d2c24")

        # Configure rows to stretch evenly so cells fill the calendar's height
        for r_i in range(1, len(month_weeks) + 1):
            self.grid_frame.grid_rowconfigure(r_i, weight=1, uniform="cal-row")

        for r, week in enumerate(month_weeks, start=1):
            for c, d in enumerate(week):
                in_month = (d.month == self.month)
                is_today = (d == today)
                is_past = (d < today) and in_month
                day_events = past_events.get(d, [])
                planned_info = planned.get(d) if (d in planned and d >= today) else None

                if is_past:
                    cell_bg = past_bg
                    hover_for_cell = past_hover
                else:
                    cell_bg = normal_bg_in if in_month else normal_bg_out
                    hover_for_cell = hover_bg

                cell_border = color_accent() if is_today else color_card_border()

                cell = ctk.CTkFrame(
                    self.grid_frame, fg_color=cell_bg, corner_radius=8,
                    border_width=2 if is_today else 1, border_color=cell_border,
                    height=78,
                )
                cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                cell.grid_propagate(False)
                cell.grid_columnconfigure(0, weight=1)
                cell.grid_rowconfigure(0, weight=0)
                cell.grid_rowconfigure(1, weight=1)

                num_color = ("black", "white") if in_month else color_text_secondary()
                if is_past:
                    num_color = color_text_secondary()
                if is_today:
                    num_color = color_accent()
                num_label = ctk.CTkLabel(cell, text=str(d.day),
                                         font=ctk.CTkFont(size=12, weight=("bold" if is_today else "normal")),
                                         text_color=num_color, anchor="w",
                                         fg_color="transparent")
                num_label.grid(row=0, column=0, sticky="nw", padx=8, pady=(6, 0))

                # Determine which single badge to show (priority: booked > failed > planned)
                has_one_off = (d in one_off_dates) and (d >= today)
                badge_kind = None
                if any(e["kind"] == "booked" for e in day_events):
                    badge_kind = "booked"
                elif any(e["kind"] == "failed" for e in day_events):
                    badge_kind = "failed"
                elif planned_info or has_one_off:
                    badge_kind = "planned"

                if badge_kind:
                    badge = StatusBadge(cell, badge_kind, size=24, bg=cell_bg)
                    badge.grid(row=1, column=0, padx=0, pady=(0, 10))

                self._make_clickable(cell, cell_bg, hover_for_cell, d, day_events, planned_info)

    def _make_clickable(self, cell, base_bg, hover_bg, day, day_events, planned_info) -> None:
        def _click(_e=None):
            self._open_day_detail(day, day_events, planned_info)

        def _enter(_e=None):
            try:
                cell.configure(fg_color=hover_bg)
            except Exception:
                pass

        def _leave(_e=None):
            try:
                cell.configure(fg_color=base_bg)
            except Exception:
                pass

        def bind_all(widget):
            widget.bind("<Button-1>", _click)
            widget.bind("<Enter>", _enter)
            widget.bind("<Leave>", _leave)
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass
            for child in widget.winfo_children():
                bind_all(child)
        bind_all(cell)

    def _open_day_detail(self, day: date, events: list[dict], planned_info: dict | None) -> None:
        DayDetailDialog(self.app, day, events, planned_info, self.app)


# ============================================================================
# App shell
# ============================================================================

def _set_windows_app_user_model_id(appid: str) -> None:
    """Tell Windows this Python process is a discrete app so the taskbar uses our icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self) -> None:
        _set_windows_app_user_model_id("teeoff.golf.booker.1")
        super().__init__()
        self.title(f"Tee Off Golf Booker  v{__version__}")
        self.minsize(700, 460)
        # customtkinter multiplies the geometry we pass by the DPI scaling factor, while the
        # screen is reported in (scaled) pixels. Size the window so it FITS *after* scaling
        # (a 150%-DPI laptop screen is only ~1280x720), then center once it is realized.
        try:
            _scale = ctk.ScalingTracker.get_window_scaling(self) or 1.0
        except Exception:
            _scale = 1.0
        _sw, _sh = self.winfo_screenwidth(), self.winfo_screenheight()
        _w = int(min(1020, (_sw - 80) / _scale))
        _h = int(min(700, (_sh - 100) / _scale))
        self.geometry(f"{_w}x{_h}")          # fits-the-screen size (DPI-aware)
        # Center it once the window is realized. Deferred (with a retry) because calling
        # this too early — before the window has its rendered size — silently no-ops.
        self.after(400, self._center_on_screen)
        self.after(1500, self._center_on_screen)

        # Window + taskbar icon
        if ICON_ICO.exists():
            try:
                self.iconbitmap(default=str(ICON_ICO))
            except Exception:
                # Fallback: PNG via iconphoto
                try:
                    self._icon_photo = tk.PhotoImage(file=str(ICON_PNG_128))
                    self.iconphoto(True, self._icon_photo)
                except Exception:
                    pass
        elif ICON_PNG_128.exists():
            try:
                self._icon_photo = tk.PhotoImage(file=str(ICON_PNG_128))
                self.iconphoto(True, self._icon_photo)
            except Exception:
                pass

        self.settings = load_settings()
        self.last_info: dict = {"registered": False}

        # Self-heal the scheduled task on every launch: if the app was moved or
        # reinstalled, the task's baked interpreter path may be stale (the original
        # "flash and die" cause). Silently re-point it on a background thread.
        threading.Thread(target=self._self_heal_task, daemon=True).start()

        # Check GitHub for a newer version shortly after launch (non-blocking, silent
        # if offline / up to date). Manual checks come from the dashboard button.
        self.after(3500, lambda: self._check_updates_async(manual=False))

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, on_nav=self._navigate)
        self.sidebar.grid(row=0, column=0, sticky="nsw")

        self.main_area = ctk.CTkFrame(self, fg_color=color_app_bg())
        self.main_area.grid(row=0, column=1, sticky="nsew")
        self.main_area.grid_rowconfigure(0, weight=1)
        self.main_area.grid_columnconfigure(0, weight=1)

        # Background status fetcher
        self.status_fetcher = StatusFetcher(refresh_seconds=20.0)
        self.status_fetcher.start()
        self.after(200, self._poll_status_queue)

        # Background bookings fetcher (live data from site)
        self.bookings_fetcher = BookingsFetcher(refresh_seconds=1800.0)
        self.bookings_fetcher.set_callback(self._on_bookings_data)
        self.bookings_fetcher.start()
        # Apply any cached data we already have on disk
        if self.bookings_fetcher.last_result:
            self.after(0, lambda d=self.bookings_fetcher.last_result: self._on_bookings_data(d))

        # Partner fetcher (on-demand only). Auto-fire once on first launch when
        # the cached partner list is empty.
        self.partner_fetcher = PartnerFetcher()
        if not self.settings.get("partners"):
            self.after(2500, self._auto_fetch_partners_once)

        # Build views
        self.views: dict[str, View] = {
            "dashboard": DashboardView(self),
            "schedule": ScheduleView(self),
            "account": AccountView(self),
            "test": TestView(self),
            "logs": LogsView(self),
        }
        self.current_view_key = "dashboard"
        for v in self.views.values():
            v.grid(row=0, column=0, sticky="nsew", padx=20, pady=16)
            v.grid_remove()
        self._navigate("dashboard")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center_on_screen(self) -> None:
        """Center the window once realized. winfo_width/height come back in logical px but
        winfo_screenwidth/height and wm_geometry are in physical px, so scale the window
        size up by the DPI factor before centering (customtkinter quirk)."""
        try:
            self.update_idletasks()
            try:
                scale = ctk.ScalingTracker.get_window_scaling(self) or 1.0
            except Exception:
                scale = 1.0
            w = int(self.winfo_width() * scale)
            h = int(self.winfo_height() * scale)
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.wm_geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
        except Exception:
            pass

    def _self_heal_task(self) -> None:
        try:
            healed, msg = ensure_task_current(self.settings)
            if healed:
                log.info("scheduler self-heal: %s", msg)
                sf = getattr(self, "status_fetcher", None)  # thread may outrun __init__
                if sf is not None:
                    sf.request_refresh()
        except Exception:
            log.exception("scheduler self-heal failed")

    # --- updates ---------------------------------------------------------------
    def _check_updates_async(self, manual: bool) -> None:
        """Check GitHub for a newer release on a background thread (debounced)."""
        if getattr(self, "_update_check_inflight", False):
            return
        self._update_check_inflight = True
        # Safety net: if the network call ever hangs (e.g. during the busy launch),
        # never leave the lock stuck — clear it after 20s so future checks still work.
        self.after(20000, lambda: setattr(self, "_update_check_inflight", False))
        def _runner():
            try:
                info = updater.check_for_update()
            except Exception:
                info = None
            self.after(0, lambda: self._on_update_result(info, manual))
        threading.Thread(target=_runner, daemon=True).start()

    def _on_update_result(self, info, manual: bool) -> None:
        self._update_check_inflight = False
        if info is None:
            if manual:
                messagebox.showinfo("Up to date", f"TeeOff is up to date (v{__version__}).")
            return
        notes = (info.notes[:300] + "\n\n") if info.notes else ""
        if messagebox.askyesno(
            "Update available",
            f"A new version of TeeOff is available.\n\n"
            f"Installed:  v{__version__}\nNew:  v{info.version}\n\n{notes}"
            f"Install it now? The app will close briefly and reopen on the new version."):
            self._do_update(info)

    def _do_update(self, info) -> None:
        self.title(f"Tee Off Golf Booker  v{__version__} — updating…")

        def _runner():
            try:
                staging = updater.download_and_stage(info)
            except Exception as e:
                self.after(0, lambda: (
                    self.title(f"Tee Off Golf Booker  v{__version__}"),
                    messagebox.showerror(
                        "Update failed",
                        f"Couldn't install the update:\n{e}\n\nYou can try again later — "
                        f"your existing version keeps working.")))
                return

            def _apply():
                try:
                    updater.apply_and_restart(staging)
                except Exception as e:
                    self.title(f"Tee Off Golf Booker  v{__version__}")
                    messagebox.showerror(
                        "Update failed",
                        f"Couldn't start the update:\n{e}\n\nYour current version still works.")
                    return
                # Success: close so the helper can swap files and relaunch.
                try:
                    self.status_fetcher.stop()
                    self.bookings_fetcher.stop()
                except Exception:
                    pass
                self.destroy()
            self.after(0, _apply)

        threading.Thread(target=_runner, daemon=True).start()

    def _navigate(self, key: str) -> None:
        if key not in self.views:
            return
        self.views[self.current_view_key].grid_remove()
        self.current_view_key = key
        self.views[key].grid()
        self.sidebar.set_active(key)
        self.views[key].on_show()

    def _poll_status_queue(self) -> None:
        try:
            while True:
                info = self.status_fetcher.queue.get_nowait()
                self.last_info = info
                self._apply_status(info)
        except queue.Empty:
            pass
        self.after(250, self._poll_status_queue)

    def _apply_status(self, info: dict) -> None:
        # Sidebar pill
        if not info.get("registered"):
            self.sidebar.set_status(color_warning(), "Not installed")
        else:
            state = info.get("State", "?")
            if state.lower() == "disabled":
                self.sidebar.set_status(color_warning(), "Paused")
            elif state.lower() == "running":
                self.sidebar.set_status(color_accent(), "Running now")
            else:
                self.sidebar.set_status(color_success(), "Ready")
        # Notify each view
        for v in self.views.values():
            try:
                v.on_status_update(info)
            except Exception:
                logging.exception("view %s on_status_update failed", v.__class__.__name__)

    def _auto_fetch_partners_once(self) -> None:
        """Kick off a single background partner fetch the first time the app runs."""
        def _done(fresh, err):
            def _apply():
                if fresh:
                    if merge_into_settings(self.settings, fresh):
                        save_settings(self.settings)
                    sched = self.views.get("schedule")
                    if sched is not None and hasattr(sched, "on_partner_data_updated"):
                        sched.on_partner_data_updated()
            self.after(0, _apply)
        self.partner_fetcher.fetch(_done)

    def _on_bookings_data(self, data: dict) -> None:
        # Called from the bookings fetcher thread → marshal to UI thread.
        def _apply():
            dash = self.views.get("dashboard")
            if dash is not None and hasattr(dash, "on_bookings_update"):
                dash.on_bookings_update(data)
        try:
            self.after(0, _apply)
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            self.status_fetcher.stop()
            self.bookings_fetcher.stop()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
