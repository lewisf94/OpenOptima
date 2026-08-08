"""Draw the Windows application icon.

Windows wants a multi-size ``.ico`` for the Start menu, the taskbar and the
installer. The interface's own icon is `app/static/favicon.svg`, and Pillow
cannot read SVG, so the same shape is drawn here in code. Keep the two in step
by hand -- there are four shapes in it.

Run after changing the design:

    python scripts/make_icon.py

The result, `packaging/icon.ico`, is committed. It is not generated during the
build, because a build should not depend on Pillow or on anyone remembering to
run this.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "packaging" / "icon.ico"

#: The application window is drawn by Edge, which takes its taskbar icon from
#: the page's own favicon rather than from the executable. An SVG favicon is
#: enough for the browser tab but not reliably picked up for the taskbar, so a
#: PNG is written alongside it.
FAVICON = ROOT / "src" / "openoptima" / "app" / "static" / "favicon.png"

#: Matches --accent, --ok and white in `app/static/style.css`.
BACKGROUND = (31, 111, 235, 255)
CURVE = (255, 255, 255, 255)
DOT = (63, 185, 80, 255)

#: Every size Windows asks for. 16 is the one people actually squint at, in the
#: taskbar and the title bar, so the design has to survive it: a thick curve and
#: one dot, nothing finer.
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: Drawn this much larger and shrunk down, because Pillow does not anti-alias.
#: Without it the curve looks like a staircase at every size.
SUPERSAMPLE = 8


def _bezier(points, steps: int = 200):
    """Points along a cubic Bezier curve, as `favicon.svg` defines it."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    result = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        result.append((a * x0 + b * x1 + c * x2 + d * x3, a * y0 + b * y1 + c * y2 + d * y3))
    return result


def render(size: int) -> Image.Image:
    scale = size * SUPERSAMPLE / 32  # the SVG's own 32x32 coordinate system
    edge = size * SUPERSAMPLE
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((0, 0, edge - 1, edge - 1), radius=7 * scale, fill=BACKGROUND)

    # Stamped as overlapping dots rather than drawn with line(). Pillow's line
    # joins leave a hatched, ragged edge on a curve this thick, and its line()
    # has no round caps either. A dense enough brush gives both for free.
    curve = _bezier([(6, 24), (12, 24), (14, 8), (26, 8)], steps=600)
    radius = 1.5 * scale  # stroke-width 3 in the SVG
    for x, y in curve:
        px, py = x * scale, y * scale
        draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=CURVE)

    cx, cy, r = 13 * scale, 17 * scale, 3 * scale
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=DOT)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [render(size) for size in SIZES]
    largest = frames[-1]
    largest.save(TARGET, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes, sizes {SIZES})")

    largest.save(FAVICON, format="PNG", optimize=True)
    print(f"wrote {FAVICON.relative_to(ROOT)} ({FAVICON.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
