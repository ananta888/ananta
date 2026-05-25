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
