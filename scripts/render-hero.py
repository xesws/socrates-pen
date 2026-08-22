"""Copy PORTRAIT_WIDE as-is: monospace glyphs, same metrics as the plugin splash."""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "src" / "logo.ts"
OUT_DIR = ROOT / "docs"
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
# Plugin: font-size clamp, line-height 1, letter-spacing 0, advance ~0.6em.
SIZE = 14
PAD_X = 36
PAD_Y = 32
FRAMES = 20
DURATION_MS = 90

BG = (18, 6, 36)
VIOLET = (92, 32, 168)
DEEP = (6, 2, 16)
# Dark-theme splash uses --text-muted on a dark field.
INK = (214, 201, 232)


def parse_portrait() -> list[str]:
    src = LOGO.read_text(encoding="utf-8")
    m = re.search(
        r"export const PORTRAIT_WIDE = art\(String\.raw`(.*?)`\)",
        src,
        re.S,
    )
    if not m:
        raise SystemExit("PORTRAIT_WIDE not found in src/logo.ts")
    lines = m.group(1).replace("\r", "").split("\n")
    if lines and lines[0] == "":
        lines = lines[1:]
    while lines and lines[-1].strip() == "":
        lines.pop()
    return [ln.rstrip(" \t") for ln in lines]


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def waterfall(x: float, y: float, t: float) -> float:
    s1 = 0.5 + 0.5 * math.sin((y * 0.045 - t * 6.4) + math.sin(x * 0.031) * 2.2)
    s2 = 0.5 + 0.5 * math.sin((y * 0.09 - t * 9.1) + x * 0.07)
    s3 = 0.5 + 0.5 * math.sin((y * 0.16 - t * 4.2) + x * 0.018)
    col = 0.5 + 0.5 * math.sin(x * 0.052 + t * 1.4)
    v = 0.22 * s1 + 0.45 * s2 + 0.20 * s3 + 0.13 * col
    return v * v


def load_font() -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, SIZE, index=0)
    except OSError:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Courier New.ttf", SIZE)


def paint_portrait(lines: list[str], font: ImageFont.FreeTypeFont) -> Image.Image:
    """One RGBA layer: transparent except the original glyphs."""
    sample = "0" * 64
    adv = font.getlength(sample) / 64
    line_h = SIZE  # CSS line-height: 1
    cols = max((len(ln) for ln in lines), default=0)
    w = int(math.ceil(cols * adv)) + PAD_X * 2
    h = len(lines) * line_h + PAD_Y * 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i, ln in enumerate(lines):
        if not ln:
            continue
        draw.text(
            (PAD_X, PAD_Y + i * line_h),
            ln,
            font=font,
            fill=INK + (255,),
        )
    return layer


def paint_bg(size: tuple[int, int], t: float) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), BG)
    px = img.load()
    assert px is not None
    for j in range(h):
        for i in range(w):
            flow = waterfall(float(i), float(j), t)
            shade = lerp(BG, VIOLET, 0.08 + 0.42 * flow)
            shade = lerp(shade, DEEP, 0.72 * flow)
            px[i, j] = shade
    return img


def composite(bg: Image.Image, portrait: Image.Image) -> Image.Image:
    out = bg.convert("RGBA")
    out.alpha_composite(portrait)
    return out.convert("RGB")


def main() -> None:
    lines = parse_portrait()
    font = load_font()
    portrait = paint_portrait(lines, font)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    still = composite(paint_bg(portrait.size, 0.18), portrait)
    png_path = OUT_DIR / "socrates.png"
    still.save(png_path, "PNG")
    print("wrote", png_path, still.size, "adv", round(font.getlength("0"), 3))

    raw = [composite(paint_bg(portrait.size, i / FRAMES), portrait) for i in range(FRAMES)]
    pal = still.convert("P", palette=Image.Palette.ADAPTIVE, colors=48)
    frames = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in raw]
    gif_path = OUT_DIR / "socrates.gif"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=DURATION_MS,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print("wrote", gif_path, gif_path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
