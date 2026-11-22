#!/usr/bin/env python3
"""Generate QDVC Spork's icons for both editions.

    python3 tools/make_icons.py [--preview]

Writes:
  macos/Resources/AppIcon.svg   vector master (Apple's macOS icon template)
  macos/Resources/AppIcon.icns  all sizes, 16-1024 px
  gtk4/data/icons/qdvc.spork.gtk4app.svg           GNOME-style app icon (128 grid)
  gtk4/data/icons/qdvc.spork.gtk4app-symbolic.svg  16 px symbolic icon
  build/icon-preview*.png       with --preview

The design follows the other QDVC macOS apps: a white continuous-corner tile
with a glyph and a round "seal" badge. The glyph is a spork whose tines are
git branch lanes with commit dots, merging into the bowl; the badge shows the
up/down arrows of syncing with several remotes. Needs `rsvg-convert` (librsvg)
for the PNG renders; everything else is the Python standard library.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Apple macOS icon template: 1024 canvas, 824 tile, 100 px margin.
TILE_ORIGIN = 100
TILE_SIZE = 824
CORNER_RADIUS = 185.4
TILE_TOP = "#FFFFFF"
TILE_BOTTOM = "#F3F3F5"

SLATE_LIGHT = "#5A6B82"
SLATE_DARK = "#2B3646"
LANES = ["#3584E4", "#2EC27E", "#E66100"]   # GNOME blue, green, orange
FACE_LIGHT = "#FFB38A"
FACE_MID = "#F0603A"
FACE_DARK = "#A8321C"

# Bezier description of a continuous ("squircle") corner, from PaintCode's
# reverse-engineering of UIKit's continuous corners, scaled by the radius.
_CORNER = [
    (1.528665, 0.0), (1.08849296, 0.0), (0.86840694, 0.0), (0.63149379, 0.07491139),
    (0.37282383, 0.16905956), (0.16905956, 0.37282383), (0.07491139, 0.63149379),
    (0.0, 0.86840694), (0.0, 1.08849296), (0.0, 1.528665),
]


def continuous_rounded_rect(x: float, y: float, w: float, h: float, r: float) -> str:
    c = [(px * r, py * r) for px, py in _CORNER]

    def pt(px, py):
        return f"{px:.2f},{py:.2f}"

    d = [f"M{pt(x + c[0][0], y)}"]
    d.append(f"L{pt(x + w - c[0][0], y)}")
    # top-right
    tr = [(x + w - px, y + py) for px, py in c]
    d.append(f"C{pt(*tr[1])} {pt(*tr[2])} {pt(*tr[3])}")
    d.append(f"C{pt(*tr[4])} {pt(*tr[5])} {pt(*tr[6])}")
    d.append(f"C{pt(*tr[7])} {pt(*tr[8])} {pt(x + w, y + c[9][1])}")
    d.append(f"L{pt(x + w, y + h - c[0][0])}")
    br = [(x + w - py, y + h - px) for px, py in c]
    d.append(f"C{pt(*br[1])} {pt(*br[2])} {pt(*br[3])}")
    d.append(f"C{pt(*br[4])} {pt(*br[5])} {pt(*br[6])}")
    d.append(f"C{pt(*br[7])} {pt(*br[8])} {pt(*br[9])}")
    d.append(f"L{pt(x + c[0][0], y + h)}")
    bl = [(x + px, y + h - py) for px, py in c]
    d.append(f"C{pt(*bl[1])} {pt(*bl[2])} {pt(*bl[3])}")
    d.append(f"C{pt(*bl[4])} {pt(*bl[5])} {pt(*bl[6])}")
    d.append(f"C{pt(*bl[7])} {pt(*bl[8])} {pt(x, y + h - c[9][1])}")
    d.append(f"L{pt(x, y + c[0][0])}")
    tl = [(x + py, y + px) for px, py in c]
    d.append(f"C{pt(*tl[1])} {pt(*tl[2])} {pt(*tl[3])}")
    d.append(f"C{pt(*tl[4])} {pt(*tl[5])} {pt(*tl[6])}")
    d.append(f"C{pt(*tl[7])} {pt(*tl[8])} {pt(*tl[9])}")
    d.append("Z")
    return " ".join(d)


def spork_glyph(cx: float, top: float, s: float) -> str:
    """The spork: three lane-tines with commit dots, a bowl, and a handle.

    `cx` is the centre line, `top` the y of the tine tips, `s` a scale (1 = 1024 canvas).
    """
    def X(v): return cx + v * s
    def Y(v): return top + v * s
    lane_dx = 95
    parts = []
    # handle (drawn first, under the bowl)
    parts.append(
        f'<rect x="{X(-26):.1f}" y="{Y(330):.1f}" width="{52 * s:.1f}" height="{300 * s:.1f}" '
        f'rx="{26 * s:.1f}" fill="url(#slate)"/>')
    # tines = lanes
    for i, colour in enumerate(LANES):
        lx = (i - 1) * lane_dx
        parts.append(
            f'<path d="M{X(lx):.1f},{Y(40):.1f} L{X(lx):.1f},{Y(205):.1f}" stroke="{colour}" '
            f'stroke-width="{34 * s:.1f}" stroke-linecap="round" fill="none"/>')
    # bowl: lanes merge into it
    parts.append(
        f'<path d="M{X(-150):.1f},{Y(185):.1f} L{X(150):.1f},{Y(185):.1f} '
        f'C{X(150):.1f},{Y(310):.1f} {X(80):.1f},{Y(375):.1f} {X(0):.1f},{Y(375):.1f} '
        f'C{X(-80):.1f},{Y(375):.1f} {X(-150):.1f},{Y(310):.1f} {X(-150):.1f},{Y(185):.1f} Z" '
        f'fill="url(#slate)"/>')
    # bowl highlight
    parts.append(
        f'<path d="M{X(-118):.1f},{Y(205):.1f} L{X(118):.1f},{Y(205):.1f} '
        f'C{X(112):.1f},{Y(250):.1f} {X(80):.1f},{Y(275):.1f} {X(0):.1f},{Y(280):.1f} '
        f'C{X(-80):.1f},{Y(275):.1f} {X(-112):.1f},{Y(250):.1f} {X(-118):.1f},{Y(205):.1f} Z" '
        f'fill="#FFFFFF" opacity="0.10"/>')
    # commit dots on the tines
    for i, colour in enumerate(LANES):
        lx = (i - 1) * lane_dx
        for yy in (40, 128):
            parts.append(
                f'<circle cx="{X(lx):.1f}" cy="{Y(yy):.1f}" r="{24 * s:.1f}" fill="#FFFFFF" '
                f'stroke="{colour}" stroke-width="{13 * s:.1f}"/>')
    # merge node in the bowl and a commit on the handle
    parts.append(f'<circle cx="{X(0):.1f}" cy="{Y(268):.1f}" r="{30 * s:.1f}" fill="#FFFFFF"/>')
    parts.append(f'<circle cx="{X(0):.1f}" cy="{Y(268):.1f}" r="{14 * s:.1f}" fill="{SLATE_DARK}"/>')
    parts.append(f'<circle cx="{X(0):.1f}" cy="{Y(520):.1f}" r="{14 * s:.1f}" fill="#FFFFFF" opacity="0.9"/>')
    return "\n    ".join(parts)


def seal(cx: float, cy: float, r: float, ring: float) -> str:
    a = r * 0.42
    return f"""
  <g filter="url(#objshadow)">
    <circle cx="{cx}" cy="{cy}" r="{r + ring}" fill="#FFFFFF"/>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#face)"/>
    <g stroke="#FFFFFF" stroke-width="{r * 0.15:.1f}" stroke-linecap="round" stroke-linejoin="round" fill="none">
      <path d="M{cx - a * 0.55:.1f},{cy + a:.1f} L{cx - a * 0.55:.1f},{cy - a:.1f}"/>
      <path d="M{cx - a * 1.05:.1f},{cy - a * 0.45:.1f} L{cx - a * 0.55:.1f},{cy - a:.1f} L{cx - a * 0.05:.1f},{cy - a * 0.45:.1f}"/>
      <path d="M{cx + a * 0.55:.1f},{cy - a:.1f} L{cx + a * 0.55:.1f},{cy + a:.1f}"/>
      <path d="M{cx + a * 0.05:.1f},{cy + a * 0.45:.1f} L{cx + a * 0.55:.1f},{cy + a:.1f} L{cx + a * 1.05:.1f},{cy + a * 0.45:.1f}"/>
    </g>
    <ellipse cx="{cx - r * 0.18:.1f}" cy="{cy - r * 0.5:.1f}" rx="{r * 0.55:.1f}" ry="{r * 0.28:.1f}" fill="#FFFFFF" opacity="0.35"/>
  </g>"""


def defs(shadow_blur: float, shadow_dy: float, obj_blur: float) -> str:
    return f"""
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{TILE_TOP}"/>
      <stop offset="1" stop-color="{TILE_BOTTOM}"/>
    </linearGradient>
    <linearGradient id="edge" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#000000" stop-opacity="0.04"/>
      <stop offset="1" stop-color="#000000" stop-opacity="0.12"/>
    </linearGradient>
    <linearGradient id="slate" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{SLATE_LIGHT}"/>
      <stop offset="1" stop-color="{SLATE_DARK}"/>
    </linearGradient>
    <radialGradient id="face" cx="0.38" cy="0.32" r="0.8">
      <stop offset="0" stop-color="{FACE_LIGHT}"/>
      <stop offset="0.55" stop-color="{FACE_MID}"/>
      <stop offset="1" stop-color="{FACE_DARK}"/>
    </radialGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="{shadow_blur}"/>
      <feOffset dy="{shadow_dy}" result="b"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.5"/></feComponentTransfer>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="objshadow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="{obj_blur}"/>
      <feOffset dy="{obj_blur}" result="b"/>
      <feFlood flood-color="#5A1A0C" flood-opacity="0.35"/>
      <feComposite in2="b" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>"""


def mac_svg() -> str:
    tile = continuous_rounded_rect(TILE_ORIGIN, TILE_ORIGIN, TILE_SIZE, TILE_SIZE, CORNER_RADIUS)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  {defs(14, 12, 12)}
  <g filter="url(#shadow)">
    <path d="{tile}" fill="url(#bg)"/>
  </g>
  <path d="{tile}" fill="none" stroke="url(#edge)" stroke-width="3"/>
  <g>
    {spork_glyph(455, 190, 1.0)}
  </g>
  {seal(720, 718, 118, 12)}
</svg>
"""


