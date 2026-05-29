from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class HeadingBlock:
    level: int
    text: str


@dataclass(frozen=True)
class ParagraphBlock:
    text: str
    # Inline spans: list of (kind, text) tuples
    # kind: "text" | "bold" | "italic" | "code" | "link"
    # For "link": text is "label\x00url"
    spans: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BulletListBlock:
    items: tuple[str, ...]


@dataclass(frozen=True)
class NumberedListBlock:
    items: tuple[str, ...]


@dataclass(frozen=True)
class FencedCodeBlock:
    language: str
    source: str


@dataclass(frozen=True)
class MermaidBlock:
    source: str


@dataclass(frozen=True)
class TableBlock:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    raw_text: str


@dataclass(frozen=True)
class BlockquoteBlock:
    text: str


@dataclass(frozen=True)
class HorizontalRuleBlock:
    pass


MarkdownBlock = Union[
    HeadingBlock,
    ParagraphBlock,
    BulletListBlock,
    NumberedListBlock,
    FencedCodeBlock,
    MermaidBlock,
    TableBlock,
    BlockquoteBlock,
    HorizontalRuleBlock,
]

_FENCE_CHARS = ("```", "~~~")
_BULLET_PREFIXES = ("- ", "* ", "+ ")
_HR_PATTERNS = frozenset({"---", "***", "___"})

# Inline span parsing regex: inline code, bold, italic, links
_INLINE_RE = re.compile(
    r"`(?P<code>[^`]+)`"           # `inline code`
    r"|(?P<link_all>\[(?P<link_label>[^\]]+)\]\((?P<link_url>[^)]+)\))"  # [label](url)
    r"|\*\*(?P<bold>[^*]+)\*\*"   # **bold**
    r"|__(?P<bold2>[^_]+)__"       # __bold__
    r"|\*(?P<italic>[^*]+)\*"      # *italic*
    r"|_(?P<italic2>[^_]+)_"       # _italic_
)


def parse_inline_spans(text: str) -> tuple[tuple[str, str], ...]:
    """Parse inline formatting into spans: (kind, content)."""
    spans: list[tuple[str, str]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            spans.append(("text", text[pos:m.start()]))
        if m.group("code"):
            spans.append(("code", m.group("code")))
        elif m.group("bold"):
            spans.append(("bold", m.group("bold")))
        elif m.group("bold2"):
            spans.append(("bold", m.group("bold2")))
        elif m.group("italic"):
            spans.append(("italic", m.group("italic")))
        elif m.group("italic2"):
            spans.append(("italic", m.group("italic2")))
        elif m.group("link_all"):
            label = m.group("link_label") or ""
            url = m.group("link_url") or ""
            spans.append(("link", f"{label}\x00{url}"))
        pos = m.end()
    if pos < len(text):
        spans.append(("text", text[pos:]))
    return tuple(spans)


def _is_hr(stripped: str) -> bool:
    if stripped in _HR_PATTERNS:
        return True
    if len(stripped) >= 3 and stripped[0] in "-*_" and all(c == stripped[0] for c in stripped):
        return True
    return False


def _is_fence_start(stripped: str) -> bool:
    return stripped.startswith("```") or stripped.startswith("~~~")


def _fence_prefix(stripped: str) -> str:
    return "```" if stripped.startswith("```") else "~~~"


def _is_numbered_item(stripped: str) -> bool:
    if not stripped or not stripped[0].isdigit():
        return False
    dot = stripped.find(". ")
    return dot > 0 and stripped[:dot].isdigit()


def _parse_table_row(line: str) -> tuple[str, ...]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return tuple(cells)


def _is_separator_row(cells: tuple[str, ...]) -> bool:
    return all(re.match(r"^:?-+:?$", c.strip()) for c in cells if c.strip())


def parse_markdown(text: str) -> list[MarkdownBlock]:
    lines = text.splitlines()
    blocks: list[MarkdownBlock] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if _is_fence_start(stripped):
            fence = _fence_prefix(stripped)
            lang = stripped[3:].strip().lower()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            source = "\n".join(code_lines)
            blocks.append(MermaidBlock(source=source) if lang == "mermaid" else FencedCodeBlock(language=lang, source=source))
            continue

        if _is_hr(stripped):
            blocks.append(HorizontalRuleBlock())
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= 6 and (len(stripped) == level or stripped[level] == " "):
                blocks.append(HeadingBlock(level=level, text=stripped[level:].strip()))
                i += 1
                continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(BlockquoteBlock(text=" ".join(quote_lines)))
            continue

        if "|" in stripped and i + 1 < len(lines) and "|" in lines[i + 1] and "---" in lines[i + 1]:
            table_lines: list[str] = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            raw_text = "\n".join(table_lines)
            headers: tuple[str, ...] = ()
            rows: list[tuple[str, ...]] = []
            for j, tl in enumerate(table_lines):
                cells = _parse_table_row(tl)
                if j == 0:
                    headers = cells
                elif j == 1 and _is_separator_row(cells):
                    continue
                else:
                    rows.append(cells)
            blocks.append(TableBlock(headers=headers, rows=tuple(rows), raw_text=raw_text))
            continue

        if stripped.startswith(_BULLET_PREFIXES):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(_BULLET_PREFIXES):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append(BulletListBlock(items=tuple(items)))
            continue

        if _is_numbered_item(stripped):
            num_items: list[str] = []
            while i < len(lines) and _is_numbered_item(lines[i].strip()):
                s = lines[i].strip()
                dot = s.index(". ")
                num_items.append(s[dot + 2:].strip())
                i += 1
            if num_items:
                blocks.append(NumberedListBlock(items=tuple(num_items)))
            continue

        para_lines: list[str] = []
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                break
            if s.startswith("#") or _is_fence_start(s) or s.startswith(">") or _is_hr(s):
                break
            if s.startswith(_BULLET_PREFIXES) or _is_numbered_item(s):
                break
            if "|" in s and i + 1 < len(lines) and "|" in lines[i + 1] and "---" in lines[i + 1]:
                break
            para_lines.append(s)
            i += 1
        if para_lines:
            raw = " ".join(para_lines)
            blocks.append(ParagraphBlock(text=raw, spans=parse_inline_spans(raw)))

    return blocks
