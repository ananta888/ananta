from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".md": "markdown",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
}


def infer_language(path: str) -> str:
    normalized = str(path or "").strip().lower()
    for suffix, language in _LANGUAGE_BY_SUFFIX.items():
        if normalized.endswith(suffix):
            return language
    return "text"


@dataclass(frozen=True)
class ChunkMetadata:
    chunk_id: str
    path: str
    language: str
    symbol_name: str
    start_byte: int
    end_byte: int
    content_hash: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "chunk_id": self.chunk_id,
            "path": self.path,
            "language": self.language,
            "symbol_name": self.symbol_name,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "content_hash": self.content_hash,
        }


def build_chunk_metadata(
    *,
    path: str,
    chunk_text: str,
    start_byte: int,
    end_byte: int,
    symbol_name: str = "",
) -> ChunkMetadata:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("chunk_path_required")
    if int(start_byte) < 0 or int(end_byte) < int(start_byte):
        raise ValueError("chunk_byte_range_invalid")
    normalized_text = str(chunk_text or "")
    content_hash = sha256(normalized_text.encode("utf-8")).hexdigest()
    chunk_id = f"{normalized_path}:{start_byte}:{end_byte}:{content_hash[:12]}"
    return ChunkMetadata(
        chunk_id=chunk_id,
        path=normalized_path,
        language=infer_language(normalized_path),
        symbol_name=str(symbol_name or "").strip(),
        start_byte=int(start_byte),
        end_byte=int(end_byte),
        content_hash=content_hash,
    )

