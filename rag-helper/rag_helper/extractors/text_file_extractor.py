from __future__ import annotations

import re

from rag_helper.extractors.code_outline_fallback import CodeOutlineFallbackParser
from rag_helper.extractors.python_module_outline_parser import PythonModuleOutlineParser
from rag_helper.extractors.structured_support import normalize_extraction_records
from rag_helper.extractors.typescript_module_outline_parser import TypeScriptModuleOutlineParser
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id

# Compatibility re-exports: pattern tables that were importable from this
# module before the language outline parsers were extracted.
from rag_helper.extractors.code_outline_fallback import (  # noqa: E402,F401,I001
    PYTHON_SYMBOL_PATTERNS,
    TYPESCRIPT_SYMBOL_PATTERNS,
)
from rag_helper.extractors.typescript_framework_annotator import (  # noqa: E402,F401
    ANGULAR_IMPORTS_PATTERN,
    ANGULAR_LIFECYCLE_METHODS,
    ANGULAR_SELECTOR_PATTERN,
    ANGULAR_STANDALONE_PATTERN,
    ANGULAR_TEMPLATE_URL_PATTERN,
    JSX_TAG_PATTERN,
    REACT_CLASS_LIFECYCLE_METHODS,
    REACT_COMPONENT_NAME_PATTERN,
    REACT_HOOK_CALL_PATTERN,
    REACT_HOOK_NAME_PATTERN,
)
from rag_helper.extractors.typescript_module_outline_parser import (  # noqa: E402,F401
    TYPESCRIPT_IMPORT_PATTERN,
    TYPESCRIPT_METHOD_PATTERN,
    TYPESCRIPT_TOP_LEVEL_PATTERNS,
)
from rag_helper.extractors.typescript_test_case_collector import (  # noqa: E402,F401
    TYPESCRIPT_FIXTURE_ARGUMENT_PATTERN,
    TYPESCRIPT_TEST_CASE_PATTERN,
)


class TextFileExtractor:
    SUPPORTED_EXTENSIONS = {"properties", "yaml", "yml", "sql", "md", "py", "ts", "tsx", "gradle", "kts"}

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        *,
        python_outline_parser: PythonModuleOutlineParser | None = None,
        typescript_outline_parser: TypeScriptModuleOutlineParser | None = None,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.python_outline_parser = python_outline_parser or PythonModuleOutlineParser(
            embedding_text_mode=embedding_text_mode,
            fallback_parser=CodeOutlineFallbackParser(embedding_text_mode=embedding_text_mode),
        )
        self.typescript_outline_parser = typescript_outline_parser or TypeScriptModuleOutlineParser(
            embedding_text_mode=embedding_text_mode
        )

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported text extension: {ext}")

        if ext in {"yaml", "yml"}:
            result = self._parse_keyed_file(rel_path, text, kind_prefix="yaml", separator=":")
        elif ext == "properties":
            result = self._parse_keyed_file(rel_path, text, kind_prefix="properties", separator="=")
        elif ext == "md":
            result = self._parse_markdown(rel_path, text)
        elif ext in {"gradle", "kts"} or rel_path.endswith(
            ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
        ):
            result = self._parse_gradle(rel_path, text)
        elif ext == "sql":
            result = self._parse_sql(rel_path, text)
        elif ext in {"py", "ts", "tsx"}:
            result = self._parse_code_outline(
                rel_path,
                text,
                language="python" if ext == "py" else "typescript",
            )
        else:
            result = self._parse_file_only(rel_path, text, kind_prefix=ext)
        index, details, relations, stats = result
        normalize_extraction_records(
            (index, details, relations),
            rel_path=rel_path,
            source_text=text,
            extractor=type(self).__name__,
        )
        return index, details, relations, stats

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

    def _parse_gradle(self, rel_path: str, text: str):
        file_id = f"gradle_file:{safe_id(rel_path)}"
        lines = text.splitlines()
        declarations: list[str] = []
        include_entries: list[str] = []
        plugins: list[str] = []
        detail_records = []
        relation_records = []

        for index, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*")):
                continue
            if stripped.startswith("include "):
                include_entries.extend(re.findall(r'["\']([^"\']+)["\']', stripped))
            if stripped.startswith("plugins") or stripped.startswith("pluginManagement"):
                declarations.append(stripped[:120])
            if "id " in stripped:
                plugins.extend(re.findall(r'id\s+[("\']?([^"\')\s]+)', stripped))
            structural_tokens = (
                "dependencies",
                "sourceSets",
                "repositories",
                "include ",
                "project(",
                "plugins",
                "pluginManagement",
            )
            if any(token in stripped for token in structural_tokens):
                detail_id = f"gradle_entry:{safe_id(rel_path)}:{index}"
                detail_records.append({
                    "kind": "gradle_entry",
                    "file": rel_path,
                    "id": detail_id,
                    "parent_id": file_id,
                    "line": index,
                    "content": stripped[:240],
                })
                relation_records.append({"from": file_id, "to": detail_id, "type": "contains_entry"})

        index_record = {
            "kind": "gradle_file",
            "file": rel_path,
            "id": file_id,
            "includes": include_entries[:50],
            "plugins": plugins[:30],
            "declarations": declarations[:30],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"Gradle file {rel_path}. "
                    f"Includes: {', '.join(include_entries[:20]) or 'none'}. "
                    f"Plugins: {', '.join(plugins[:20]) or 'none'}. "
                    f"Declarations: {', '.join(declarations[:10]) or 'none'}."
                ),
                (
                    f"Gradle {rel_path}. "
                    f"Includes {compact_list(include_entries, limit=6)}. "
                    f"Plugins {compact_list(plugins, limit=6)}."
                ),
            ),
            "summary": {
                "include_count": len(include_entries),
                "plugin_count": len(plugins),
                "declaration_count": len(declarations),
            },
        }
        return [index_record], detail_records, relation_records, {
            "kind": "gradle",
            "file": rel_path,
            "include_count": len(include_entries),
            "plugin_count": len(plugins),
            "declaration_count": len(declarations),
        }

    def _parse_code_outline(self, rel_path: str, text: str, language: str):
        if language == "python":
            return self.python_outline_parser.parse(rel_path, text)
        return self.typescript_outline_parser.parse(rel_path, text)

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
