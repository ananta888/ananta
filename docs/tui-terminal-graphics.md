# TUI Terminal Graphics (2D + 3D Offscreen)

Diese TUI unterstützt Pixel-orientiertes Rendering im Terminal mit Backend-Fallback:

`kitty > sixel > iterm2 > halfblock > ascii`

ASCII ist nur Notfallmodus.

## Architektur

- `logo_renderer/frame.py`: gemeinsames `PixelFrame`-Objekt (RGBA, PNG, Cache-Key)
- `logo_renderer/detect.py`: Capability Detection + Backend-Auswahl
- `logo_renderer/kitty.py` / `sixel.py`: Pixel-Backends
- `logo_renderer/halfblock.py` / `ascii.py`: Fallback-Backends
- `logo_renderer/moderngl_renderer.py`: 3D-Offscreen-Primärpfad
- `logo_renderer/raylib_renderer.py`: optionaler 3D-Prototyp
- `logo_renderer/compositor.py`: 2D/3D-Overlay-Komposition
- `logo_renderer/animation_loop.py`: FPS-/Budget-Steuerung

## CLI

```bash
ananta tui --graphics auto --quality high
ananta tui --graphics kitty --quality high --target-fps 10
ananta tui --graphics sixel --quality high --frame-width 640 --frame-height 360
ananta tui --graphics halfblock
ananta tui --enable-3d --scene demo-cube --3d-renderer auto
ananta tui --enable-3d --graphics kitty --force-pixel-graphics
```

### Optionen

- `--graphics auto|kitty|sixel|iterm2|halfblock|ascii|none`
- `--quality low|medium|high|ultra`
- `--frame-width/--frame-height`
- `--target-fps`
- `--oversampling-factor`
- `--force-pixel-graphics`
- `--enable-3d --scene <id> --3d-renderer auto|moderngl|raylib`

## Umgebung / Defaults

- Standard-Frame: `480x270`
- High-Quality: `640x360`
- 2D Oversampling: quality-gesteuert (high = 4x, ultra = 6x)

Wichtige Env-Overrides:

- `ANANTA_TUI_GRAPHICS`
- `ANANTA_TUI_LOGO_QUALITY`
- `ANANTA_TUI_FRAME_WIDTH`, `ANANTA_TUI_FRAME_HEIGHT`
- `ANANTA_TUI_TARGET_FPS`
- `ANANTA_TUI_LOGO_OVERSAMPLING`
- `ANANTA_TUI_FORCE_PIXEL_GRAPHICS`
- `ANANTA_TUI_ENABLE_3D`, `ANANTA_TUI_3D_SCENE`, `ANANTA_TUI_3D_RENDERER`

## E2E-Demos

```bash
.venv/bin/python scripts/smoke_logo.py --pixel-2d-demo
.venv/bin/python scripts/smoke_logo.py --pixel-3d-demo
.venv/bin/python scripts/smoke_logo.py --header-check
```

## Plattform-Hinweise

- **Windows Terminal Preview / WSL2**: meist `sixel`
- **WezTerm**: bevorzugt `kitty`
- **iTerm2**: `iterm2` wird erkannt, aktuell Fallback über textbasierte Backends wenn kein Pixelpfad aktiv ist

## Troubleshooting

- Verpixelung: `--quality high` + `--oversampling-factor 4` testen.
- Zu langsam: `--target-fps 5` und kleinere `--frame-width/--frame-height`.
- Kein Pixelbackend: `--graphics halfblock` oder `--force-pixel-graphics`.
- Debug-Metriken: `ANANTA_TUI_GFX_DEBUG=1` zeigt `render_ms/encode_ms/output_ms/fps/frame`.
