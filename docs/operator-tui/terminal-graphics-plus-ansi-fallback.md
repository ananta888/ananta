# Terminal Graphics + ANSI Fallback

## Rendering Pipeline

```
Mermaid-Quellcode (von AI-Chat)
    │
    ▼ MermaidRenderer
        ├─ MermaidCliBackend (mmdc)     → SVG bytes
        ├─ PlaywrightBackend            → PNG bytes
        └─ FallbackCodeblockBackend     → success=False (graceful)
    │
    ▼ MarkdownMermaidDocumentView
        success=True  → diagram_image node (kind='diagram_image', SVG/PNG bytes)
        success=False → MermaidFallbackInfo → ANSI Quellcode-Block
    │
    ┌──────────────────────────────────────────┐
    │  Fallback Resolver (prefer_image_mode)   │
    │  1. cpu_raster + kitty   (Kitty terminal)│
    │  2. cpu_raster + sixel   (Sixel terminal)│
    │  3. ansi_blocks + ansi   (immer)         │
    └──────────────────────────────────────────┘
    │
    ├─ cpu_raster / svg_raster_optional
    │       ↓ RenderFrame (image/png)
    │   ├─ KittyOutputAdapter  → Kitty APC escape → Terminal-Bild
    │   └─ SixelOutputAdapter  → DCS Sixel escape  → Terminal-Bild
    │
    └─ ansi_blocks
            ↓ RenderFrame (ansi, text/plain)
        AnsiOutputAdapter → ANSI-Zeilen / Unicode Block-Art (▀)
```

## Betriebsmodi

| Modus | Anforderung | Ausgabe |
|---|---|---|
| **Kitty graphics** | Kitty/WezTerm/Ghostty terminal | Echtes PNG-Bild via Kitty APC |
| **Sixel** | Sixel-Terminal + Pillow | Echtes Sixel-Bild |
| **ANSI block art** | True-Color (24-Bit) terminal | ▀-Zeichen mit 24-Bit-Farben |
| **ANSI source** | Jedes Terminal | Mermaid-Quellcode formatiert |

## Abhängigkeiten

| Schicht | Paket | Installation |
|---|---|---|
| Mermaid-Renderer | `mmdc` | `npm install -g @mermaid-js/mermaid-cli` |
| Mermaid-Renderer (alt) | `playwright` | `pip install playwright && playwright install chromium` |
| Raster-Renderer | `Pillow` | `pip install Pillow` |
| SVG→PNG | `cairosvg` | `pip install cairosvg` (optional) |
| Kitty | Kitty-Terminal | kitty.sh, WezTerm, Ghostty |
| Sixel | Sixel-Terminal + Pillow | `SIXEL_SUPPORTED=1` setzen |

## WSL2 / Windows Terminal Hinweise

**Windows Terminal** (`WT_SESSION` gesetzt): Weder Kitty noch Sixel werden automatisch aktiviert.
Windows Terminal ≥ 1.22 unterstützt Sixel experimentell.

| Situation | Lösung |
|---|---|
| Windows Terminal + WSL2 | ANSI Unicode Block-Art (`▀`) funktioniert immer |
| Windows Terminal ≥ 1.22 | `SIXEL_SUPPORTED=1` setzen + Pillow installieren |
| Kitty als WSL2-Terminal | `ANANTA_FORCE_KITTY=1` setzen |
| WezTerm als WSL2-Terminal | Wird automatisch erkannt (TERM_PROGRAM=WezTerm) |

## Manuelle Overrides (Umgebungsvariablen)

```bash
# Kitty-Protokoll erzwingen
ANANTA_FORCE_KITTY=1

# Kitty deaktivieren (auch wenn Terminal erkannt)
ANANTA_FORCE_KITTY=0

# Sixel aktivieren
SIXEL_SUPPORTED=1
# oder
ANANTA_FORCE_SIXEL=1
```

## Diagnose

Im TUI: `/view renderer_diagnostics` oder `F9` (Next View) — zeigt:
- Aktive Renderer/Adapter-Kombination
- mmdc, Pillow, cairosvg, Kitty, Sixel Status
- Resolver-Diagnostik (welche Kandidaten übersprungen wurden)

Smoke-Script:
```bash
# Alle Adapter testen
python scripts/operator_tui_terminal_graphics_smoke.py

# Nur Kitty (erzwingen)
python scripts/operator_tui_terminal_graphics_smoke.py --adapter kitty --force-kitty

# SVG speichern und extern öffnen (Debug)
python scripts/operator_tui_terminal_graphics_smoke.py --save-svg /tmp/test.svg --open-external

# Schlägt fehl wenn nur ANSI möglich
python scripts/operator_tui_terminal_graphics_smoke.py --require-image
```

## Troubleshooting

| Problem | Ursache | Lösung |
|---|---|---|
| `[mermaid]…[/mermaid]` | Kein mmdc/playwright | `npm install -g @mermaid-js/mermaid-cli` |
| `[Mermaid: <Fehler>]` | mmdc Syntax-Fehler | Auto-Fix läuft, oder Syntax prüfen |
| Block-Art erscheint nicht | Kein Pillow | `pip install Pillow` |
| Blank/leer nach Kitty | Kitty nicht unterstützt | `ANANTA_FORCE_KITTY=0` + ANSI nutzen |
| `sixel_encoder_unavailable` | Kein Pillow | `pip install Pillow` |
| Nur 2-3 Zeilen Block-Art | LR-Diagramm zu breit | `flowchart TD` statt `flowchart LR` verwenden |

## Markdown/Mermaid quality matrix (WSL2)

| Symptom | Root cause | Recommended fix |
|---|---|---|
| Mermaid shows only source block | `mmdc` missing | `npm install -g @mermaid-js/mermaid-cli`, then `:doc preflight` |
| Mermaid image path unstable | Playwright missing assets | `npm install mermaid` and `playwright install chromium` or prefer `mmdc` |
| Markdown looks fine but diagrams are tiny | Pixel limits too low for diagram complexity | Increase markdown/mermaid max pixel config and retest with smoke script |
| Browser mode looks white/empty | Carbonyl + site/DNS/terminal limitations | Use `:doc open <file.md>` (document view), not browser mode |
| Inconsistent output across terminals | Adapter capability mismatch | Prefer WezTerm/Kitty for image adapters, fallback to ANSI/chafa on Windows Terminal |
