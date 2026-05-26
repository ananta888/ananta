# Terminal Header Logo Renderer

## Current baseline path (before protocol-specific renderers)

The compact TUI header logo currently uses this deterministic path:

1. `ananta.svg`
2. rasterized to PNG via `scripts/render_terminal_logo.py` (`svg_to_png`)
3. converted to terminal output (`ANSI halfblocks`, `ASCII color`, or `ASCII mono`)
4. consumed by `agent/cli/logo_layout.py` and `client_surfaces/operator_tui/renderer.py`

This keeps the persistent header stable even when optional graphics protocols are unavailable.

## Existing compact logo parameters

- `agent/cli/logo_layout.py::_SMALL_LOGO_COLS = 35`
- `agent/cli/logo_layout.py::COMPACT_HEADER_LINES = 8`

These values define the current small-logo shape used in the persistent header.

## ANSI baseline extraction decision

The reusable baseline renderer for the new logo-renderer stack is:

- `client_surfaces/operator_tui/logo_renderer/ansi_halfblock.py`

It is now used by `scripts/render_terminal_logo.py` and accepts RGBA frames while preserving:

- TrueColor halfblock output
- NO_COLOR/mono halfblock fallback
- stable 8-line header-compatible rendering

## Compatibility guardrails

- If SVG rasterization is unavailable, existing text fallback remains active.
- The TUI must continue to start in non-interactive or limited terminals.
- Optional protocol renderers (kitty/sixel) are additive and must not break ANSI baseline behavior.

## Manual smoke tests (WSL2 / Windows Terminal / Kitty family)

### Sixel on Windows 11 WSL2 + Windows Terminal

```bash
ANANTA_TUI_LOGO_RENDERER=sixel ananta tui --render-once --width 120 --height 32
```

Fallback when output looks broken:

```bash
ANANTA_TUI_LOGO_RENDERER=ansi ananta tui --render-once --width 120 --height 32
```

Sixel is a terminal image protocol. It is unrelated to WSLg/OpenGL windows.

### Kitty graphics (kitty / wezterm / ghostty)

```bash
ANANTA_TUI_LOGO_RENDERER=kitty ananta tui --render-once --width 120 --height 32
```

If the terminal does not support kitty graphics, renderer selection falls back to ANSI.

### Safe reset commands

```bash
ANANTA_TUI_LOGO_RENDERER=ansi ananta tui
ANANTA_TUI_LOGO=0 ananta tui
```

## Renderer environment variables

- `ANANTA_TUI_LOGO=0` disables the persistent header logo.
- `ANANTA_TUI_LOGO_RENDERER=auto|ansi|sixel|kitty|none` selects renderer strategy.
- `ANANTA_TUI_LOGO_ANIMATION=static|pulse|shimmer|rotate_hint` controls subtle animation presets.
- `ANANTA_TUI_LOGO_FPS=<n>` limits header animation rate (`1..16`, default `6`).
- `ANANTA_TUI_SPLASH` controls fullscreen startup splash only and remains separate from persistent header rendering.

## Terminal compatibility guidance

- **Windows Terminal + WSL2**: prefer `auto` or explicit `ansi`; test `sixel` only when terminal/image backend supports it.
- **Kitty / Ghostty / WezTerm**: `auto` can choose kitty graphics when capability is detected.
- **Simple SSH terminals**: use `ansi` or `none` for maximum reliability.

Quality overview:

- `kitty` (best image quality when supported)
- `sixel` (good bitmap quality on compatible terminals)
- `ansi` (portable default)
- `none` (disable logo)

## Optional tools and dependencies

Required runtime:

- `Pillow` (already in `pyproject.toml`)

Optional rasterizers for SVG -> PNG:

- `cairosvg` (Python)
- `rsvg-convert` (system)
- `inkscape` (system)

Optional image protocol backend:

- `img2sixel` for sixel encoding

If optional tools are missing, renderer selection degrades to ANSI and startup remains functional.

## Troubleshooting

Symptoms and fallback actions:

- **Visible escape sequences / broken glyph output**  
  `ANANTA_TUI_LOGO_RENDERER=ansi ananta tui`
- **Flicker or unstable cursor position**  
  `ANANTA_TUI_LOGO_RENDERER=ansi ANANTA_TUI_LOGO_ANIMATION=static ananta tui`
- **Logo clipped/cut**  
  increase terminal size or run `ananta tui --width 120 --height 32 --render-once`
- **Need fully clean text mode**  
  `ANANTA_TUI_LOGO=0 ananta tui`

Debug mode:

- `ANANTA_TUI_LOGO_DEBUG=1` (or `ANANTA_TUI_VERBOSE=1`) prints renderer fallback warnings to stderr.
- `ANANTA_TUI_SPLASH_DEBUG=1` enables splash/header debug file output.
- `ANANTA_TUI_SPLASH_DEBUG_PATH=/tmp/splash_debug.txt` overrides debug output target path.
- No debug files are written by default.

## Smoke commands and CI gates

Mandatory text-based CI-safe checks (no kitty/sixel terminal required):

```bash
ANANTA_TUI_LOGO_RENDERER=ansi ananta tui --render-once --skip-splash --width 120 --height 32
ANANTA_TUI_LOGO_RENDERER=auto ananta tui --render-once --skip-splash --width 120 --height 32
ANANTA_TUI_LOGO=0 ananta tui --render-once --skip-splash --width 120 --height 32
.venv/bin/python scripts/smoke_logo.py --header-check
```

Optional local smoke checks (protocol support depends on terminal/backend):

```bash
ANANTA_TUI_LOGO_RENDERER=sixel ananta tui --render-once --width 120 --height 32
ANANTA_TUI_LOGO_RENDERER=kitty ananta tui --render-once --width 120 --height 32
```

Gate definition:

- CI requires passing text-only renderer tests and snapshot-safe render-once checks.
- Local release validation should include at least one real live terminal check (Windows Terminal/WSL2, kitty, wezterm, or ghostty) when available.
- If a live protocol test is not available in the current environment, document the skipped live step in release notes.
