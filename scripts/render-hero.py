"""Rasterize PORTRAIT_WIDE onto purple, then a looping waterfall-shadow GIF."""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "src" / "logo.ts"
OUT_DIR = ROOT / "docs"
CELL = 6
PAD = 28
FRAMES = 24
DURATION_MS = 80

# 8-level density, sparse → solid. Matches logo.ts 注释.
RAMP = " .:sA3G&@"
DENSITY = {ch: i / (len(RAMP) - 1) for i, ch in enumerate(RAMP)}
DENSITY[" "] = 0.0

BG = (18, 6, 36)
VIOLET = (92, 32, 168)
DEEP = (6, 2, 16)
FACE_LO = (108, 48, 196)
FACE_HI = (244, 232, 255)


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
    lines = [ln.rstrip(" \t") for ln in lines]
    cols = max((len(ln) for ln in lines), default=0)
    return [ln.ljust(cols) for ln in lines]


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def waterfall(x: float, y: float, t: float) -> float:
    """0..1, darker where the curtain is thick. Scrolls down."""
    # several sheets, different speeds/wavelengths
    s1 = 0.5 + 0.5 * math.sin((y * 0.045 - t * 6.4) + math.sin(x * 0.031) * 2.2)
    s2 = 0.5 + 0.5 * math.sin((y * 0.09 - t * 9.1) + x * 0.07)
    s3 = 0.5 + 0.5 * math.sin((y * 0.16 - t * 4.2) + x * 0.018)
    col = 0.5 + 0.5 * math.sin(x * 0.052 + t * 1.4)
    v = 0.22 * s1 + 0.45 * s2 + 0.20 * s3 + 0.13 * col
    return v * v


def render_frame(
    grid: list[str],
    mask: list[list[bool]],
    halo: list[list[float]],
    t: float,
) -> Image.Image:
    rows = len(grid)
    cols = len(grid[0])
    w = cols * CELL + PAD * 2
    h = rows * CELL + PAD * 2
    img = Image.new("RGB", (w, h), BG)
    px = img.load()
    assert px is not None

    for j in range(h):
        for i in range(w):
            flow = waterfall(float(i), float(j), t)
            shade = lerp(BG, VIOLET, 0.08 + 0.42 * flow)
            shade = lerp(shade, DEEP, 0.72 * flow)
            px[i, j] = shade

    for y, line in enumerate(grid):
        for x, ch in enumerate(line):
            d = DENSITY.get(ch, 0.0)
            inside = mask[y][x]
            glow = halo[y][x]
            if inside and d < 0.08:
                d = 0.22
            x0 = PAD + x * CELL
            y0 = PAD + y * CELL
            for dy in range(CELL):
                for dx in range(CELL):
                    i = x0 + dx
                    j = y0 + dy
                    cur = px[i, j]
                    if glow > 0:
                        gflow = waterfall(float(i), float(j), t)
                        cur = lerp(cur, DEEP, glow * (0.4 + 0.6 * gflow))
                    if inside:
                        face = lerp(FACE_LO, FACE_HI, d ** 0.7)
                        edge = dx == 0 or dy == 0 or dx == CELL - 1 or dy == CELL - 1
                        cur = lerp(cur, face, 0.78 if edge else 1.0)
                    px[i, j] = cur
    return img


def filled_mask(grid: list[str]) -> list[list[bool]]:
    """Density > 0, plus holes that don't touch the border — so the bust is solid."""
    rows, cols = len(grid), len(grid[0])
    ink = [[DENSITY.get(ch, 0) > 0.04 for ch in row] for row in grid]
    outside = [[False] * cols for _ in range(rows)]
    q: list[tuple[int, int]] = []
    for x in range(cols):
        for y in (0, rows - 1):
            if not ink[y][x]:
                outside[y][x] = True
                q.append((x, y))
    for y in range(rows):
        for x in (0, cols - 1):
            if not ink[y][x] and not outside[y][x]:
                outside[y][x] = True
                q.append((x, y))
    while q:
        x, y = q.pop()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < cols and 0 <= ny < rows and not ink[ny][nx] and not outside[ny][nx]:
                outside[ny][nx] = True
                q.append((nx, ny))
    return [[not outside[y][x] for x in range(cols)] for y in range(rows)]


def halo_field(mask: list[list[bool]]) -> list[list[float]]:
    rows, cols = len(mask), len(mask[0])
    halo = [[0.0] * cols for _ in range(rows)]
    radius = 6
    for y in range(rows):
        for x in range(cols):
            if mask[y][x]:
                continue
            best = 99.0
            for yy in range(max(0, y - radius), min(rows, y + radius + 1)):
                for xx in range(max(0, x - radius), min(cols, x + radius + 1)):
                    if mask[yy][xx]:
                        dist = math.hypot(xx - x, yy - y)
                        if dist < best:
                            best = dist
            if best < radius:
                halo[y][x] = (1.0 - best / radius) ** 1.35
    return halo


def main() -> None:
    grid = parse_portrait()
    mask = filled_mask(grid)
    halo = halo_field(mask)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    still = render_frame(grid, mask, halo, 0.18)
    png_path = OUT_DIR / "socrates.png"
    still.save(png_path, "PNG")
    print("wrote", png_path, still.size)

    raw_frames = [render_frame(grid, mask, halo, i / FRAMES) for i in range(FRAMES)]
    pal = still.convert("P", palette=Image.Palette.ADAPTIVE, colors=40)
    frames = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in raw_frames]
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
