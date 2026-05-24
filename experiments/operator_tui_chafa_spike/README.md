# chafa/Sixel/Kitty Spike — Rich Terminal Graphics Backend

## Setup

```bash
# chafa CLI tool (not Python library)
apt-get install chafa   # or brew install chafa / choco install chafa
pip install Pillow      # already a project dependency for ananta.svg rendering
```

## Evaluation

### chafa

| Aspect | Assessment |
|--------|-----------|
| Capability | Converts PNG → ANSI half-block/Sixel/Kitty; can render the full ananta.svg logo at high fidelity |
| Terminal support | Half-block mode works everywhere; Sixel/Kitty only in supporting terminals |
| Integration | Can generate frame images (via Pillow PNG) → pipe through `chafa` → capture stdout as ANSI string |
| Performance | Subprocess call per frame ≈ 50–150 ms — too slow for real-time 24 fps; frame cache required |
| Testability | Requires chafa binary installed; no pure-Python fallback |

### Sixel / Kitty protocols

| Aspect | Assessment |
|--------|-----------|
| Sixel | Supported by xterm, mlterm, libvte ≥ 0.76; requires pixel-precise frame rendering |
| Kitty | `kitty icat` protocol; only in Kitty terminal; transmits PNG as base64 in control sequences |
| Fallback | Must fall back to ASCII/ANSI if protocol not detected on the terminal |
| Capability detection | Already exists in `client_surfaces/operator_tui/capabilities.py` (`detect_terminal_graphics`) |

## Feasibility

The existing `scripts/render_terminal_logo.py` already proves that ananta.svg can be rendered to ANSI at arbitrary resolutions. A chafa-based backend could reuse the same SVG→PNG pipeline, but:

1. Adds a subprocess call and binary dependency (`chafa`)
2. Frame generation is too slow for real-time animation without aggressive caching
3. The custom `BuiltinBackend` already delivers recognisable geometry at zero dependency cost

## Recommendation

**Reject as default. Keep as documented option** for a future `--3d-backend chafa` flag if the wireframe 3D is deemed insufficient. The `LogoAnimationBackend` protocol makes this trivially swappable — implement `frame_at()` with a SVG→PNG→chafa→ANSI pipeline under `animation3d/backends_chafa.py`.
