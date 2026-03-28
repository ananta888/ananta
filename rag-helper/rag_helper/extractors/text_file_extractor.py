from __future__ import annotations

import re

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


class TextFileExtractor:
    SUPPORTED_EXTENSIONS = {"properties", "yaml", "yml", "sql", "md", "py", "ts", "tsx"}

    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported text extension: {ext}")

        if ext in {"yaml", "yml"}:
            return self._parse_keyed_file(rel_path, text, kind_prefix="yaml", separator=":")
        if ext == "properties":
            return self._parse_keyed_file(rel_path, text, kind_prefix="properties", separator="=")
        if ext == "md":
            return self._parse_markdown(rel_path, text)
        if ext == "sql":
            return self._parse_sql(rel_path, text)
        if ext in {"py", "ts", "tsx"}:
            return self._parse_code_outline(rel_path, text, language="python" if ext == "py" else "typescript")
        return self._parse_file_only(rel_path, text, kind_prefix=ext)

    def _parse_file_only(self, rel_path: str, text: str, kind_prefix: str):
        file_id = f"{kind_prefix}_file:{safe_id(rel_path)}"
        index_record = {
            "kind": f"{kind_prefix}_file",
            "file": rel_path,
            "id": file_id,
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{kind_prefix.upper()} file {rel_path}. Content length {len(text)} characters.",
                f"{kind_prefix.upper()} file {rel_path}.",
            ),
            "summary": {"char_count": len(text)},
        }
        return [index_record], [], [], {"kind": kind_prefix, "file": rel_path, "record_count": 1}

    def _parse_markdown(self, rel_path: str, text: str):
        file_id = f"md_file:{safe_id(rel_path)}"
        headings = []
        detail_records = []
        relation_records = []
        current_parent_id = file_id
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            if not heading:
                continue
            section_id = f"md_section:{safe_id(rel_path)}:{index}"
            headings.append(heading)
            detail_records.append({
                "kind": "md_section",
                "file": rel_path,
                "id": section_id,
                "parent_id": current_parent_id,
                "heading": heading,
                "level": level,
                "line": index,
            })
            relation_records.append({"from": current_parent_id, "to": section_id, "type": "contains_section"})
            current_parent_id = section_id

        index_record = {
            "kind": "md_file",
            "file": rel_path,
            "id": file_id,
            "heading_count": len(headings),
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"Markdown file {rel_path}. Headings: {', '.join(headings[:20]) or 'none'}.",
                f"Markdown {rel_path}. Headings {compact_list(headings, limit=6)}.",
            ),
            "summary": {"heading_count": len(headings)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": "md",
            "file": rel_path,
            "heading_count": len(headings),
        }

    def _parse_keyed_file(self, rel_path: str, text: str, kind_prefix: str, separator: str):
        file_id = f"{kind_prefix}_file:{safe_id(rel_path)}"
        keys = []
        detail_records = []
        relation_records = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if kind_prefix == "properties" and stripped.startswith(("!", ";")):
                continue
            key = self._extract_key(stripped, separator)
            if not key:
                continue
            detail_id = f"{kind_prefix}_entry:{safe_id(rel_path)}:{index}"
            keys.append(key)
            detail_records.append({
                "kind": f"{kind_prefix}_entry",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "key": key,
                "line": index,
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_entry"})

        index_record = {
            "kind": f"{kind_prefix}_file",
            "file": rel_path,
            "id": file_id,
            "keys": keys[:50],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{kind_prefix.upper()} file {rel_path}. Keys: {', '.join(keys[:30]) or 'none'}.",
                f"{kind_prefix.upper()} {rel_path}. Keys {compact_list(keys, limit=6)}.",
            ),
            "summary": {"entry_count": len(keys)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": kind_prefix,
            "file": rel_path,
            "entry_count": len(keys),
        }

    def _parse_sql(self, rel_path: str, text: str):
        file_id = f"sql_file:{safe_id(rel_path)}"
        statements = [stmt.strip() for stmt in text.split(";") if stmt.strip()]
        detail_records = []
        relation_records = []
        titles = []
        for index, statement in enumerate(statements[:50], start=1):
            title = self._sql_statement_title(statement)
            titles.append(title)
            detail_id = f"sql_statement:{safe_id(rel_path)}:{index}"
            detail_records.append({
                "kind": "sql_statement",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "title": title,
                "statement": statement[:400],
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_statement"})

        index_record = {
            "kind": "sql_file",
            "file": rel_path,
            "id": file_id,
            "statement_count": len(statements),
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"SQL file {rel_path}. Statements: {', '.join(titles[:20]) or 'none'}.",
                f"SQL {rel_path}. Statements {compact_list(titles, limit=6)}.",
            ),
            "summary": {"statement_count": len(statements)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": "sql",
            "file": rel_path,
            "statement_count": len(statements),
        }

    def _parse_code_outline(self, rel_path: str, text: str, language: str):
        file_id = f"{language}_file:{safe_id(rel_path)}"
        symbols = self._extract_symbols(text, language)
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
            "summary": {"symbol_count": len(symbols)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": language,
            "file": rel_path,
            "symbol_count": len(symbols),
        }

    def _extract_key(self, line: str, separator: str) -> str | None:
        if separator in line:
            return line.split(separator, 1)[0].strip()
        if separator == ":" and ":" in line:
            return line.split(":", 1)[0].strip()
        return None

    def _sql_statement_title(self, statement: str) -> str:
        compact = re.sub(r"\s+", " ", statement).strip()
        words = compact.split(" ")
        return " ".join(words[:6])

    def _extract_symbols(self, text: str, language: str) -> list[dict]:
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
