"""Render status badges (booked / failed / planned) as PNGs at several sizes.

Pre-rendered PNGs displayed via tk.PhotoImage look crisper than Tk Canvas at
small sizes, especially for the checkmark/X glyphs.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS_DIR = Path(__file__).parent.parent / "app" / "assets" / "badges"

KINDS = {
    "booked":  (31, 122, 77, 255),    # fairway green
    "failed":  (207, 34, 46, 255),    # red
    "planned": (122, 138, 130, 255),  # muted slate
}
SHADOW = (0, 0, 0, 80)
WHITE = (255, 255, 255, 255)


def render(kind: str, size: int) -> Image.Image:
    SS = 4
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Drop shadow — blurred dark circle, offset down 1 unit
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sh_off = max(1, SS)
    sd.ellipse([SS, SS + sh_off, s - SS, s - SS + sh_off], fill=SHADOW)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=SS * 1.4))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # Main coloured circle (slightly inset so the shadow is visible underneath)
    inset = SS * 0.5
    draw.ellipse([inset, inset, s - inset, s - inset], fill=KINDS[kind])

    # Inner top highlight — subtle lighter ring at the top
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse([SS * 2, SS * 1.5, s - SS * 2, s * 0.55], fill=(255, 255, 255, 30))
    hl = hl.filter(ImageFilter.GaussianBlur(radius=SS))
    img = Image.alpha_composite(img, hl)
    draw = ImageDraw.Draw(img)

    lw = max(2, int(s / 8))  # glyph stroke width
    if kind == "booked":
        # Bold checkmark
        draw.line(
            [(s * 0.30, s * 0.52), (s * 0.45, s * 0.68), (s * 0.74, s * 0.36)],
            fill=WHITE, width=lw, joint="curve",
        )
    elif kind == "failed":
        draw.line([(s * 0.32, s * 0.32), (s * 0.68, s * 0.68)], fill=WHITE, width=lw)
        draw.line([(s * 0.68, s * 0.32), (s * 0.32, s * 0.68)], fill=WHITE, width=lw)
    elif kind == "planned":
        dot_r = s * 0.16
        cx = cy = s / 2
        draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    sizes = [14, 16, 18, 22, 24, 28, 32]
    n = 0
    for kind in KINDS:
        for size in sizes:
            render(kind, size).save(ASSETS_DIR / f"{kind}-{size}.png", "PNG")
            n += 1
    print(f"Wrote {n} badge PNGs to {ASSETS_DIR}")


if __name__ == "__main__":
    main()
