from __future__ import annotations

import hashlib
from dataclasses import dataclass

from client_surfaces.operator_tui.visual.markdown.markdown_parser import MarkdownBlock, MermaidBlock


@dataclass(frozen=True)
class ExtractedMermaidBlock:
    block_id: str
    source: str
    source_hash: str
    position_index: int


def _hash_source(source: str, render_config_key: str = "") -> str:
    data = f"{source}|{render_config_key}".encode()
    return hashlib.sha256(data).hexdigest()[:16]


def extract_mermaid_blocks(
    blocks: list[MarkdownBlock],
    *,
    render_config_key: str = "",
) -> list[ExtractedMermaidBlock]:
    result: list[ExtractedMermaidBlock] = []
    idx = 0
    for block in blocks:
        if isinstance(block, MermaidBlock):
            src_hash = _hash_source(block.source, render_config_key)
            result.append(
                ExtractedMermaidBlock(
                    block_id=f"mermaid_{idx}_{src_hash}",
                    source=block.source,
                    source_hash=src_hash,
                    position_index=idx,
                )
            )
            idx += 1
    return result
