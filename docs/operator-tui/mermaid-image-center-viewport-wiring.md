# Mermaid Image Center Viewport Wiring

## Pipeline overview

```
Markdown text
    │
    ▼ parse_markdown()
[MermaidBlock, ParagraphBlock, ...]
    │
    ▼ MermaidRenderer.render()
        ├─ MermaidCliBackend (mmdc)   → SVG bytes
        ├─ PlaywrightBackend          → PNG bytes
        └─ FallbackCodeblockBackend   → success=False (graceful)
    │
    ▼ MarkdownMermaidDocumentView._rendered_scene()
        success=True  → diagram_image node (kind='diagram_image')
        success=False → MermaidFallbackInfo → ANSI source block
    │
    ▼ RenderScene.nodes  [label nodes + diagram_image nodes]
    │
    ▼ CpuRasterRenderer / SvgRasterRenderer
        diagram_image png  → PIL paste into output image
        diagram_image svg  → cairosvg → PIL paste
    │
    ▼ RenderFrame (frame_type='raster', mime='image/png')
    │
    ▼ Output adapter
        KittyOutputAdapter  → Kitty graphics protocol (APC escape)
        SixelOutputAdapter  → real Sixel encoding via Pillow quantize
        AnsiOutputAdapter   → ANSI text lines (Mermaid source fallback)
```

## diagram_image node schema

```python
{
    "kind":             "diagram_image",
    "diagram_id":       str,      # stable hash-based ID per block
    "image_format":     str,      # "png" | "svg" | "svg+xml"
    "image_data":       bytes,    # raw PNG or SVG bytes
    "x":                int,
    "y":                int,
    "requested_width":  int,      # pixels
    "requested_height": int,      # pixels
    "alt_text":         str,
    "fallback_text":    str,      # Mermaid source for ANSI fallback
    "render_duration_ms": float,
    "cache_hit":        bool,
}
```

Views emit diagram_image nodes. Renderers/adapters decide whether to
draw raster images or fall back to text. No terminal escape sequences
appear in nodes.

## Capability layers

| Layer | Component | Check |
|---|---|---|
| Mermaid renderer | `mmdc` CLI / `playwright` | `shutil.which("mmdc")` |
| Raster renderer | `Pillow` (PIL) | `from PIL import Image` |
| SVG rasterizer | `cairosvg` (optional) | `import cairosvg` |
| Terminal protocol | Kitty | `TERM=xterm-kitty` or `KITTY_WINDOW_ID` |
| Terminal protocol | Sixel | `SIXEL_SUPPORTED=1` |
| ANSI fallback | always | always available |

## Degraded states

| What fails | What the user sees |
|---|---|
| No mmdc/playwright | Mermaid source as `[mermaid]…[/mermaid]` code block |
| mmdc fails on source | `[Mermaid: <reason>]` + source |
| No Pillow | Dark placeholder image |
| No Kitty/Sixel | ANSI source fallback |
| image-rendered-but-adapter-unavailable | Kitty/Sixel output not shown; ANSI source shown instead |

## Runtime dependencies

- `mmdc`: `npm install -g @mermaid-js/mermaid-cli`
- `playwright`: `pip install playwright && playwright install chromium`
- `Pillow`: `pip install Pillow`
- `cairosvg`: `pip install cairosvg` (optional, for SVG→PNG)
- Kitty terminal: use the Kitty terminal emulator
- Sixel: any Sixel-capable terminal (set `SIXEL_SUPPORTED=1`)
