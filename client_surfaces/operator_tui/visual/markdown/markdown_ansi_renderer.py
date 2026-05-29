from __future__ import annotations

import textwrap
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.markdown.markdown_parser import (
    BlockquoteBlock,
    BulletListBlock,
    FencedCodeBlock,
    HeadingBlock,
    HorizontalRuleBlock,
    MarkdownBlock,
    MermaidBlock,
    NumberedListBlock,
    ParagraphBlock,
    TableBlock,
    parse_inline_spans,
)

_R = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_GREY = "\033[90m"
_GREEN = "\033[32m"
_MAGENTA = "\033[35m"
_UNDERLINE = "\033[4m"

# Heading colors by level
_HEADING_COLORS = [
    "\033[1;33m",   # h1: bold yellow
    "\033[1;36m",   # h2: bold cyan
    "\033[1;32m",   # h3: bold green
    "\033[1;35m",   # h4: bold magenta
    "\033[33m",     # h5: yellow
    "\033[36m",     # h6: cyan
]


@dataclass(frozen=True)
class MermaidFallbackInfo:
    source: str
    reason: str


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text] if text else []
    return textwrap.wrap(text, width=width) or [""]


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:max(0, width - 1)] + "…"


def _render_inline_spans(spans: tuple[tuple[str, str], ...]) -> str:
    """Render inline spans to ANSI string."""
    parts: list[str] = []
    for kind, content in spans:
        if kind == "text":
            parts.append(content)
        elif kind == "code":
            parts.append(f"\033[38;5;214m`{content}`{_R}")
        elif kind == "bold":
            parts.append(f"{_BOLD}{content}{_R}")
        elif kind == "italic":
            parts.append(f"{_ITALIC}{content}{_R}")
        elif kind == "link":
            label, _, url = content.partition("\x00")
            # Shorten URL for display
            short_url = url if len(url) <= 40 else url[:37] + "…"
            parts.append(f"{_UNDERLINE}{label}{_R}{_DIM}({short_url}){_R}")
        else:
            parts.append(content)
    return "".join(parts)


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _render_table(block: TableBlock, width: int) -> list[str]:
    """Render a pipe table with aligned columns."""
    lines: list[str] = []
    headers = block.headers
    rows = block.rows
    if not headers:
        # Fallback to raw if no headers parsed
        for tl in block.raw_text.splitlines():
            lines.append(_DIM + _clip(tl, width) + _R)
        lines.append("")
        return lines

    all_rows = [headers, *rows]
    # Compute column widths
    n_cols = max(len(r) for r in all_rows)
    col_widths: list[int] = []
    for c in range(n_cols):
        col_widths.append(max(
            len(str(r[c] if c < len(r) else "")) for r in all_rows
        ))

    # Total table width check
    total = sum(col_widths) + n_cols * 3 + 1
    if total > width and n_cols > 0:
        # Scale down columns proportionally
        budget = max(n_cols, width - n_cols * 3 - 1)
        scale = budget / max(1, sum(col_widths))
        col_widths = [max(3, int(w * scale)) for w in col_widths]

    def _row_line(cells: tuple[str, ...], color: str = "") -> str:
        parts = []
        for c, w in enumerate(col_widths):
            cell = cells[c] if c < len(cells) else ""
            cell_str = _clip(cell, w).ljust(w)
            parts.append(f" {color}{cell_str}{_R} ")
        return _DIM + "│" + _R + ("│" + _R).join(parts) + _DIM + "│" + _R

    sep = _DIM + "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤" + _R
    top = _DIM + "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐" + _R
    bot = _DIM + "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘" + _R

    lines.append(top)
    lines.append(_row_line(headers, _BOLD))
    lines.append(sep)
    for row in rows:
        lines.append(_row_line(row))
    lines.append(bot)
    lines.append("")
    return lines


def _render_code_block(block: FencedCodeBlock, width: int) -> list[str]:
    """Render fenced code block with language label, line clipping."""
    lines: list[str] = []
    lang_label = f" {block.language}" if block.language else ""
    bar = "─" * min(max(8, width - 2), 60)
    lines.append(f"{_CYAN}┌{bar}[{lang_label.strip() or 'code'}]{_R}")
    for cl in (block.source.splitlines() or [""]):
        # Clip long lines; do not allow ANSI injection from code content
        safe = cl.replace("\033", "^[")
        lines.append(f"{_DIM}│ {_clip(safe, width - 2)}{_R}")
    lines.append(f"{_CYAN}└{bar}{_R}")
    lines.append("")
    return lines


