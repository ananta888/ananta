from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DiagramBlock:
    kind: str
    source: str


_FENCED_BLOCK_RE = re.compile(r"```(?P<kind>mermaid|plantuml|puml)\s*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)


def detect_diagram_blocks(source: str) -> tuple[DiagramBlock, ...]:
    blocks: list[DiagramBlock] = []
    text = str(source or "")
    for match in _FENCED_BLOCK_RE.finditer(text):
        kind = match.group("kind").lower()
        if kind == "puml":
            kind = "plantuml"
        blocks.append(DiagramBlock(kind=kind, source=match.group("body").strip()))
    stripped = text.strip()
    if not blocks and stripped.startswith("@startuml"):
        blocks.append(DiagramBlock(kind="plantuml", source=stripped))
    if not blocks and stripped.splitlines()[:1] and stripped.splitlines()[0].strip().lower().startswith(("graph ", "flowchart ", "sequenceDiagram".lower())):
        blocks.append(DiagramBlock(kind="mermaid", source=stripped))
    return tuple(blocks)


def render_diagram_fallback(block: DiagramBlock, *, width: int = 80) -> list[str]:
    lines = [f"{block.kind} diagram preview", "render_mode=text_fallback"]
    for raw_line in block.source.splitlines():
        normalized = raw_line.strip()
        if not normalized or normalized.startswith("@startuml") or normalized.startswith("@enduml"):
            continue
        lines.append(_diagram_line(normalized, width=width))
    return lines or [f"{block.kind} diagram preview", "empty diagram"]


def _diagram_line(line: str, *, width: int) -> str:
    normalized = (
        line.replace("-->", " -> ")
        .replace("->", " -> ")
        .replace("=>", " => ")
        .replace("[", " ")
        .replace("]", " ")
    )
    normalized = " ".join(normalized.split())
    return normalized if len(normalized) <= width else normalized[: max(0, width - 3)] + "..."
