# Terminal Logo (ANSI Banner)

The Ananta CLI shows an ANSI TrueColor terminal banner derived from
`ananta.svg` (snake-around-A design) on `ananta status` and the welcome
screen (`ananta` with no arguments).

## Pipeline

```
ananta.svg  →  SVG renderer  →  PNG  →  ANSI half-block converter  →  .txt
```

1. **SVG → PNG**: One of `cairosvg`, `rsvg-convert`, or `inkscape`.
2. **PNG → ANSI**: `scripts/render_terminal_logo.py` converts pixels to
   half-block characters (`▀` `▄` `▌` `▐`) with TrueColor escape codes.
3. **Asset files** are stored in `agent/cli/assets/` and loaded at runtime by
   `agent/cli/banner.py`.

## Generator

```bash
python scripts/render_terminal_logo.py --width 120 --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90  --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90  --mono-only --output-dir agent/cli/assets
```

Arguments:

| Flag | Default | Description |
|------|---------|-------------|
| `--svg` | `ananta.svg` | Source SVG file |
| `--output-dir` | temp dir | Where to write `.txt` files |
| `--width` | `90` | Output widths (space-separated list) |
| `--mono-only` | off | Only generate monochrome fallback |
| `--color-only` | off | Only generate ANSI color output |

Requirements: `Pillow` + one of `cairosvg`, `rsvg-convert`, or `inkscape`.

## Runtime behavior (`agent/cli/banner.py`)

| Condition | Behavior |
|-----------|----------|
| `ANANTA_NO_BANNER=1` | No banner is shown (empty output). |
| `NO_COLOR=1` | Monochrome fallback (no ANSI escapes). |
| stdout is not a TTY | Monochrome fallback by default. |
| Width >= 110 | Large color banner (120-char asset). |
| 80 <= Width < 110 | Medium color banner (90-char asset). |
| Width < 80 | Monochrome fallback. |
| Missing asset file | Graceful fallback to monochrome. |

## Integration: CLI/Banner

Banner is shown in two places in `agent/cli/main.py`:

- `ananta` (no arguments) — before help text
- `ananta status` — before delegating to the goal status command

## Integration: Operator TUI Splash

The Operator TUI (`ananta tui`) uses an additional asset loader at
`agent/cli/logo_assets.py` to display the logo as a fullscreen startup
splash and a compact 8-line status header.

- `agent/cli/logo_assets.py` — loads and selects assets by width/color
- `agent/cli/logo_layout.py` — compact header layout (logo-left, status-right)
- `agent/cli/status_snapshot.py` — status model with cwd, git, endpoint, tasks
- `agent/cli/splash.py` — splash lifecycle machine (fullscreen → transition → compact_header)

The splash uses the same asset files from `agent/cli/assets/`, trimmed to
8 lines (`max_lines=8`) for the compact header. No separate compact asset
files are needed.

Lifecycle states: `disabled → fullscreen → transition → compact_header`

| CLI Flag | Default | Description |
|----------|---------|-------------|
| `--skip-splash` | on | Skip fullscreen splash, show compact header immediately (default) |
| `--splash` | off | Explicitly enable fullscreen splash |
| `--splash-seconds` | `2.0` | Duration of fullscreen phase |

| Env Var | Effect |
|---------|--------|
| `ANANTA_TUI_SPLASH=0` | Disable splash entirely |
| `ANANTA_TUI_SPLASH=1` | Force fullscreen splash |
| `NO_COLOR=1` | Disable ANSI color in splash/header |

Preview without interactive TUI:
```bash
python -m client_surfaces.operator_tui.app --render-once --skip-splash --width 120 --height 32
python -m client_surfaces.operator_tui.app --render-once --splash --width 120 --height 32
```

## Maintenance

Regenerate assets when `ananta.svg` changes:

```bash
python scripts/render_terminal_logo.py --width 120 --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90 --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90 --mono-only --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 180 --ascii-color --ascii-palette detailed --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 160 --ascii-only --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 180 --ascii-only --output-dir agent/cli/assets
```

Then update the embedded `_MONO_90` string in `agent/cli/banner.py` if the mono
fallback changes shape (run the generator and copy the output).

**Do not commit `.tmp/` output.** Generator temporary output is ignored by
`.gitignore`.