def _render_block(
    block: MarkdownBlock,
    *,
    width: int,
    mermaid_fallbacks: dict[str, MermaidFallbackInfo],
    diagram_height: int = 6,
) -> list[str]:
    w = max(1, width)
    lines: list[str] = []

    if isinstance(block, HeadingBlock):
        level = max(1, min(6, block.level))
        color = _HEADING_COLORS[level - 1]
        prefix = "#" * level + " "
        bar = "═" * min(w, 60) if level == 1 else ("─" * min(w, 60) if level == 2 else "")
        lines.append(f"{color}{prefix}{block.text}{_R}")
        if bar:
            lines.append(f"{_DIM}{bar}{_R}")
        lines.append("")

    elif isinstance(block, ParagraphBlock):
        # Use inline spans if available, else fall back to plain text
        if block.spans:
            rendered = _render_inline_spans(block.spans)
            plain = _strip_ansi(rendered)
            if len(plain) <= w:
                lines.append(rendered)
            else:
                # Word-wrap using plain text, then apply spans per word (simplified)
                for wl in _wrap(plain, w):
                    # Re-render spans for wrapped line — find what spans cover this text segment
                    lines.append(wl)
        else:
            for wl in _wrap(block.text, w):
                lines.append(wl)
        lines.append("")

    elif isinstance(block, BulletListBlock):
        for item in block.items:
            spans = parse_inline_spans(item)
            rendered_item = _render_inline_spans(spans)
            plain_item = _strip_ansi(rendered_item)
            wrapped = _wrap(plain_item, max(1, w - 2))
            for j, wl in enumerate(wrapped):
                lines.append(("  " if j else f"{_CYAN}•{_R} ") + wl)
        lines.append("")

    elif isinstance(block, NumberedListBlock):
        for idx, item in enumerate(block.items, 1):
            pfx = f"{idx}. "
            spans = parse_inline_spans(item)
            rendered_item = _render_inline_spans(spans)
            plain_item = _strip_ansi(rendered_item)
            wrapped = _wrap(plain_item, max(1, w - len(pfx)))
            for j, wl in enumerate(wrapped):
                lines.append((" " * len(pfx) if j else f"{_YELLOW}{pfx}{_R}") + wl)
        lines.append("")

    elif isinstance(block, FencedCodeBlock):
        lines.extend(_render_code_block(block, w))

    elif isinstance(block, MermaidBlock):
        fallback = mermaid_fallbacks.get(block.source)
        if fallback is not None:
            # A real image renderer attempted and failed — show reason + source
            lines.append(f"{_YELLOW}[Mermaid: {fallback.reason}]{_R}")
            lines.append(f"{_CYAN}┌─[mermaid source]{_R}")
            for ml in block.source.splitlines():
                safe = ml.replace("\033", "^[")
                lines.append(f"{_DIM}│ {_clip(safe, w - 2)}{_R}")
            lines.append(f"{_CYAN}└{'─' * min(w - 1, 40)}{_R}")
        else:
            # Graceful degradation: show source as code block
            lines.append(f"{_CYAN}┌─[mermaid]{_R}")
            for ml in block.source.splitlines():
                safe = ml.replace("\033", "^[")
                lines.append(f"{_DIM}│ {_clip(safe, w - 2)}{_R}")
            lines.append(f"{_CYAN}└{'─' * min(w - 1, 40)}{_R}")
        lines.append("")

    elif isinstance(block, TableBlock):
        lines.extend(_render_table(block, w))

    elif isinstance(block, BlockquoteBlock):
        for wl in _wrap(block.text, max(1, w - 2)):
            lines.append(f"{_GREY}▌ {wl}{_R}")
        lines.append("")

    elif isinstance(block, HorizontalRuleBlock):
        lines.append(_DIM + "─" * min(w, 60) + _R)
        lines.append("")

    return lines


def render_markdown_ansi(
    blocks: list[MarkdownBlock],
    *,
    width: int,
    height: int,
    scroll_offset: int = 0,
    mermaid_fallbacks: dict[str, MermaidFallbackInfo] | None = None,
) -> list[str]:
    all_lines = render_markdown_ansi_lines(blocks, width=width, mermaid_fallbacks=mermaid_fallbacks)

    start = max(0, scroll_offset)
    visible = all_lines[start : start + height]
    while len(visible) < height:
        visible.append("")
    return visible


def render_markdown_ansi_lines(
    blocks: list[MarkdownBlock],
    *,
    width: int,
    mermaid_fallbacks: dict[str, MermaidFallbackInfo] | None = None,
) -> list[str]:
    fallbacks = mermaid_fallbacks or {}
    all_lines: list[str] = []
    for block in blocks:
        all_lines.extend(_render_block(block, width=width, mermaid_fallbacks=fallbacks))

    if not all_lines:
        return ["(empty document)"]
    return all_lines
