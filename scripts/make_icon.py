"""Generate the golf-ball + checkered-flag-pin icon as PNG + multi-resolution .ico.

Concept: a golf ball next to a tall flagstick whose flag is a black-and-white
racing/checkered pattern — golf meets race-against-the-clock.

Run from the project root:
    .venv\\Scripts\\python scripts\\make_icon.py

Outputs:
    app/assets/icon.png   — 256x256 transparent PNG
    app/assets/icon.ico   — Windows .ico with sizes 16, 32, 48, 64, 128, 256
    app/assets/icon-64/96/128.png — pre-rendered sidebar/header sizes
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS_DIR = Path(__file__).parent.parent / "app" / "assets"

WHITE_BALL = (255, 255, 255, 255)
BALL_OUTLINE = (155, 165, 159, 255)
DIMPLE = (212, 218, 214, 255)
STICK_DARK = (32, 38, 35, 255)
STICK_MID = (72, 80, 76, 255)
FLAG_BLACK = (28, 32, 30, 255)
FLAG_WHITE = (250, 250, 248, 255)
ACCENT_GREEN = (31, 122, 77, 255)


def draw_dimples(draw, cx, cy, r):
    dimple_r = max(1, r * 0.10)
    # Outer ring
    for i in range(7):
        ang = math.radians(45 + i * (360 / 7))
        dx = cx + r * 0.58 * math.cos(ang)
        dy = cy + r * 0.58 * math.sin(ang)
        draw.ellipse([dx - dimple_r, dy - dimple_r, dx + dimple_r, dy + dimple_r], fill=DIMPLE)
    # A few interior dimples
    for ox, oy in [(-0.14, -0.08), (0.18, 0.04), (-0.06, 0.22), (0.05, -0.20)]:
        x = cx + r * ox
        y = cy + r * oy
        draw.ellipse([x - dimple_r, y - dimple_r, x + dimple_r, y + dimple_r], fill=DIMPLE)


def render(size: int = 256) -> Image.Image:
    """Compose ball-on-left + flagstick-on-right with a checkered flag."""
    s = size * 2  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # --- Geometry ---
    ball_cx = s * 0.34
    ball_cy = s * 0.66
    ball_r = s * 0.24

    stick_top_y = s * 0.10
    stick_base_y = s * 0.90
    stick_x = s * 0.68
    stick_w = max(3, s * 0.022)

    # --- Soft green ground patch (anchors the scene) ---
    ground = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(ground)
    gd.ellipse([s * 0.10, s * 0.86, s * 0.96, s * 1.02],
               fill=(*ACCENT_GREEN[:3], 75))
    ground = ground.filter(ImageFilter.GaussianBlur(radius=s * 0.014))
    img = Image.alpha_composite(img, ground)

    # --- Flagstick shadow on the ground ---
    sh = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.ellipse([stick_x - s * 0.09, stick_base_y - s * 0.014,
                stick_x + s * 0.09, stick_base_y + s * 0.018],
               fill=(0, 0, 0, 95))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=s * 0.012))
    img = Image.alpha_composite(img, sh)

    draw = ImageDraw.Draw(img)

    # --- Flagstick (two parallel lines for a hint of metallic shading) ---
    half = stick_w / 2
    # Dark left side
    draw.rectangle([stick_x - half, stick_top_y,
                    stick_x + half * 0.1, stick_base_y], fill=STICK_DARK)
    # Light right highlight
    draw.rectangle([stick_x + half * 0.1, stick_top_y,
                    stick_x + half, stick_base_y], fill=STICK_MID)
    # Ferrule (small dark ball on top of stick)
    tip_r = stick_w * 1.3
    draw.ellipse([stick_x - tip_r, stick_top_y - tip_r * 0.5,
                  stick_x + tip_r, stick_top_y + tip_r * 1.5],
                 fill=STICK_DARK)

    # --- Checkered flag (rectangle attached to right of stick) ---
    flag_left = stick_x + half
    flag_right = stick_x + s * 0.26
    flag_top = stick_top_y + s * 0.008
    flag_bot = stick_top_y + s * 0.22
    flag_w = flag_right - flag_left
    flag_h = flag_bot - flag_top

    rows = 4
    cols = 6
    flag_img = Image.new("RGBA", (max(1, int(flag_w)), max(1, int(flag_h))), (0, 0, 0, 0))
    fd = ImageDraw.Draw(flag_img)
    cell_w = flag_w / cols
    cell_h = flag_h / rows
    for r in range(rows):
        for c in range(cols):
            x0 = c * cell_w
            y0 = r * cell_h
            color = FLAG_BLACK if (r + c) % 2 == 0 else FLAG_WHITE
            # +1 to overlap and kill seams between cells
            fd.rectangle([x0, y0, x0 + cell_w + 1, y0 + cell_h + 1], fill=color)

    # Subtle wave: shear the flag image into a parallelogram so it looks like
    # it's waving in the breeze. We map (0,0)->(0, slight up), (W,0)->(W, slight down).
    skew = s * 0.022
    waved = flag_img.transform(
        flag_img.size,
        Image.AFFINE,
        # Output (x', y') = (x, y + (x/W)*skew - skew/2)  → expressed as affine matrix
        # PIL AFFINE takes inverse mapping: (input_x, input_y) from (x', y'):
        # input_x = x'
        # input_y = y' - (x'/W)*skew + skew/2
        (1, 0, 0,
         -skew / flag_img.width, 1, skew / 2),
        resample=Image.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )

    img.paste(waved, (int(flag_left), int(flag_top - skew / 2)), waved)

    # Subtle outline around flag for definition (drawn after paste, on top)
    draw = ImageDraw.Draw(img)
    draw.rectangle([flag_left, flag_top - skew / 2,
                    flag_right, flag_bot + skew / 2],
                   outline=STICK_DARK, width=max(1, int(s * 0.005)))

    # --- Ball drop shadow ---
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd2 = ImageDraw.Draw(shadow)
    sd2.ellipse([ball_cx - ball_r + s * 0.014, ball_cy - ball_r + s * 0.024,
                 ball_cx + ball_r + s * 0.014, ball_cy + ball_r + s * 0.024],
                fill=(0, 0, 0, 80))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=s * 0.016))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # --- Ball body ---
    outline_w = max(2, int(s * 0.006))
    draw.ellipse([ball_cx - ball_r, ball_cy - ball_r,
                  ball_cx + ball_r, ball_cy + ball_r],
                 fill=WHITE_BALL, outline=BALL_OUTLINE, width=outline_w)

    # --- Ball top-left highlight ---
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hl_w = ball_r * 0.85
    hl_h = ball_r * 0.55
    hd.ellipse([ball_cx - ball_r * 0.55, ball_cy - ball_r * 0.65,
                ball_cx - ball_r * 0.55 + hl_w, ball_cy - ball_r * 0.65 + hl_h],
               fill=(255, 255, 255, 200))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=s * 0.010))
    img = Image.alpha_composite(img, hl)
    draw = ImageDraw.Draw(img)

    # --- Dimples ---
    draw_dimples(draw, ball_cx, ball_cy, ball_r)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    big = render(256)
    big.save(ASSETS_DIR / "icon.png", "PNG")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(ASSETS_DIR / "icon.ico", sizes=sizes)
    for px in (64, 96, 128):
        big.resize((px, px), Image.LANCZOS).save(ASSETS_DIR / f"icon-{px}.png", "PNG")
    print(f"Wrote icon.png, icon.ico ({len(sizes)} resolutions), icon-64/96/128.png")


if __name__ == "__main__":
    main()
