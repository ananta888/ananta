# Polished Markdown and Visible Mermaid Diagrams

## What this feature does

A Markdown document opened in the center TUI viewport renders as a
proper document: headings, paragraphs, lists, code blocks, tables,
inline formatting and Mermaid diagrams.

When a Kitty or Sixel-capable terminal is in use and mmdc/playwright
is installed, Mermaid diagrams appear as actual raster images. In
ANSI-only mode the source is shown cleanly as a code block.

## Switching between Plain-Text and Rendered mode

Press **Ctrl+Space** once to open the latest long chat answer in the
center view (Markdown/Mermaid rendered by default).
Press **Ctrl+Space** again to toggle to Plain-Text mode.
The current mode and the toggle hint are shown at the bottom of the view.

## Markdown features

| Element | How it looks |
|---|---|
| `# H1` | Bold yellow, followed by `═══` separator |
| `## H2` | Bold cyan, followed by `───` separator |
| `### H3`–`###### H6` | Colored prefix, no separator |
| `**bold**` / `__bold__` | Bold ANSI |
| `*italic*` / `_italic_` | Italic ANSI |
| `` `inline code` `` | Orange highlighted |
| `[label](url)` | label + dim URL (shortened if >40 chars) |
| `- item` / `* item` | `•` bullet |
| `1. item` | Numbered, yellow prefix |
| `> quote` | `▌ ` blockquote marker |
| ` ``` lang ` | Box with language label, clipped long lines |
| `---` | Dim horizontal rule |
| Pipe tables | Box-drawing characters, aligned columns |

## Mermaid diagrams

### ANSI-only mode (always available)

```
┌─[mermaid]
│ graph TD
│   A --> B
└────────────────────
```

### Image mode (Kitty or Sixel terminal + mmdc/playwright + Pillow)

The diagram is rendered as a real PNG/SVG-derived image embedded
in the center viewport via Kitty graphics protocol or Sixel encoding.

### When image rendering fails

If mmdc/playwright succeeds but the terminal adapter cannot show
images, status shows `image-rendered-but-adapter-unavailable`.
The ANSI source fallback is shown automatically.

## Checking capabilities

Run the smoke script to see what's available on your system:

```bash
python scripts/operator_tui_markdown_mermaid_visible_smoke.py
python scripts/operator_tui_markdown_mermaid_visible_smoke.py --adapter kitty
python scripts/operator_tui_markdown_mermaid_visible_smoke.py --adapter sixel
python scripts/operator_tui_markdown_mermaid_visible_smoke.py --require-image
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `[mermaid]…[/mermaid]` block | No image renderer | Install `mmdc` or `playwright` |
| `[Mermaid: <reason>]` | mmdc/playwright failed on source | Check diagram syntax |
| Dark placeholder image | No Pillow | `pip install Pillow` |
| No image in Kitty terminal | Kitty not detected | Check `TERM` / `KITTY_WINDOW_ID` |
| No Sixel output | Sixel not enabled | Set `SIXEL_SUPPORTED=1` |
| `sixel_encoder_unavailable` | No Pillow for encoding | `pip install Pillow` |

## Installing image rendering dependencies

```bash
# Mermaid CLI (Node.js required)
npm install -g @mermaid-js/mermaid-cli

# Or: Playwright
pip install playwright
playwright install chromium

# Raster renderer (required for PNG output)
pip install Pillow

# SVG rasterizer (optional, for SVG→PNG via cairosvg)
pip install cairosvg
```

## Scrolling

Use **PgUp/PgDn** or **Shift+Up/Down** to scroll Markdown documents
in the center viewport. Scrolling is integrated with the shared
ScrollManager — position survives redraws and resets only when a new
document is opened.
