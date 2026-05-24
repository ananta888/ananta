#!/usr/bin/env python3
"""
Smoke test for the animated 3D ASCII logo.

Renders all animation frames in-process and either plays them live
or saves them as an asciinema v2 cast file.

Usage
-----
Live animation (requires real terminal):
    .venv/bin/python scripts/smoke_logo.py

Different preset:
    .venv/bin/python scripts/smoke_logo.py --preset snake_orbit
    .venv/bin/python scripts/smoke_logo.py --preset depth_pulse

Record to cast file (ANSI colours preserved):
    .venv/bin/python scripts/smoke_logo.py --record
    python scripts/play_cast.py tests/output/operator_tui_splash.cast

Headless check (e.g. in CI or pytest):
    .venv/bin/python scripts/smoke_logo.py --check
    echo $?   # 0 = OK
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_CAST_FILE = Path(__file__).parent.parent / "tests" / "output" / "operator_tui_splash.cast"


# ── capability / backend setup ───────────────────────────────────────────────

def _make_cap(preset: str, width: int, height: int, fps: int, duration_ms: int):
    from client_surfaces.operator_tui.animation3d.capabilities import detect_3d_capability
    return detect_3d_capability(
        terminal_width=width,
        terminal_height=height,
        is_tty=True,            # force-enable even in headless contexts
        no_color=bool(os.environ.get("NO_COLOR")),
        env={
            "ANANTA_TUI_3D": "1",
            "ANANTA_TUI_3D_PRESET": preset,
            "ANANTA_TUI_3D_FPS": str(fps),
            "ANANTA_TUI_3D_DURATION_MS": str(duration_ms),
        },
    )


def _render_frames(preset: str, width: int, height: int, fps: int, duration_ms: int) -> list[str]:
    """Render all animation frames, return list of raw strings (ANSI included)."""
    from client_surfaces.operator_tui.animation3d.backends import BuiltinBackend

    cap = _make_cap(preset, width, height, fps, duration_ms)
    if not cap.enabled:
        print(f"[smoke_logo] 3D disabled: {cap.reason_code}", file=sys.stderr)
        return []

    backend = BuiltinBackend()
    total = max(1, int(cap.max_fps * cap.duration_ms / 1000))
    frames: list[str] = []
    for i in range(total):
        t = i / cap.max_fps
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


# ── output modes ─────────────────────────────────────────────────────────────

def play_live(frames: list[str], fps: int, repeat: int = 1) -> None:
    """Play frames directly in the terminal."""
    interval = 1.0 / fps
    sys.stdout.write("\x1b[?25l")   # hide cursor
    try:
        for _ in range(repeat):
            for frame in frames:
                sys.stdout.write(f"\x1b[H{frame}")   # cursor home + frame
                sys.stdout.flush()
                time.sleep(interval)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")  # show cursor + clear
        sys.stdout.flush()


def save_cast(frames: list[str], fps: int, path: Path, preset: str) -> None:
    """Write asciinema v2 cast file with ANSI colours preserved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    interval = 1.0 / fps
    header = {
        "version": 2,
        "width": shutil.get_terminal_size((120, 32)).columns,
        "height": shutil.get_terminal_size((120, 32)).lines,
        "title": f"Ananta 3D Logo – {preset}",
        "env": {"TERM": "xterm-256color"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for i, frame in enumerate(frames):
        t = round(i * interval, 4)
        lines.append(json.dumps([t, "o", f"\x1b[H{frame}"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[smoke_logo] saved {len(frames)} frames → {path}", file=sys.stderr)


def check_headless(frames: list[str]) -> bool:
    """Return True if frames look reasonable (non-empty, contain logo drawing chars)."""
    if not frames:
        return False
    import re
    _strip = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")
    mid  = _strip.sub("", frames[len(frames) // 2])
    last = _strip.sub("", frames[-1])
    # 3D renderer uses /, \, |, - and snake chars s S o O c C ~
    logo_chars = set(r"/\|-sSOoCc~")
    has_content    = len(mid.strip()) > 0
    has_logo_chars = bool(logo_chars & set(mid))
    # animation should produce different frames (not all identical)
    frames_differ  = _strip.sub("", frames[0]) != last
    return has_content and has_logo_chars and frames_differ


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset",      default="rotate_in",
                    choices=["rotate_in", "snake_orbit", "depth_pulse"])
    ap.add_argument("--fps",         type=int, default=24)
    ap.add_argument("--duration-ms", type=int, default=2000)
    ap.add_argument("--width",       type=int, default=0,   help="0 = auto-detect")
    ap.add_argument("--height",      type=int, default=0,   help="0 = auto-detect")
    ap.add_argument("--repeat",      type=int, default=1,   help="loop count for live mode")
    ap.add_argument("--record",      action="store_true",   help="save cast file instead of live play")
    ap.add_argument("--check",       action="store_true",   help="headless check, exit 0/1")
    ap.add_argument("--output",      default=str(_CAST_FILE))
    args = ap.parse_args(argv)

    w = args.width  or shutil.get_terminal_size((120, 32)).columns
    h = args.height or shutil.get_terminal_size((120, 32)).lines

    frames = _render_frames(args.preset, w, h, args.fps, args.duration_ms)

    if args.check:
        ok = check_headless(frames)
        print(f"[smoke_logo] check={'ok' if ok else 'FAIL'}  frames={len(frames)}")
        return 0 if ok else 1

    if args.record:
        save_cast(frames, args.fps, Path(args.output), args.preset)
        return 0

    if not frames:
        print("[smoke_logo] no frames rendered – check terminal size / env vars", file=sys.stderr)
        return 1

    play_live(frames, args.fps, repeat=args.repeat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