def gnome_svg() -> str:
    """GNOME HIG style: 128 px canvas, a light plate with a darker base edge."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  {defs(1.5, 1, 1.5)}
  <rect x="10" y="16" width="108" height="104" rx="18" fill="#C0BFBC"/>
  <rect x="10" y="10" width="108" height="104" rx="18" fill="#F6F5F4"/>
  <rect x="10.5" y="10.5" width="107" height="103" rx="17.5" fill="none" stroke="#000000" stroke-opacity="0.06"/>
  <g>
    {spork_glyph(57, 24, 0.118)}
  </g>
  {seal(90, 88, 17, 2.5)}
</svg>
"""


SYMBOLIC = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <g fill="#2e3436">
    <circle cx="4.5" cy="2.5" r="1.5"/>
    <circle cx="8" cy="2.5" r="1.5"/>
    <circle cx="11.5" cy="2.5" r="1.5"/>
    <rect x="3.75" y="2.5" width="1.5" height="4"/>
    <rect x="7.25" y="2.5" width="1.5" height="4"/>
    <rect x="10.75" y="2.5" width="1.5" height="4"/>
    <path d="M3,6 h10 c0,2.6 -2.2,4.2 -5,4.2 c-2.8,0 -5,-1.6 -5,-4.2 z"/>
    <rect x="7.1" y="9.5" width="1.8" height="6" rx="0.9"/>
  </g>
