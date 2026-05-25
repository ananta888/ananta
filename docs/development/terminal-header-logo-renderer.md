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
