from __future__ import annotations

from worker.retrieval.chunk_metadata import build_chunk_metadata, infer_language

_SYMBOL_PREFIXES_BY_LANGUAGE = {
    "python": ("def ", "class "),
    "typescript": ("function ", "class ", "interface ", "type ", "const "),
    "tsx": ("function ", "class ", "interface ", "type ", "const "),
    "javascript": ("function ", "class ", "const "),
    "jsx": ("function ", "class ", "const "),
    "java": ("class ", "interface ", "enum ", "public ", "private ", "protected "),
    "go": ("func ", "type "),
    "rust": ("fn ", "struct ", "enum ", "impl ", "trait "),
}


def _extract_symbol_name(line: str, *, language: str) -> str:
    normalized = str(line or "").strip()
    if not normalized:
        return ""
    prefixes = _SYMBOL_PREFIXES_BY_LANGUAGE.get(language, ())
    for prefix in prefixes:
        if normalized.startswith(prefix):
            tail = normalized[len(prefix) :].strip()
            for token in ("(", "{", ":", "<", " ", "="):
                if token in tail:
                    tail = tail.split(token, 1)[0]
            return tail.strip()
    return ""


def _iter_line_chunks(content: str, *, max_chunk_bytes: int) -> list[tuple[int, int, str, str]]:
    chunks: list[tuple[int, int, str, str]] = []
    start = 0
    current = []
    current_bytes = 0
    first_symbol = ""
    for line in str(content or "").splitlines(keepends=True):
        encoded = line.encode("utf-8")
        if current and (current_bytes + len(encoded)) > max_chunk_bytes:
            text = "".join(current)
            end = start + len(text.encode("utf-8"))
            chunks.append((start, end, text, first_symbol))
            start = end
            current = []
            current_bytes = 0
            first_symbol = ""
        current.append(line)
        current_bytes += len(encoded)
    if current:
        text = "".join(current)
        end = start + len(text.encode("utf-8"))
        chunks.append((start, end, text, first_symbol))
    return chunks


def split_into_chunks(
    *,
    path: str,
    content: str,
    max_chunk_bytes: int = 1200,
) -> list[dict]:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        raise ValueError("chunk_path_required")
    if int(max_chunk_bytes) <= 0:
        raise ValueError("max_chunk_bytes_must_be_positive")

    normalized_content = str(content or "")
    if not normalized_content:
        metadata = build_chunk_metadata(path=normalized_path, chunk_text="", start_byte=0, end_byte=0)
        return [{"text": "", "metadata": metadata.as_dict()}]

    language = infer_language(normalized_path)
    lines = normalized_content.splitlines(keepends=True)
    line_symbols = [_extract_symbol_name(line, language=language) for line in lines]
    raw_chunks = _iter_line_chunks(normalized_content, max_chunk_bytes=int(max_chunk_bytes))

    chunks: list[dict] = []
    byte_cursor = 0
    line_index = 0
    for start, end, text, _ in raw_chunks:
        chunk_bytes = len(text.encode("utf-8"))
        start = byte_cursor
        end = start + chunk_bytes
        byte_cursor = end

        symbol_name = ""
        consumed_lines = len(text.splitlines())
        for idx in range(line_index, min(line_index + consumed_lines, len(line_symbols))):
            if line_symbols[idx]:
                symbol_name = line_symbols[idx]
                break
        line_index += consumed_lines
        metadata = build_chunk_metadata(
            path=normalized_path,
            chunk_text=text,
            start_byte=start,
            end_byte=end,
            symbol_name=symbol_name,
        )
        chunks.append({"text": text, "metadata": metadata.as_dict()})
    return chunks

