"""Split one file into citable chunks with 1-based line ranges.

One strategy per file kind (``CHUNKERS``): Python by top-level function and
class (large classes by method), Markdown by heading, everything else by line
windows. Every strategy falls back to line windows, so no text file is ever
dropped for its size; an oversized symbol is split into windows that keep
its name.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable

MAX_WINDOW_LINES = 120
MAX_WINDOW_CHARS = 6000
MAX_CLASS_LINES = 150
_HEADING = re.compile(r"^#{1,6}\s")


@dataclass(frozen=True)
class Chunk:
    start_line: int
    end_line: int
    symbol: str
    kind: str
    content: str


def chunk_file(path: str, text: str) -> list[Chunk]:
    """Chunks covering every non-blank line of ``text``, in file order."""
    lines = text.splitlines()
    if not lines:
        return []
    chunker = CHUNKERS.get(_extension(path), line_window_chunks)
    return [chunk for chunk in chunker(lines) if chunk.content.strip()]


def line_window_chunks(lines: list[str], *, first: int = 1, last: int | None = None,
                       symbol: str = "", kind: str = "text") -> list[Chunk]:
    """Windows of at most ``MAX_WINDOW_LINES`` lines / ``MAX_WINDOW_CHARS`` characters."""
    last = len(lines) if last is None else last
    chunks, start = [], first
    while start <= last:
        end, size = start, 0
        while end <= last and (end == start or (end - start < MAX_WINDOW_LINES
                                                and size + len(lines[end - 1]) + 1 <= MAX_WINDOW_CHARS)):
            size += len(lines[end - 1]) + 1
            end += 1
        chunks.append(Chunk(start, end - 1, symbol, kind, "\n".join(lines[start - 1:end - 1])))
        start = end
    return chunks


def python_chunks(lines: list[str]) -> list[Chunk]:
    try:
        tree = ast.parse("\n".join(lines))
    except (SyntaxError, ValueError):
        return line_window_chunks(lines, kind="python")
    spans: list[tuple[int, int, str, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start, end = _span(node)
        if isinstance(node, ast.ClassDef) and end - start + 1 > MAX_CLASS_LINES:
            spans.extend(_class_member_spans(node, start, end))
        else:
            spans.append((start, end, node.name, "class" if isinstance(node, ast.ClassDef) else "function"))
    return _cover(lines, spans, gap_kind="module")


def markdown_chunks(lines: list[str]) -> list[Chunk]:
    starts = [index + 1 for index, line in enumerate(lines) if _HEADING.match(line)]
    if not starts or starts[0] != 1:
        starts.insert(0, 1)
    spans = []
    for position, start in enumerate(starts):
        end = (starts[position + 1] - 1) if position + 1 < len(starts) else len(lines)
        heading = lines[start - 1].lstrip("#").strip() if _HEADING.match(lines[start - 1]) else ""
        spans.append((start, end, heading[:120], "section"))
    return _cover(lines, spans, gap_kind="section")


def _class_member_spans(node: ast.ClassDef, start: int, end: int) -> list[tuple[int, int, str, str]]:
    members = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not members:
        return [(start, end, node.name, "class")]
    spans, cursor = [], start
    for member in members:
        member_start, member_end = _span(member)
        if member_start > cursor:
            spans.append((cursor, member_start - 1, node.name, "class"))
        spans.append((member_start, member_end, f"{node.name}.{member.name}", "method"))
        cursor = member_end + 1
    if cursor <= end:
        spans.append((cursor, end, node.name, "class"))
    return spans


def _span(node: ast.AST) -> tuple[int, int]:
    decorators = [item.lineno for item in getattr(node, "decorator_list", [])]
    start = min([node.lineno, *decorators])
    return start, int(getattr(node, "end_lineno", None) or node.lineno)


def _cover(lines: list[str], spans: list[tuple[int, int, str, str]], *, gap_kind: str) -> list[Chunk]:
    """Chunks for the spans plus line windows for the gaps between them; big spans are windowed."""
    chunks, cursor = [], 1
    for start, end, symbol, kind in sorted(spans):
        if start > cursor:
            chunks.extend(line_window_chunks(lines, first=cursor, last=start - 1, kind=gap_kind))
        chunks.extend(_bounded(lines, start, end, symbol, kind))
        cursor = max(cursor, end + 1)
    if cursor <= len(lines):
        chunks.extend(line_window_chunks(lines, first=cursor, kind=gap_kind))
    return chunks


def _bounded(lines: list[str], start: int, end: int, symbol: str, kind: str) -> list[Chunk]:
    content = "\n".join(lines[start - 1:end])
    if end - start < MAX_WINDOW_LINES * 2 and len(content) <= MAX_WINDOW_CHARS * 2:
        return [Chunk(start, end, symbol, kind, content)]
    return line_window_chunks(lines, first=start, last=end, symbol=symbol, kind=kind)


def _extension(path: str) -> str:
    name = str(path).rsplit("/", 1)[-1].lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


CHUNKERS: dict[str, Callable[[list[str]], list[Chunk]]] = {
    "py": python_chunks,
    "md": markdown_chunks,
    "markdown": markdown_chunks,
}
