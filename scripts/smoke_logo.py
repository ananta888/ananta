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

# ── splash animation ─────────────────────────────────────────────────────────

_GREEN   = '\x1b[38;2;71;166;56m'   # #47A638 – SVG green
_RESET_C = '\x1b[0m'
_SNAKE_CHARS = '~sSoOcC'


def _render_svg_ascii(logo_width: int, color: bool = True) -> list[str]:
    """Render ananta.svg as colored (or plain) ASCII art; returns lines."""
    import sys as _sys
    import tempfile as _tmp
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _sys.path.insert(0, script_dir)
    try:
        from render_terminal_logo import (
            ASCII_PALETTES, RenderConfig, load_image,
            render_ascii, render_ascii_color, svg_to_png,
        )
    except ImportError:
        return []

    svg_path = os.path.abspath(os.path.join(script_dir, '..', 'ananta.svg'))
    if not os.path.isfile(svg_path):
        return []

    cfg = RenderConfig()
    chars = ASCII_PALETTES['clean']

    with _tmp.NamedTemporaryFile(suffix='.png', delete=False) as f:
        png = f.name
    try:
        svg_to_png(svg_path, png, width=800)
        img = load_image(png, logo_width, cfg)
        if color:
            art = render_ascii_color(img, chars)
        else:
            art = render_ascii(img, chars, cfg)
    finally:
        try:
            os.unlink(png)
        except OSError:
            pass

    return art.split('\n')


def _orbit_path(top: int, bottom: int, left: int, right: int) -> list[tuple[int, int]]:
    p: list[tuple[int, int]] = []
    for c in range(left, right + 1):          p.append((top, c))
    for r in range(top + 1, bottom + 1):      p.append((r, right))
    for c in range(right - 1, left - 1, -1):  p.append((bottom, c))
    for r in range(bottom - 1, top, -1):       p.append((r, left))
    return p


def _insert_snake_on_row(
    base: str,
    snake_cols: dict[int, str],
    logo_col_start: int,
    canvas_w: int,
) -> str:
    """
    Insert green snake chars at `snake_cols` positions into a canvas row.
    `base` is the pre-rendered logo line (possibly ANSI-colored) starting at col 0.
    Snake chars are only placed in empty regions (before logo_col_start or after logo end).
    """
    if not snake_cols:
        return base.ljust(canvas_w)

    # Positions left of the logo and right of the logo
    left_snakes  = {c: ch for c, ch in snake_cols.items() if c < logo_col_start}
    right_snakes = {c: ch for c, ch in snake_cols.items() if c >= logo_col_start + 70}

    if not left_snakes and not right_snakes:
        return base.ljust(canvas_w)

    # Left region (cols 0..logo_col_start-1): pure spaces + snake chars
    left_part = list(' ' * logo_col_start)
    for c, ch in left_snakes.items():
        if 0 <= c < logo_col_start:
            left_part[c] = ch

    def _colorize_region(chars_list: list[str], snake_set: dict[int, str], col_offset: int) -> str:
        out, in_g = '', False
        for i, ch in enumerate(chars_list):
            col = i + col_offset
            if col in snake_set:
                if not in_g:
                    out += _GREEN
                    in_g = True
                out += ch
            else:
                if in_g:
                    out += _RESET_C
                    in_g = False
                out += ch
        if in_g:
            out += _RESET_C
        return out

    left_str = _colorize_region(left_part, left_snakes, 0)

    # Logo region (already ANSI-colored): pass through unchanged
    logo_start = logo_col_start
    # Strip the plain leading pad from base (base = ' '*logo_col_start + logo_line)
    logo_str = base[logo_col_start:] if len(base) > logo_col_start else ''

    # Right region (cols logo_end..canvas_w-1): pure spaces + snake chars
    logo_end = logo_col_start + 70
    right_len = canvas_w - logo_end
    right_part = list(' ' * right_len)
    for c, ch in right_snakes.items():
        idx = c - logo_end
        if 0 <= idx < right_len:
            right_part[idx] = ch

    right_str = _colorize_region(right_part, {c - logo_end: ch for c, ch in right_snakes.items()
                                               if 0 <= c - logo_end < right_len}, 0)

    return left_str + logo_str + right_str


