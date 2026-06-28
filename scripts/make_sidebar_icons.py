"""Render monochrome SVG-style sidebar nav icons as PNG.

Each icon is drawn with simple PIL primitives (lines, ovals, rectangles) at 4x
the final size, then downsampled with LANCZOS for crisp anti-aliasing. Two
copies of each icon are produced — one in the "inactive" sidebar text colour
and one in the green accent — so the sidebar can switch between them on hover.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS_DIR = Path(__file__).parent.parent / "app" / "assets" / "nav"
SIZE = 20            # final display size
SS = 4               # supersample factor
S = SIZE * SS
LINE = 2 * SS
ROUND_R = 1.5 * SS

INACTIVE = (180, 195, 184, 255)   # muted text colour
ACTIVE = (63, 185, 80, 255)       # bright green accent


def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _save(img: Image.Image, name: str, color: tuple[int, int, int, int]) -> None:
    # Recolor: replace any non-transparent pixel with the chosen colour
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a > 0:
                px[x, y] = (color[0], color[1], color[2], a)
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "active" if color == ACTIVE else "inactive"
    img.save(ASSETS_DIR / f"{name}-{suffix}.png", "PNG")


def _draw_dashboard(draw: ImageDraw.ImageDraw) -> None:
    """2x2 grid of rounded rectangles."""
    pad = 2 * SS
    gap = 2 * SS
    inner = S - pad * 2
    cell = (inner - gap) // 2
    for r in range(2):
        for c in range(2):
            x0 = pad + c * (cell + gap)
            y0 = pad + r * (cell + gap)
            draw.rounded_rectangle(
                [x0, y0, x0 + cell, y0 + cell],
                radius=ROUND_R, fill=(255, 255, 255, 255),
            )


def _draw_schedule(draw: ImageDraw.ImageDraw) -> None:
    """Calendar with header tabs + a couple of marked days."""
    pad = 2 * SS
    body = [pad, pad + 3 * SS, S - pad, S - pad]
    draw.rounded_rectangle(body, radius=ROUND_R, outline=(255, 255, 255, 255), width=LINE)
    # rings (calendar bindings)
    ring_y = pad + 2 * SS
    for x in (pad + 3 * SS, S - pad - 3 * SS):
        draw.line([x, pad, x, ring_y + LINE], fill=(255, 255, 255, 255), width=LINE)
    # header bar fill
    draw.rectangle([pad + LINE, pad + 3 * SS + LINE,
                    S - pad - LINE, pad + 6 * SS],
                   fill=(255, 255, 255, 255))
    # marked day
    dot_r = 1.5 * SS
    cx, cy = S // 2, S - pad - 4 * SS
    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=(255, 255, 255, 255))


def _draw_account(draw: ImageDraw.ImageDraw) -> None:
    """Person silhouette — head + shoulders."""
    cx = S // 2
    head_r = 3.2 * SS
    head_cy = 6 * SS
    draw.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
                 fill=(255, 255, 255, 255))
    # body — half-ellipse / pill shape
    body_w = 7 * SS
    body_h = 6 * SS
    body_top = head_cy + head_r + 0.5 * SS
    draw.rounded_rectangle(
        [cx - body_w, body_top, cx + body_w, body_top + body_h],
        radius=body_w, fill=(255, 255, 255, 255),
    )


def _draw_test(draw: ImageDraw.ImageDraw) -> None:
    """Lab flask."""
    cx = S // 2
    # neck
    neck_top = 2 * SS
    neck_bottom = 7 * SS
    neck_w = 1.5 * SS
    draw.rectangle([cx - neck_w, neck_top, cx + neck_w, neck_bottom],
                   fill=(255, 255, 255, 255))
    # top lip
    draw.rectangle([cx - 3 * SS, neck_top - 0.5 * SS, cx + 3 * SS, neck_top + LINE],
                   fill=(255, 255, 255, 255))
    # body triangle
    body_top_w = 2.5 * SS
    body_bottom_w = 6 * SS
    body_bottom = S - 2 * SS
    draw.polygon([
        cx - body_top_w, neck_bottom,
        cx + body_top_w, neck_bottom,
        cx + body_bottom_w, body_bottom,
        cx - body_bottom_w, body_bottom,
    ], fill=(255, 255, 255, 255))
    # bubbles
    for (dx, dy, r) in [(-1.5 * SS, -1 * SS, 0.8 * SS), (1 * SS, -1.5 * SS, 0.6 * SS)]:
        draw.ellipse([cx + dx - r, body_bottom - 3 * SS + dy - r,
                      cx + dx + r, body_bottom - 3 * SS + dy + r],
                     fill=(0, 0, 0, 0))


def _draw_logs(draw: ImageDraw.ImageDraw) -> None:
    """Stack of horizontal lines (log file)."""
    pad = 3 * SS
    lh = LINE
    gap = 1.8 * SS
    y = pad
    while y + lh < S - pad:
        # First line is a "title" — slightly shorter, on the left
        w_factor = 0.55 if y == pad else (0.85 if (int((y - pad) / (lh + gap)) % 2 == 0) else 0.70)
        draw.rounded_rectangle(
            [pad, y, pad + (S - 2 * pad) * w_factor, y + lh],
            radius=ROUND_R, fill=(255, 255, 255, 255),
        )
        y += lh + gap


ICONS = {
    "dashboard": _draw_dashboard,
    "schedule":  _draw_schedule,
    "account":   _draw_account,
    "test":      _draw_test,
    "logs":      _draw_logs,
}


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for name, draw_fn in ICONS.items():
        for color in (INACTIVE, ACTIVE):
            img, draw = _new()
            draw_fn(draw)
            _save(img, name, color)
    print(f"Wrote {len(ICONS) * 2} icons to {ASSETS_DIR}")


if __name__ == "__main__":
    main()
