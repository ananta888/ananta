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
)

_R = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_GREY = "\033[90m"


@dataclass(frozen=True)
class MermaidFallbackInfo:
    source: str
    reason: str


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text] if text else []
    return textwrap.wrap(text, width=width) or [""]


def _render_block(
    block: MarkdownBlock,
    *,
    width: int,
    mermaid_fallbacks: dict[str, MermaidFallbackInfo],
) -> list[str]:
    w = max(1, width)
    lines: list[str] = []

    if isinstance(block, HeadingBlock):
        prefix = "#" * block.level + " "
        lines.append(f"{_BOLD}{_YELLOW}{prefix}{block.text}{_R}")
        lines.append("")

    elif isinstance(block, ParagraphBlock):
        for wl in _wrap(block.text, w):
            lines.append(wl)
        lines.append("")

    elif isinstance(block, BulletListBlock):
        for item in block.items:
            wrapped = _wrap(item, max(1, w - 2))
            for j, wl in enumerate(wrapped):
                lines.append(("  " if j else "- ") + wl)
        lines.append("")

    elif isinstance(block, NumberedListBlock):
        for idx, item in enumerate(block.items, 1):
            pfx = f"{idx}. "
            wrapped = _wrap(item, max(1, w - len(pfx)))
            for j, wl in enumerate(wrapped):
                lines.append((" " * len(pfx) if j else pfx) + wl)
        lines.append("")

    elif isinstance(block, FencedCodeBlock):
        lang_label = f" {block.language}" if block.language else ""
        lines.append(f"{_CYAN}[code{lang_label}]{_R}")
        for cl in block.source.splitlines():
            lines.append(f"  {_DIM}{cl}{_R}")
        lines.append(f"{_CYAN}[/code]{_R}")
        lines.append("")

    elif isinstance(block, MermaidBlock):
        fallback = mermaid_fallbacks.get(block.source)
        if fallback is not None:
            lines.append(f"{_YELLOW}[Mermaid: {fallback.reason}]{_R}")
            lines.append(f"{_CYAN}```mermaid{_R}")
            for ml in block.source.splitlines():
                lines.append(f"  {_DIM}{ml}{_R}")
            lines.append(f"{_CYAN}```{_R}")
        else:
            lines.append(f"{_CYAN}[mermaid diagram]{_R}")
        lines.append("")

    elif isinstance(block, TableBlock):
        lines.append(f"{_DIM}[table]{_R}")
        for tl in block.raw_text.splitlines():
            lines.append(f"  {tl[:max(1, w - 2)]}")
        lines.append("")

    elif isinstance(block, BlockquoteBlock):
        for wl in _wrap(block.text, max(1, w - 2)):
            lines.append(f"{_GREY}| {wl}{_R}")
        lines.append("")

    elif isinstance(block, HorizontalRuleBlock):
        lines.append(_DIM + "─" * min(w, 40) + _R)
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
    fallbacks = mermaid_fallbacks or {}
    all_lines: list[str] = []
    for block in blocks:
        all_lines.extend(_render_block(block, width=width, mermaid_fallbacks=fallbacks))

    if not all_lines:
        all_lines = ["(empty document)"]

    start = max(0, scroll_offset)
    visible = all_lines[start : start + height]
    while len(visible) < height:
        visible.append("")
    return visible
