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
- **Migration to Textual** — see library evaluation below.
- **Sixel / Kitty / iTerm2 image protocols** — evaluated as future optional backends only.

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
backends (Textual, asciimatics, chafa) can be swapped in without changing the
splash lifecycle.

---

## Library Evaluation

| Library | Deps | Terminal Fit | Recommendation |
|---|---|---|---|
| Custom ASCII/ANSI renderer | None (built-in) | Exact | **MVP path** — zero new deps, full control |
| prompt_toolkit (existing) | Already used | Animation via `TextArea.text` + `invalidate()` | Use as render target, not render engine |
| Textual | Heavy (new framework) | Good animation model, but full TUI migration | Spike later; not for MVP |
| asciimatics | Medium | Terminal animation primitives exist | Spike later; input model differs from prompt_toolkit |
| chafa / Sixel / Kitty | chafa binary / terminal-specific | Rich image rendering | Spike later; optional high-fidelity backend |
| pygame / moderngl / pyglet | Very heavy | Leaves the terminal | **Rejected** for Operator TUI MVP |

### Current Recommendation

**Custom ASCII/ANSI pseudo-3D renderer first.** No new mandatory dependencies.
Falls back cleanly to existing 2D assets. After the MVP is stable, optional spikes
into Textual / asciimatics / chafa can be evaluated independently under
`experiments/`.

---

## CLI Flags & Env Vars

| Flag | Env Var | Default | Description |
|---|---|---|---|
| `--no-3d` | `ANANTA_TUI_3D=0` | auto-detect | Disable 3D animation entirely |
| `--3d-preset` | `ANANTA_TUI_3D_PRESET` | `rotate_in` | Animation preset name |
| `--3d-fps` | `ANANTA_TUI_3D_FPS` | `24` | Target frame rate |
| `--3d-duration-ms` | `ANANTA_TUI_3D_DURATION_MS` | `2000` | Duration of fullscreen animation (ms) |
| `--render-once` | — | `False` | Print one frame and exit (CI/preview) |
| `--splash-frame` | — | `3d:last` | Which frame to render (`3d:0`, `3d:mid`, `3d:last`) |
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
