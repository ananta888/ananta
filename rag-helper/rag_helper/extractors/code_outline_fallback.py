"""Regex-based code outline used when a language-aware parse is unavailable."""

from __future__ import annotations

import re

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id

PYTHON_SYMBOL_PATTERNS = [
    ("class", re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("function", re.compile(r"(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
]

TYPESCRIPT_SYMBOL_PATTERNS = [
    ("class", re.compile(r"(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("interface", re.compile(r"(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("type", re.compile(r"(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("enum", re.compile(r"(?:export\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("function", re.compile(r"(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("const", re.compile(r"(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")),
]


class CodeOutlineFallbackParser:
    """Emit ``<language>_symbol`` records from line-level symbol patterns."""

    def __init__(self, *, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str, language: str, parse_mode: str):
        file_id = f"{language}_file:{safe_id(rel_path)}"
        symbols = self.extract_symbols(text, language)
        detail_records = []
        relation_records = []
        for index, symbol in enumerate(symbols[:100], start=1):
            detail_id = f"{language}_symbol:{safe_id(rel_path)}:{index}"
            detail_records.append({
                "kind": f"{language}_symbol",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "symbol_kind": symbol["kind"],
                "name": symbol["name"],
                "line": symbol["line"],
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_symbol"})

        names = [symbol["name"] for symbol in symbols]
        index_record = {
            "kind": f"{language}_file",
            "file": rel_path,
            "id": file_id,
            "symbols": symbols[:50],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{language.title()} file {rel_path}. Symbols: {', '.join(names[:30]) or 'none'}.",
                f"{language.title()} {rel_path}. Symbols {compact_list(names, limit=6)}.",
            ),
            "summary": {"symbol_count": len(symbols), "parse_mode": parse_mode},
        }
        return [index_record], detail_records, relation_records, {
            "kind": language,
            "file": rel_path,
            "symbol_count": len(symbols),
            "parse_mode": parse_mode,
        }

    def extract_symbols(self, text: str, language: str) -> list[dict]:
        patterns = PYTHON_SYMBOL_PATTERNS if language == "python" else TYPESCRIPT_SYMBOL_PATTERNS
        symbols: list[dict] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            for kind, pattern in patterns:
                match = pattern.match(stripped)
                if not match:
                    continue
                symbols.append({"kind": kind, "name": match.group(1), "line": index})
                break
        return symbols