def _frame_from_logo(
    logo_lines: list[str],
    logo_col: int,
    canvas_w: int,
    canvas_h: int,
    n_logo_rows: int,
    snake_map: dict[tuple[int, int], str],
) -> str:
    """Build one complete frame string."""
    rows_out: list[str] = []
    logo_h = min(n_logo_rows, len(logo_lines))

    for row in range(canvas_h):
        if row < logo_h:
            base = ' ' * logo_col + logo_lines[row]
        else:
            base = ''

        row_snakes = {c: ch for (r, c), ch in snake_map.items() if r == row}

        if not row_snakes:
            rows_out.append(base.ljust(canvas_w) if base else ' ' * canvas_w)
        elif row >= logo_h:
            # Empty row: just place snake chars
            line = list(' ' * canvas_w)
            parts, in_g = '', False
            for ci in range(canvas_w):
                if ci in row_snakes:
                    if not in_g:
                        parts += _GREEN; in_g = True
                    parts += row_snakes[ci]
                else:
                    if in_g:
                        parts += _RESET_C; in_g = False
                    parts += line[ci]
            if in_g:
                parts += _RESET_C
            rows_out.append(parts)
        else:
            rows_out.append(_insert_snake_on_row(base, row_snakes, logo_col, canvas_w))

    return '\n'.join(rows_out)


