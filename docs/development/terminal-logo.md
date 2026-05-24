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

## Integration

Banner is shown in two places in `agent/cli/main.py`:

- `ananta` (no arguments) — before help text
- `ananta status` — before delegating to the goal status command

## Maintenance

Regenerate assets when `ananta.svg` changes:

```bash
python scripts/render_terminal_logo.py --width 120 --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90 --output-dir agent/cli/assets
python scripts/render_terminal_logo.py --width 90 --mono-only --output-dir agent/cli/assets
```

Then update the embedded `_MONO_90` string in `agent/cli/banner.py` if the mono
fallback changes shape (run the generator and copy the output).

**Do not commit `.tmp/` output.** Generator temporary output is ignored by
`.gitignore`.
