#!/usr/bin/env python3
"""
Render ananta.svg as high-quality ANSI terminal art.

Produces:
  - ANSI TrueColor half-block with optimized codes
  - ANSI TrueColor half-block with grouped pixels (compact)
  - Monochrome fallback
"""

from PIL import Image
import os

SVG_PATH = "ananta.svg"
PNG_PATH = ".tmp/ananta-rendered.png"
OUT_DIR = ".tmp/terminal-logo"
os.makedirs(OUT_DIR, exist_ok=True)

WHITE_THRESHOLD = 230
ALPHA_THRESHOLD = 200


def is_white_or_transparent(r, g, b, a=255):
    return a < ALPHA_THRESHOLD or (r > WHITE_THRESHOLD and g > WHITE_THRESHOLD and b > WHITE_THRESHOLD)


def load_image(width, height_ratio=0.45):
    img = Image.open(PNG_PATH).convert("RGBA")
    aspect = img.height / img.width
    new_w = width
    new_h = max(1, int(new_w * aspect * height_ratio))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


def ansi_fg(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


def ansi_bg(r, g, b):
    return f"\x1b[48;2;{r};{g};{b}m"


def ansi_reset():
    return "\x1b[0m"


def render_optimized(img):
    """Render with ANSI optimization: group same-color runs."""
    pixels = list(img.getdata())
    w, h = img.size
    lines = []
    for y in range(0, h - 1, 2):
        line_parts = []
        last_fg = None
        last_bg = None
        x = 0
        while x < w:
            top = pixels[y * w + x]
            bot = pixels[(y + 1) * w + x]
            top_trans = is_white_or_transparent(*top)
            bot_trans = is_white_or_transparent(*bot)

            if top_trans and bot_trans:
                if last_fg is not None or last_bg is not None:
                    line_parts.append(ansi_reset())
                    last_fg = None
                    last_bg = None
                line_parts.append(" ")
                x += 1
                continue

            tr, tg, tb, ta = top
            br, bg, bb, ba = bot

            if top_trans and not bot_trans:
                ch = "▄"
                fg_key = (br, bg, bb)
                bg_key = None
            elif not top_trans and bot_trans:
                ch = "▀"
                fg_key = (tr, tg, tb)
                bg_key = None
            else:
                ch = "▀"
                fg_key = (tr, tg, tb)
                bg_key = (br, bg, bb)

            # Find run of same pixels
            run = 1
            while x + run < w:
                nt = pixels[y * w + x + run]
                nb = pixels[(y + 1) * w + x + run]
                nt_t = is_white_or_transparent(*nt)
                nb_t = is_white_or_transparent(*nb)
                if nt_t and nb_t:
                    break
                if not nt_t and not nb_t:
                    if (nt[0], nt[1], nt[2]) == fg_key and (nb[0], nb[1], nb[2]) == bg_key:
                        run += 1
                        continue
                if nt_t and not nb_t:
                    if (nb[0], nb[1], nb[2]) == fg_key:
                        run += 1
                        continue
                if not nt_t and nb_t:
                    if (nt[0], nt[1], nt[2]) == fg_key:
                        run += 1
                        continue
                break

            if fg_key != last_fg or bg_key != last_bg:
                if last_fg is not None or last_bg is not None:
                    line_parts.append(ansi_reset())
                if bg_key:
                    line_parts.append(ansi_bg(*bg_key))
                line_parts.append(ansi_fg(*fg_key))
                last_fg = fg_key
                last_bg = bg_key

            line_parts.append(ch * run)
            x += run

        if last_fg is not None or last_bg is not None:
            line_parts.append(ansi_reset())
        lines.append("".join(line_parts))
    return "\n".join(lines)


def render_simple_halfblock(img):
    """Simple half-block without optimization (per-pixel codes)."""
    pixels = list(img.getdata())
    w, h = img.size
    lines = []
    for y in range(0, h - 1, 2):
        line = ""
        for x in range(w):
            top = pixels[y * w + x]
            bot = pixels[(y + 1) * w + x]
            top_trans = is_white_or_transparent(*top)
            bot_trans = is_white_or_transparent(*bot)
            if top_trans and bot_trans:
                line += " "
                continue
            tr, tg, tb, ta = top
            br, bg, bb, ba = bot

            if top_trans and not bot_trans:
                line += ansi_fg(br, bg, bb) + "▄"
            elif not top_trans and bot_trans:
                line += ansi_fg(tr, tg, tb) + "▀"
            else:
                line += ansi_bg(br, bg, bb) + ansi_fg(tr, tg, tb) + "▀"
        if not all(is_white_or_transparent(*pixels[y * w + x]) and
                    is_white_or_transparent(*pixels[(y + 1) * w + x])
                    for x in range(w)):
            line += ansi_reset()
        lines.append(line)
    return "\n".join(lines)


def render_monochrome(img):
    """Monochrome fallback using ▀▄ chars."""
    pixels = list(img.getdata())
    w, h = img.size
    lines = []
    for y in range(0, h - 1, 2):
        line = ""
        for x in range(w):
            top = pixels[y * w + x]
            bot = pixels[(y + 1) * w + x]
            top_trans = is_white_or_transparent(*top)
            bot_trans = is_white_or_transparent(*bot)
            if top_trans and bot_trans:
                line += " "
            elif top_trans and not bot_trans:
                line += "▄"
            elif not top_trans and bot_trans:
                line += "▀"
            else:
                tb = top[0] * 0.299 + top[1] * 0.587 + top[2] * 0.114
                bb = bot[0] * 0.299 + bot[1] * 0.587 + bot[2] * 0.114
                line += "▀" if tb > bb else "▄"
        lines.append(line)
    return "\n".join(lines)


def save_output(content, filename):
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w") as f:
        f.write(content)
    print(f"  Saved: {path}")


def main():
    widths = [80, 100, 120]

    for w in widths:
        print(f"\n{'='*70}")
        print(f"  WIDTH = {w}")
        print(f"{'='*70}")

        # --- ANSI Optimized ---
        print(f"\n--- ANSI Color Half-Block ({w}) ---")
        img = load_image(w, height_ratio=0.45)
        art = render_optimized(img)
        save_output(art, f"ansi_color_{w}.txt")
        print(art)

        # --- Monochrome ---
        print(f"\n--- Monochrome Fallback ({w}) ---")
        art_m = render_monochrome(img)
        save_output(art_m, f"mono_{w}.txt")
        print(art_m)


if __name__ == "__main__":
    main()
