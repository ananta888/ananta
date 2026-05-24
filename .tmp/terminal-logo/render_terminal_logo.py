#!/usr/bin/env python3
"""
Final improved ANSI terminal logo renderer for ananta.svg.

Renders SVG -> PNG -> ANSI TrueColor half-block art with:
- Smooth white-background removal (luma-based with falloff)
- No color quantization (true colors from SVG)
- Smart run-length encoding with color tolerance
- Widths: 120 (large), 90 (medium), monochrome fallback
"""

from PIL import Image
import os

SVG_PATH = "ananta.svg"
PNG_PATH = ".tmp/ananta-rendered.png"
OUT_DIR = ".tmp/terminal-logo"
os.makedirs(OUT_DIR, exist_ok=True)

WHITE_LUMA = 240
FALLOFF = 10
COLOR_TOLERANCE = 25  # max squared distance for color grouping


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def pixel_alpha(r, g, b, a=255):
    if a < 200:
        return 0.0
    lum = luma(r, g, b)
    if lum >= WHITE_LUMA:
        return 0.0
    if lum >= WHITE_LUMA - FALLOFF:
        return (WHITE_LUMA - lum) / FALLOFF
    return 1.0


def is_transparent(r, g, b, a=255):
    return pixel_alpha(r, g, b, a) < 0.3


def col_dist(a, b):
    return (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2


def load_image(width, height_ratio=0.48):
    img = Image.open(PNG_PATH).convert("RGBA")
    aspect = img.height / img.width
    new_w = width
    new_h = max(1, int(new_w * aspect * height_ratio))
    return img.resize((new_w, new_h), Image.LANCZOS)


def ansi_fg(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


def ansi_bg(r, g, b):
    return f"\x1b[48;2;{r};{g};{b}m"


def ansi_reset():
    return "\x1b[0m"


def render_ansi(img):
    """Render to ANSI TrueColor half-block art with tolerance-based run grouping."""
    px = list(img.getdata())
    w, h = img.size

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
            ta2 = pixel_alpha(tr, tg, tb, ta)
            ba2 = pixel_alpha(br, bg, bb, ba)
            tt = ta2 < 0.3
            bt = ba2 < 0.3

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

            # Find run of similar colors
            run = 1
            while x + run < w:
                ntr, ntg, ntb, nta = g(x + run, y)
                nbr, nbg, nbb, nba = g(x + run, y + 1)
                nta2 = pixel_alpha(ntr, ntg, ntb, nta)
                nba2 = pixel_alpha(nbr, nbg, nbb, nba)
                ntt = nta2 < 0.3
                nbt = nba2 < 0.3
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


def render_mono(img):
    """Monochrome half-block fallback."""
    px = list(img.getdata())
    w, h = img.size
    lines = []
    for y in range(0, h - 1, 2):
        line = ""
        for x in range(w):
            t = px[y * w + x]
            b = px[(y + 1) * w + x]
            tt = pixel_alpha(*t) < 0.3
            bt = pixel_alpha(*b) < 0.3
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


def save(content, name):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        f.write(content)
    print(f"  -> {path}")


def main():
    configs = [
        (120, "ansi_halfblock_120"),
        (90,  "ansi_halfblock_90"),
    ]
    for w, label in configs:
        print(f"\n{'='*70}")
        print(f"  {label}  (width={w})")
        print(f"{'='*70}")
        img = load_image(w)
        art = render_ansi(img)
        save(art, f"{label}.txt")
        print(art)

    print(f"\n{'='*70}")
    print(f"  mono_fallback_90 (width=90)")
    print(f"{'='*70}")
    img_m = load_image(90)
    art_m = render_mono(img_m)
    save(art_m, "mono_fallback_90.txt")
    print(art_m)


if __name__ == "__main__":
    main()
