#!/usr/bin/env python3
"""
Render ananta.svg → ANSI TrueColor half-block terminal art or ASCII fallback.

Usage:
  python scripts/render_terminal_logo.py
  python scripts/render_terminal_logo.py --width 120
  python scripts/render_terminal_logo.py --width 90 --mono-only
  python scripts/render_terminal_logo.py --width 160 --ascii-only --ascii-palette detailed
  python scripts/render_terminal_logo.py --svg path/to/file.svg --output-dir /tmp/out

Requires: Pillow, cairosvg (or rsvg-convert / inkscape)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

COLOR_TOLERANCE = 25

ASCII_PALETTES = {
    "clean": " .,:;irsXA253hMHGS#9B&@",
    "detailed": " .'`^\",:;Il!i~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
}


@dataclass
class RenderConfig:
    white_luma: int = 240
    falloff: int = 10
    alpha_cutoff: int = 200
    visible_threshold: float = 0.3
    height_ratio: float = 0.48
    render_size: int = 800
    contrast: float = 1.0
    gamma: float = 1.0
    invert: bool = False
    trim: bool = False
    trim_padding: int = 2


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def pixel_alpha(r, g, b, a, cfg: RenderConfig):
    if a < cfg.alpha_cutoff:
        return 0.0
    lum = luma(r, g, b)
    if lum >= cfg.white_luma:
        return 0.0
    if lum >= cfg.white_luma - cfg.falloff:
        return (cfg.white_luma - lum) / cfg.falloff
    return 1.0


def pixel_density(r, g, b, a, cfg: RenderConfig) -> float:
    pa = pixel_alpha(r, g, b, a, cfg)
    if pa < cfg.visible_threshold:
        return 0.0
    d = 1.0 - (luma(r, g, b) / 255.0)
    d = ((d - 0.5) * cfg.contrast) + 0.5
    d = max(0.0, min(1.0, d))
    d = d ** cfg.gamma
    if cfg.invert:
        d = 1.0 - d
    return d


def col_dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def svg_to_png(svg_path: str, png_path: str, width: int = 800) -> None:
    try:
        from cairosvg import svg2png
        svg2png(url=svg_path, write_to=png_path, output_width=width, output_height=width)
        return
    except ImportError:
        pass
    try:
        subprocess.run(
            ["rsvg-convert", "--width", str(width), "--height", str(width), "-o", png_path, svg_path],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            ["inkscape", "--export-width", str(width), "--export-height", str(width),
             "--export-filename", png_path, svg_path],
            check=True, capture_output=True,
        )
        return
    except FileNotFoundError:
        pass
    print("Error: No SVG renderer found. Install cairosvg (`pip install cairosvg`), rsvg-convert, or inkscape.",
          file=sys.stderr)
    sys.exit(1)


def _trim_image(img, cfg: RenderConfig):
    from PIL import Image
    px = list(img.get_flattened_data())
    w, h = img.size
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    found = False
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[y * w + x]
            pa = pixel_alpha(r, g, b, a, cfg)
            if pa >= cfg.visible_threshold:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
                found = True
    if not found:
        return img
    pad = cfg.trim_padding
    left = max(0, min_x - pad)
    top = max(0, min_y - pad)
    right = min(w, max_x + pad + 1)
    bottom = min(h, max_y + pad + 1)
    return img.crop((left, top, right, bottom))


def load_image(png_path: str, width: int, cfg: RenderConfig):
    from PIL import Image
    img = Image.open(png_path).convert("RGBA")
    if cfg.trim:
        img = _trim_image(img, cfg)
    aspect = img.height / img.width
    new_h = max(1, int(width * aspect * cfg.height_ratio))
    return img.resize((width, new_h), Image.LANCZOS)


def ansi_fg(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


def ansi_bg(r, g, b):
    return f"\x1b[48;2;{r};{g};{b}m"


def ansi_reset():
    return "\x1b[0m"


def render_ansi(img, cfg: RenderConfig):
    px = list(img.get_flattened_data())
    w, h = img.size
    thr = cfg.visible_threshold

    def g(x, y):
        return px[y * w + x]

    lines = []
    for y in range(0, h - 1, 2):
        parts = []
        last_fg = None
        last_bg = None
        x = 0
        while x < w:
            tr, tg, tb, ta = g(x, y)
            br, bg, bb, ba = g(x, y + 1)
            nta = pixel_alpha(tr, tg, tb, ta, cfg)
            nba = pixel_alpha(br, bg, bb, ba, cfg)
            tt = nta < thr
            bt = nba < thr

            if tt and bt:
                if last_fg is not None or last_bg is not None:
                    parts.append(ansi_reset())
                    last_fg = None
                    last_bg = None
                parts.append(" ")
                x += 1
                continue

            if tt and not bt:
                ch = "▄"
                fg = (br, bg, bb)
                bg_key = None
            elif not tt and bt:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = None
            else:
                ch = "▀"
                fg = (tr, tg, tb)
                bg_key = (br, bg, bb)

            run = 1
            while x + run < w:
                ntr, ntg, ntb, nta2 = g(x + run, y)
                nbr, nbg, nbb, nba2 = g(x + run, y + 1)
                nta3 = pixel_alpha(ntr, ntg, ntb, nta2, cfg)
                nba3 = pixel_alpha(nbr, nbg, nbb, nba2, cfg)
                ntt = nta3 < thr
                nbt = nba3 < thr
                if ntt and nbt:
                    break
                if ntt and not nbt:
                    if col_dist((nbr, nbg, nbb), fg) <= COLOR_TOLERANCE and bg_key is None:
                        run += 1
                        continue
                elif not ntt and nbt:
                    if col_dist((ntr, ntg, ntb), fg) <= COLOR_TOLERANCE and bg_key is None:
                        run += 1
                        continue
                else:
                    if (col_dist((ntr, ntg, ntb), fg) <= COLOR_TOLERANCE and
                            col_dist((nbr, nbg, nbb), bg_key) <= COLOR_TOLERANCE):
                        run += 1
                        continue
                break

            if fg != last_fg or bg_key != last_bg:
                if last_fg is not None or last_bg is not None:
                    parts.append(ansi_reset())
                if bg_key:
                    parts.append(ansi_bg(*bg_key))
                parts.append(ansi_fg(*fg))
                last_fg = fg
                last_bg = bg_key

            parts.append(ch * run)
            x += run

        if last_fg is not None or last_bg is not None:
            parts.append(ansi_reset())
        lines.append("".join(parts))
    return "\n".join(lines)


def render_mono(img, cfg: RenderConfig):
    px = list(img.get_flattened_data())
    w, h = img.size
    thr = cfg.visible_threshold
    lines = []
    for y in range(0, h - 1, 2):
        line = ""
        for x in range(w):
            t = px[y * w + x]
            b = px[(y + 1) * w + x]
            tt = pixel_alpha(*t, cfg) < thr
            bt = pixel_alpha(*b, cfg) < thr
            if tt and bt:
                line += " "
            elif tt and not bt:
                line += "▄"
            elif not tt and bt:
                line += "▀"
            else:
                line += "▀" if luma(*t[:3]) > luma(*b[:3]) else "▄"
        lines.append(line)
    return "\n".join(lines)


def _floyd_steinberg_diffuse(lum_map, w, h, num_chars):
    for y in range(h):
        for x in range(w):
            old = lum_map[y][x]
            quantized = round(old * (num_chars - 1)) / (num_chars - 1)
            err = old - quantized
            lum_map[y][x] = quantized
            if x + 1 < w:
                lum_map[y][x + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    lum_map[y + 1][x - 1] += err * 3 / 16
                lum_map[y + 1][x] += err * 5 / 16
                if x + 1 < w:
                    lum_map[y + 1][x + 1] += err * 1 / 16


def render_ascii(img, chars: str, dither: bool = False, cfg: RenderConfig | None = None) -> str:
    if cfg is None:
        cfg = RenderConfig()
    px = list(img.get_flattened_data())
    w, h = img.size
    num_chars = len(chars)

    lum_map = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[y * w + x]
            lum_map[y][x] = pixel_density(r, g, b, a, cfg)

    if dither:
        _floyd_steinberg_diffuse(lum_map, w, h, num_chars)

    lines = []
    for y in range(h):
        line = ""
        for x in range(w):
            level = max(0.0, min(1.0, lum_map[y][x]))
            idx = min(int(level * (num_chars - 1)), num_chars - 1)
            line += chars[idx]
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render ananta.svg to ANSI terminal art.")
    parser.add_argument("--svg", default="ananta.svg", help="Path to source SVG (default: ananta.svg)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: temp dir)")
    parser.add_argument("--width", type=int, nargs="+", default=[90], help="Output widths (default: 90)")

    out_grp = parser.add_argument_group("output selection")
    out_grp.add_argument("--mono-only", action="store_true", help="Only generate monochrome fallback")
    out_grp.add_argument("--color-only", action="store_true", help="Only generate ANSI color output")
    out_grp.add_argument("--ascii-only", action="store_true", help="Only generate ASCII fallback")

    ascii_grp = parser.add_argument_group("ASCII options")
    ascii_grp.add_argument("--ascii-palette", choices=list(ASCII_PALETTES.keys()), default="clean")
    ascii_grp.add_argument("--ascii-chars", default=None)
    ascii_grp.add_argument("--ascii-dither", action="store_true")

    img_grp = parser.add_argument_group("image tuning")
    img_grp.add_argument("--height-ratio", type=float, default=0.48)
    img_grp.add_argument("--render-size", type=int, default=800)
    img_grp.add_argument("--white-luma", type=int, default=240)
    img_grp.add_argument("--falloff", type=int, default=10)
    img_grp.add_argument("--alpha-cutoff", type=int, default=200)
    img_grp.add_argument("--visible-threshold", type=float, default=0.3)
    img_grp.add_argument("--contrast", type=float, default=1.0)
    img_grp.add_argument("--gamma", type=float, default=1.0)
    img_grp.add_argument("--invert", action="store_true")
    img_grp.add_argument("--trim", action="store_true")
    img_grp.add_argument("--trim-padding", type=int, default=2)

    args = parser.parse_args()

    cfg = RenderConfig(
        white_luma=args.white_luma,
        falloff=args.falloff,
        alpha_cutoff=args.alpha_cutoff,
        visible_threshold=args.visible_threshold,
        height_ratio=args.height_ratio,
        render_size=args.render_size,
        contrast=args.contrast,
        gamma=args.gamma,
        invert=args.invert,
        trim=args.trim,
        trim_padding=args.trim_padding,
    )

    svg_path = os.path.abspath(args.svg)
    if not os.path.exists(svg_path):
        print(f"Error: SVG not found at {svg_path}", file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        out_dir = os.path.abspath(args.output_dir)
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = tempfile.mkdtemp(prefix="ananta-logo-")

    with tempfile.TemporaryDirectory() as tmp:
        png_path = os.path.join(tmp, "rendered.png")
        svg_to_png(svg_path, png_path, width=cfg.render_size)

        for w in args.width:
            img = load_image(png_path, w, cfg)

            if args.ascii_only:
                chars = args.ascii_chars or ASCII_PALETTES[args.ascii_palette]
                art = render_ascii(img, chars, dither=args.ascii_dither, cfg=cfg)
                dest = os.path.join(out_dir, f"ascii_fallback_{w}.txt")
                with open(dest, "w") as f:
                    f.write(art)
                print(f"  -> {dest}")
                continue

            if not args.mono_only:
                art = render_ansi(img, cfg)
                dest = os.path.join(out_dir, f"ansi_halfblock_{w}.txt")
                with open(dest, "w") as f:
                    f.write(art)
                print(f"  -> {dest}")
            if not args.color_only:
                art_m = render_mono(img, cfg)
                dest = os.path.join(out_dir, f"mono_fallback_{w}.txt")
                with open(dest, "w") as f:
                    f.write(art_m)
                print(f"  -> {dest}")

    if not args.output_dir:
        print(f"\nOutput in temporary directory: {out_dir}")
        print("Use --output-dir to specify a permanent location.")


if __name__ == "__main__":
    main()