def _build_splash_frames(w: int = 120, h: int = 32, fps: int = 24) -> list[str]:
    """
    Animation: SVG logo reveals → snake wraps around it → shrinks to corner.
    Uses the real ananta.svg rendered as colored ASCII at 70 columns.
    Falls back gracefully if rendering libraries are unavailable.
    """
    # ── render logo at three sizes ────────────────────────────────────────────
    logo_lg = _render_svg_ascii(70, color=True)   # fits 32-row canvas
    logo_sm = _render_svg_ascii(35, color=False)  # for shrink phase

    if not logo_lg:
        print('[smoke_logo] SVG render unavailable, skipping splash frames',
              file=sys.stderr)
        return []

    # Center logo horizontally
    logo_w = 70
    col0 = (w - logo_w) // 2   # left padding (cols 0..col0-1 are empty)

    # Detect content bounds (non-blank rows/cols) for tight snake orbit
    strip = _ANSI_RE
    content_rows = [i for i, ln in enumerate(logo_lg) if strip.sub('', ln).strip()]
    if not content_rows:
        content_rows = list(range(len(logo_lg)))
    ct, cb = content_rows[0], content_rows[-1]

    # Orbit path slightly outside the content bounding box
    # Left/right borders are in the empty padding cols around the logo
    pad_r, pad_c = 2, 3
    orbit_top    = max(0,     ct - pad_r)
    orbit_bottom = min(h - 1, cb + pad_r)
    orbit_left   = max(0,     col0 - pad_c)
    orbit_right  = min(w - 1, col0 + logo_w + pad_c)
    path = _orbit_path(orbit_top, orbit_bottom, orbit_left, orbit_right)
    plen = len(path)

    frames: list[str] = []

    # ── phase 1: logo reveals line-by-line (0.5 s) ───────────────────────────
    for fi in range(max(1, fps // 2)):
        n = int((fi + 1) / max(1, fps // 2) * len(logo_lg))
        frames.append(_frame_from_logo(logo_lg, col0, w, h, n, {}))

    # ── phase 2: snake grows around logo (1.5 s) ─────────────────────────────
    grow = int(fps * 1.5)
    for fi in range(grow):
        n_s = int((fi + 1) / grow * plen)
        snake_map = {path[k]: _SNAKE_CHARS[(k + fi) % len(_SNAKE_CHARS)]
                     for k in range(n_s)}
        frames.append(_frame_from_logo(logo_lg, col0, w, h, len(logo_lg), snake_map))

    # ── phase 3: full snake orbits slowly (1 s) ──────────────────────────────
    for fi in range(fps):
        off = fi * 2
        snake_map = {path[(off + k) % plen]: _SNAKE_CHARS[(k + off) % len(_SNAKE_CHARS)]
                     for k in range(plen)}
        frames.append(_frame_from_logo(logo_lg, col0, w, h, len(logo_lg), snake_map))

    # ── phase 4: shrink toward top-left (1 s) ────────────────────────────────
    last_off = fps * 2
    shrink = fps
    col_sm = (w - 35) // 2

    for fi in range(shrink):
        t = fi / shrink
        if t < 0.5:
            art, a_col = logo_lg, col0
        else:
            art, a_col = (logo_sm if logo_sm else logo_lg), (col_sm if logo_sm else col0)

        # Interpolate position toward top-left
        target_col = max(0, a_col - int(a_col * t * 2))
        snake_visible = int(plen * max(0.0, 1.0 - t * 2))
        snake_map = {path[(last_off + k) % plen]: _SNAKE_CHARS[(k + last_off) % len(_SNAKE_CHARS)]
                     for k in range(snake_visible)}
        frames.append(_frame_from_logo(art, target_col, w, h, len(art), snake_map))

    return frames


_MOCK_PANEL_STATES = None   # populated lazily inside _render_tui_snapshot
_MOCK_PAYLOADS: dict = {
    "dashboard": {
        "agents": {"online": 7, "total": 8},
        "llm_providers": {"claude-sonnet-4-6": "ok", "codex-2": "ok", "whisper-v3": "ok"},
        "queue": {"depth": 3},
        "goal_summary": "2 running · 5 done · 0 failed",
        "task_summary": "3 active · 12 completed",
    },
    "goals": {
        "items": [
            {"id": "g-001", "status": "running", "title": "WebSocket live updates"},
            {"id": "g-002", "status": "running", "title": "Voice command recognition"},
            {"id": "g-003", "status": "done",    "title": "Refactor auth middleware"},
            {"id": "g-004", "status": "done",    "title": "Multi-agent orchestrator"},
            {"id": "g-005", "status": "done",    "title": "SVG logo 3D renderer"},
            {"id": "g-006", "status": "blocked", "title": "External API rate limit fix"},
        ],
    },
    "tasks": {
        "items": [
            {"id": "t-0a1", "status": "running", "agent": "claude", "title": "Auth flow unit tests"},
            {"id": "t-0a2", "status": "running", "agent": "claude", "title": "Generate OpenAPI spec"},
            {"id": "t-0a3", "status": "running", "agent": "codex",  "title": "Refactor connection pool"},
            {"id": "t-0a4", "status": "done",    "agent": "claude", "title": "Fix ANSI rendering bug"},
            {"id": "t-0a5", "status": "done",    "agent": "claude", "title": "Add 3D SVG logo animation"},
            {"id": "t-0a6", "status": "done",    "agent": "codex",  "title": "Deploy landing page"},
        ],
        "timeline": [
            {"id": "t-0a4", "summary": "ANSI strip fix committed"},
            {"id": "t-0a5", "summary": "SVG logo animates in 3D"},
            {"id": "t-0a6", "summary": "www.ananta.de is live"},
        ],
    },
    "system": {
        "agents": {"online": 7, "total": 8},
        "llm_providers": {
            "claude-sonnet-4-6": "ok  284ms",
            "codex-2":           "ok  110ms",
            "whisper-v3":        "ok  450ms",
        },
        "queue": {"depth": 3, "counts": {"pending": 3, "retry": 0}},
        "contracts": ["hub v2.1.4", "codex v1.0.3", "voice v0.4.1", "web v3.2.0"],
    },
}


def _render_tui_snapshot(section_id: str, width: int, height: int) -> str:
    """Render a single TUI frame with rich mock data."""
    from client_surfaces.operator_tui.models import (
        FocusPane, OperatorMode, OperatorState, PanelState,
    )
    from client_surfaces.operator_tui.renderer import render_operator_shell

    panel_states = {k: PanelState.HEALTHY for k in _MOCK_PAYLOADS}
    state = OperatorState(
        endpoint="http://localhost:5000",
        auth_state="token",
        mode=OperatorMode.NORMAL,
        focus=FocusPane.CONTENT,
        section_id=section_id,
        status_message="ready",
        panel_states=panel_states,
        section_payloads=_MOCK_PAYLOADS,
    )
    return render_operator_shell(state, width=width, height=height)


def record(fps: int = 24, **_: object) -> None:
    """Record operator_tui_splash.cast: logo animation → TUI dashboard overview."""
    w, h = 120, 32

    events: list[tuple[float, str]] = []
    interval = 1.0 / fps
    t = 0.0

    # ── Phase 1: logo splash (A appears + snake wraps + shrinks) ─────────────
    splash_frames = _build_splash_frames(w, h, fps)
    for frame in splash_frames:
        events.append((t, f"\x1b[2J\x1b[H{frame}"))
        t += interval

    # ── Phase 2: TUI sections ─────────────────────────────────────────────────
    section_specs = [
        ("dashboard", 1.5),
        ("goals",     1.0),
        ("tasks",     1.0),
        ("system",    0.8),
    ]
    for section_id, dur in section_specs:
        text = _render_tui_snapshot(section_id, w, h)
        count = max(1, int(fps * dur))
        for i in range(count):
            prefix = "\x1b[2J\x1b[H" if i == 0 else "\x1b[H"
            events.append((t, f"{prefix}{text}"))
            t += interval

    # ── Save ──────────────────────────────────────────────────────────────────
    path = os.path.join(_CAST_DIR, "operator_tui_splash.cast")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = {
        "version": 2, "width": w, "height": h,
        "title": "Ananta – snake wraps A → TUI",
        "env": {"TERM": "xterm-256color"},
    }
    json_lines = [json.dumps(header, ensure_ascii=False)]
    for ts, data in events:
        json_lines.append(json.dumps([round(ts, 4), "o", data], ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(json_lines) + "\n")
    print(
        f"[smoke_logo] saved {len(events)} events → {path}\n"
        f"[smoke_logo]   splash={len(splash_frames)}  tui_sections={len(section_specs)}"
        f"  total_s={t:.1f}",
        file=sys.stderr,
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
        record(fps=args.fps)
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