</svg>
"""

ICNS_ELEMENTS = [("icp4", 16), ("ic11", 32), ("icp5", 32), ("ic12", 64), ("ic07", 128),
                 ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512), ("ic10", 1024)]


def render_png(svg_path: Path, size: int, out: Path) -> None:
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(out), str(svg_path)],
                   check=True)


def write_icns(pngs: dict[int, bytes], out: Path) -> None:
    chunks = b""
    for tag, size in ICNS_ELEMENTS:
        data = pngs[size]
        chunks += tag.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    out.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preview", action="store_true", help="also write PNG previews to build/")
    args = ap.parse_args()

    mac_res = ROOT / "macos" / "Resources"
    gtk_icons = ROOT / "gtk4" / "data" / "icons"
    mac_res.mkdir(parents=True, exist_ok=True)
    gtk_icons.mkdir(parents=True, exist_ok=True)

    mac_path = mac_res / "AppIcon.svg"
    mac_path.write_text(mac_svg())
    (gtk_icons / "qdvc.spork.gtk4app.svg").write_text(gnome_svg())
    (gtk_icons / "qdvc.spork.gtk4app-symbolic.svg").write_text(SYMBOLIC)

    with tempfile.TemporaryDirectory() as tmp:
        pngs = {}
        for size in sorted({s for _, s in ICNS_ELEMENTS}):
            p = Path(tmp) / f"{size}.png"
            render_png(mac_path, size, p)
            pngs[size] = p.read_bytes()
        write_icns(pngs, mac_res / "AppIcon.icns")

    if args.preview:
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        render_png(mac_path, 1024, build / "icon-preview.png")
        render_png(gtk_icons / "qdvc.spork.gtk4app.svg", 512, build / "icon-preview-gnome.png")
        render_png(gtk_icons / "qdvc.spork.gtk4app-symbolic.svg", 128, build / "icon-preview-symbolic.png")
    print("Wrote", mac_path.relative_to(ROOT), "AppIcon.icns and the GNOME icons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
