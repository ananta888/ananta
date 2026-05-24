#!/usr/bin/env python3
"""
Visual smoke test for the Ananta logo – 2D then 3D.

Modes
-----
2D (ANSI halfblock from SVG-generated assets):
    .venv/bin/python scripts/smoke_logo.py --2d

3D (animated wireframe built from SVG silhouette):
    .venv/bin/python scripts/smoke_logo.py --3d
    .venv/bin/python scripts/smoke_logo.py --3d --preset snake_orbit
    .venv/bin/python scripts/smoke_logo.py --3d --preset depth_pulse

Both modes in sequence:
    .venv/bin/python scripts/smoke_logo.py           (default)

Record to cast file (tests/output/):
    .venv/bin/python scripts/smoke_logo.py --record

Headless check (CI):
    .venv/bin/python scripts/smoke_logo.py --check
    echo $?    # 0 = ok
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
_CAST_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "output")


# ── 2D ───────────────────────────────────────────────────────────────────────

def show_2d(width: int = 0) -> str:
    """
    Print the SVG-derived ANSI halfblock logo and return the raw string.
    Uses the pre-rendered asset in agent/cli/assets/ansi_halfblock_*.txt
    which was generated from ananta.svg by render_terminal_logo.py.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from agent.cli.logo_assets import load_logo

    w = width or shutil.get_terminal_size((120, 32)).columns
    logo = load_logo(width=w, color=True)
    if not logo:
        print("[smoke_logo] 2D: no asset found", file=sys.stderr)
        return ""
    sys.stdout.write(logo + "\n")
    sys.stdout.flush()
    return logo


# ── 3D ───────────────────────────────────────────────────────────────────────

def _render_frames(
    preset: str,
    width: int,
    height: int,
    fps: int,
    duration_ms: int,
) -> list[str]:
    """Render all animation frames; return list of raw strings (ANSI included)."""
    from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend
    from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability

    cap = detect_3d_capability(
        terminal_width=width,
        terminal_height=height,
        is_tty=True,
        env={
            "ANANTA_TUI_3D": "1",
            "ANANTA_TUI_3D_PRESET": preset,
            "ANANTA_TUI_3D_FPS": str(fps),
            "ANANTA_TUI_3D_DURATION_MS": str(duration_ms),
        },
    )

    backend = BuiltinBackend()
    source = "svg" if backend._a_model.label == "ananta_svg" else "hardcoded-A"
    print(f"[smoke_logo] 3D geometry source: {source}  "
          f"verts={len(backend._a_model.vertices)}  "
          f"edges={len(backend._a_model.edges)}",
          file=sys.stderr)

    total = max(1, int(cap.max_fps * cap.duration_ms / 1000))
    frames: list[str] = []
    for i in range(total):
        t = i / max(1, cap.max_fps)
        result = backend.frame_at(
            t=t,
            width=width,
            height=height,
            options={
                "preset": cap.preset_name,
                "color_mode": cap.color_mode,
                "no_color": cap.color_mode in ("mono", "plain_ascii"),
                "no_ansi": cap.color_mode == "plain_ascii",
            },
        )
        frames.append(result.text or "")
    return frames


def show_3d(
    preset: str = "rotate_in",
    fps: int = 24,
    duration_ms: int = 2000,
    width: int = 0,
    height: int = 0,
    repeat: int = 1,
) -> list[str]:
    """Play the 3D animation live in the terminal."""
    sz = shutil.get_terminal_size((120, 32))
    w = width or sz.columns
    h = height or sz.lines

    frames = _render_frames(preset, w, h, fps, duration_ms)
    if not frames:
        print("[smoke_logo] 3D: no frames rendered", file=sys.stderr)
        return frames

    interval = 1.0 / fps
    sys.stdout.write("\x1b[?25l")   # hide cursor
    try:
        for _ in range(repeat):
            for frame in frames:
                sys.stdout.write(f"\x1b[H{frame}")
                sys.stdout.flush()
                time.sleep(interval)
    finally:
        sys.stdout.write("\x1b[?25h")   # restore cursor
        sys.stdout.flush()
    return frames


# ── recording ─────────────────────────────────────────────────────────────────

def _save_cast(frames: list[str], fps: int, path: str, title: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sz = shutil.get_terminal_size((120, 32))
    header = {
        "version": 2,
        "width": sz.columns,
        "height": sz.lines,
        "title": title,
        "env": {"TERM": "xterm-256color"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    interval = 1.0 / fps
    for i, frame in enumerate(frames):
        lines.append(json.dumps([round(i * interval, 4), "o", f"\x1b[H{frame}"]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[smoke_logo] saved {len(frames)} frames → {path}", file=sys.stderr)


def record(preset: str = "rotate_in", fps: int = 24, duration_ms: int = 2000) -> None:
    sz = shutil.get_terminal_size((120, 32))
    frames = _render_frames(preset, sz.columns, sz.lines, fps, duration_ms)
    _save_cast(
        frames, fps,
        os.path.join(_CAST_DIR, "operator_tui_splash.cast"),
        f"Ananta 3D Logo – {preset}",
    )


# ── check (CI / headless) ─────────────────────────────────────────────────────

def check() -> bool:
    """Return True if both 2D and 3D render successfully."""
    # 2D check
    logo = show_2d(width=90)
    has_2d = bool(logo and len(logo.strip()) > 10)

    # 3D check
    frames = _render_frames("rotate_in", 120, 32, 24, 2000)
    if not frames:
        print("[smoke_logo] check: 3D produced no frames", file=sys.stderr)
        return False

    mid = _ANSI_RE.sub("", frames[len(frames) // 2])
    logo_chars = set(r"/\|-sSOoCc~")
    has_3d = bool(mid.strip()) and bool(logo_chars & set(mid))
    frames_differ = _ANSI_RE.sub("", frames[0]) != _ANSI_RE.sub("", frames[-1])

    ok = has_2d and has_3d and frames_differ
    print(f"[smoke_logo] check: 2d={has_2d}  3d={has_3d}  animated={frames_differ}  → {'ok' if ok else 'FAIL'}",
          file=sys.stderr)
    return ok


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--2d",      dest="mode_2d",   action="store_true", help="2D only")
    mode.add_argument("--3d",      dest="mode_3d",   action="store_true", help="3D only")
    mode.add_argument("--record",  dest="mode_rec",  action="store_true", help="record 3D to cast")
    mode.add_argument("--check",   dest="mode_check",action="store_true", help="headless CI check")

    ap.add_argument("--preset",      default="rotate_in",
                    choices=["rotate_in", "snake_orbit", "depth_pulse"])
    ap.add_argument("--fps",         type=int, default=24)
    ap.add_argument("--duration-ms", type=int, default=2000)
    ap.add_argument("--repeat",      type=int, default=1)
    ap.add_argument("--width",       type=int, default=0)
    ap.add_argument("--height",      type=int, default=0)
    args = ap.parse_args(argv)

    if args.mode_check:
        return 0 if check() else 1

    if args.mode_rec:
        record(args.preset, args.fps, args.duration_ms)
        return 0

    if args.mode_2d:
        show_2d(args.width)
        return 0

    if args.mode_3d:
        show_3d(args.preset, args.fps, args.duration_ms,
                args.width, args.height, args.repeat)
        return 0

    # default: 2D then 3D
    print("\n── 2D ANSI halfblock (from SVG assets) ──", file=sys.stderr)
    show_2d(args.width)
    time.sleep(1.5)

    print("\n── 3D wireframe (built from SVG silhouette) ──", file=sys.stderr)
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()
    show_3d(args.preset, args.fps, args.duration_ms,
            args.width, args.height, args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
