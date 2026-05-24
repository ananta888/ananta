# Operator TUI 3D Logo Animation

## Scope (MVP)

The MVP is a **pseudo-3D terminal animation** — not a full graphics engine. It renders
a rotating/stylised Ananta logo (letter "A" with a snake ribbon) as an ASCII/ANSI
frame sequence within the existing `prompt_toolkit`-based Operator TUI.

### In Scope (MVP)

- Deterministic ASCII/ANSI frame generation from 3D geometry models.
- Rotation about X, Y, Z axes; configurable zoom/perspective.
- Depth ordering (Z-buffer per terminal cell) so the snake passes in front of / behind the A.
- ASCII character shading (density, edge direction) and ANSI TrueColor / 256-color support.
- Fallback chain: 3D ASCII-color → 2D ASCII-color → mono compact header → legacy shell.
- Integration into the existing `SplashMachine` lifecycle (fullscreen → transition → compact header).
- CLI flags and env variables to enable / disable / tune the animation.
- `--render-once --splash-frame 3d:<frame>` preview in non-TTY / CI.
- Frame cache bounded by width, height, preset and frame index.

### Explicitly Out of Scope (MVP)

- **pygame / moderngl / pyglet / OpenGL** — these leave the terminal model entirely.
- **Full 3D mesh rendering** — no triangle rasterisation, texture mapping, or lighting model.
- **Real-time interaction** during animation — keyboard skips the splash, it does not steer it.
- **Audio / video output** — the splash is ASCII text only.
- **Migration to Textual** — rejected as splash-only backend (see spike docs).
- **Sixel / Kitty / iTerm2 image protocols** — evaluated as optional future backends (see spike docs).

---

## Fallback Chain

```
3D ASCII-color (≥80 cols, color TTY)
  → 2D ASCII-color (≥80 cols, color TTY, no geometry model)
    → mono compact header (any terminal, no color)
      → legacy shell (no logo at all)
```

Each step checks terminal width, TTY status, `NO_COLOR`, `ANANTA_TUI_3D` and
`ANANTA_TUI_SPLASH` before deciding which renderer to use.

---

## Target Effects

| Effect | Description |
|---|---|
| Rotate | Logo rotates continuously around Y (and optionally X/Z) axis |
| Zoom | Gentle scale pulse over the animation duration |
| Depth | Snake segments pass in front of / behind the A letter |
| Snake slither | Phase offset animates the snake body along its path |

---

## Architecture

```
SplashMachine (agent/cli/splash.py)
  │
  ├── BuiltinBackend (animation3d/backends.py)
  │     ├── GeometryModel (animation3d/geometry.py)
  │     │     ├── ALetterGeometry    — stylised "A" wireframe
  │     │     └── SnakeRibbonGeometry — snake path around A
  │     ├── Projector (animation3d/projection.py)
  │     ├── Rasterizer (animation3d/rasterizer.py)
  │     ├── Shader (animation3d/shading.py)
  │     └── Coloriser (animation3d/color.py)
  │
  ├── Capability detection (animation3d/capabilities.py)
  └── Presets (animation3d/presets.py)
```

The `BuiltinBackend` implements the `LogoAnimationBackend` protocol so that future
backends can be swapped in without changing the splash lifecycle.

---

## Library Evaluation — Final Decision Record

| Library | Recommended | Reasoning |
|---|---|---|
| Custom ASCII/ANSI renderer | **MVP (implemented)** | Zero deps, full control, 62 test cases passing |
| Textual | **Rejected** | Full TUI framework, would need to run alongside prompt_toolkit — too heavy for splash-only; revisit only if whole TUI migrates |
| asciimatics | **Rejected** | Screen ownership conflicts with prompt_toolkit; no geometry/3D primitives; same projection math needed as BuiltinBackend |
| chafa / Sixel / Kitty | **Rejected for default** | Subprocess per frame too slow for 24 fps; keep as optional `--3d-backend chafa` for future high-fidelity path |
| pygame / moderngl / pyglet / OpenGL | **Rejected** | Leave the terminal; not suitable for a TUI startup splash; CI/SSH incompatible |

Detailed spike evaluations are under `experiments/`:
- `experiments/operator_tui_textual_3d_spike/README.md`
- `experiments/operator_tui_asciimatics_spike/README.md`
- `experiments/operator_tui_chafa_spike/README.md`

---

## CLI Flags & Env Vars

| Flag | Env Var | Default | Description |
|---|---|---|---|
| `--no-3d` | `ANANTA_TUI_3D=0` | auto-detect | Disable 3D animation entirely |
| `--3d-preset` | `ANANTA_TUI_3D_PRESET` | `rotate_in` | Animation preset name |
| `--3d-fps` | `ANANTA_TUI_3D_FPS` | `24` | Target frame rate |
| `--3d-duration-ms` | `ANANTA_TUI_3D_DURATION_MS` | `2000` | Duration of fullscreen animation (ms) |
| `--render-once` | — | `False` | Print one frame and exit (CI/preview) |
| `--splash-frame` | — | `3d:last` | Which frame to render (`3d:0`, `3d:mid`, `3d:last`, `compact`) |
| `--width` / `--height` | — | `120` / `32` | Viewport size for render-once |
| `NO_COLOR` | `NO_COLOR` | — | Disable ANSI color (geometry preserved) |

---

## Performance Budget

| Metric | Budget |
|---|---|
| Frame precompute (60 frames) | ≤ 500 ms |
| Single frame render | ≤ 15 ms |
| Frame cache max entries | 300 |
| Frame cache max bytes | ~ 4 MB |
