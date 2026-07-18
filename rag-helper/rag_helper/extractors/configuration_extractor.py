"""Bounded, secret-safe extraction for common configuration formats."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass

import tomllib
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken

from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    is_secret_key,
    scalar_type,
    stats_for,
)


class ConfigurationLimitError(ValueError):
    def __init__(self, code: str, line: int = 1, column: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.line = line
        self.column = column


@dataclass(frozen=True, slots=True)
class _KeyEntry:
    path: str
    line: int
    column: int
    value_type: str
    document: int = 1


class ConfigurationExtractor:
    SUPPORTED_EXTENSIONS = {"yaml", "yml", "toml", "ini", "cfg", "conf", "properties"}

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_aliases: int = 50,
        max_nodes: int = 20_000,
        max_depth: int = 64,
        max_records: int = 5_000,
    ) -> None:
        for name, value in {
            "max_aliases": max_aliases,
            "max_nodes": max_nodes,
            "max_depth": max_depth,
            "max_records": max_records,
        }.items():
            if value <= 0:
                raise ValueError(f"{name}_must_be_positive")
        self.embedding_text_mode = embedding_text_mode
        self.max_aliases = max_aliases
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported_configuration_extension:{ext}")
        format_name = "yaml" if ext in {"yaml", "yml"} else ext
        factory = StructuredRecordFactory(rel_path, format_name, self.embedding_text_mode)
        try:
            if ext in {"yaml", "yml"}:
                entries, document_count = self._yaml_entries(text)
            elif ext == "toml":
                entries, document_count = self._toml_entries(text), 1
            elif ext in {"ini", "cfg", "conf"}:
                entries, document_count = self._ini_entries(text), 1
            else:
                entries, document_count = self._properties_entries(text), 1
        except Exception as exc:  # a structured text fallback is part of the contract
            code, line, column = self._diagnostic_from_exception(exc, format_name)
            diagnostic = factory.diagnostic(
                code,
                "Structured configuration parsing failed; content was indexed without values.",
                line=line,
                column=column,
                severity="error",
                fallback="text_index",
            )
            index = [
                factory.file_record(
                    summary={
                        "entry_count": 0,
                        "document_count": 0,
                        "diagnostic_count": 1,
                        "line_count": len(text.splitlines()),
                    },
                    parser_mode="text_index",
                    confidence=0.25,
                )
            ]
            return (
                index,
                [diagnostic],
                [],
                stats_for(
                    format_name,
                    rel_path,
                    index,
                    [diagnostic],
                    [],
                    parser_mode="text_index",
                    diagnostics=[diagnostic],
                    entry_count=0,
                    document_count=0,
                    fallback="text_index",
                ),
            )

        diagnostics: list[dict] = []
        if len(entries) > self.max_records:
            entries = entries[: self.max_records]
            diagnostics.append(
                factory.diagnostic(
                    "configuration_record_limit_reached",
                    f"Only the first {self.max_records} key paths were indexed.",
                    line=entries[-1].line if entries else 1,
                    fallback="partial_structured_index",
                )
            )

        details: list[dict] = []
        relations: list[dict] = []
        key_ids: dict[tuple[int, str], str] = {}
        for ordinal, entry in enumerate(entries, start=1):
            parent_path = entry.path.rsplit(".", 1)[0] if "." in entry.path else ""
            parent_id = key_ids.get((entry.document, parent_path), factory.file_id)
            redacted = is_secret_key(entry.path)
            record = factory.symbol(
                kind=f"{format_name}_key",
                name=entry.path.rsplit(".", 1)[-1],
                line=entry.line,
                column=entry.column,
                parent_id=parent_id,
                ordinal=ordinal,
                key_path=entry.path,
                document=entry.document,
                value_type=entry.value_type,
                value_redacted=redacted,
            )
            key_ids[(entry.document, entry.path)] = record["id"]
            details.append(record)
            relations.append(
                factory.relation(
                    source_id=parent_id,
                    source_kind=f"{format_name}_key" if parent_id != factory.file_id else f"{format_name}_file",
                    source_name=parent_path or rel_path,
                    relation="contains_key",
                    target=entry.path,
                    target_resolved=record["id"],
                    line=entry.line,
                    document=entry.document,
                )
            )
        details.extend(diagnostics)
        index = [
            factory.file_record(
                summary={
                    "entry_count": len(entries),
                    "document_count": document_count,
                    "secret_key_count": sum(is_secret_key(entry.path) for entry in entries),
                    "diagnostic_count": len(diagnostics),
                },
                labels=[entry.path for entry in entries],
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                format_name,
                rel_path,
                index,
                details,
                relations,
                diagnostics=diagnostics,
                entry_count=len(entries),
                document_count=document_count,
                secret_key_count=sum(is_secret_key(entry.path) for entry in entries),
            ),
        )

    def validate_yaml(self, text: str) -> tuple[list[_KeyEntry], int]:
        """Validate YAML against the configured policy without constructing values."""

        return self._yaml_entries(text)

    def _yaml_entries(self, text: str) -> tuple[list[_KeyEntry], int]:
        aliases = 0
        try:
            for token in yaml.scan(text, Loader=yaml.SafeLoader):
                if isinstance(token, AliasToken):
                    aliases += 1
                    if aliases > self.max_aliases:
                        raise ConfigurationLimitError(
                            "yaml_alias_limit_exceeded",
                            token.start_mark.line + 1,
                            token.start_mark.column + 1,
                        )
        except yaml.YAMLError:
            raise

        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        entries: list[_KeyEntry] = []
        global_nodes: set[int] = set()
        for document_number, root in enumerate(documents, start=1):
            if root is None:
                continue
            self._walk_yaml_node(
                root,
                path="",
                document=document_number,
                depth=1,
                entries=entries,
                visited=global_nodes,
            )
        return entries, len(documents)

    def _walk_yaml_node(
        self,
        node: Node,
        *,
        path: str,
        document: int,
        depth: int,
        entries: list[_KeyEntry],
        visited: set[int],
    ) -> None:
        if depth > self.max_depth:
            raise ConfigurationLimitError(
                "yaml_depth_limit_exceeded", node.start_mark.line + 1, node.start_mark.column + 1
            )
        node_identity = id(node)
        if node_identity in visited:
            return
        visited.add(node_identity)
        if len(visited) > self.max_nodes:
            raise ConfigurationLimitError(
                "yaml_node_limit_exceeded", node.start_mark.line + 1, node.start_mark.column + 1
            )

        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                key = self._yaml_key(key_node)
                key_path = f"{path}.{key}" if path else key
                entries.append(
                    _KeyEntry(
                        path=key_path,
                        line=key_node.start_mark.line + 1,
                        column=key_node.start_mark.column + 1,
                        value_type=self._yaml_node_type(value_node),
                        document=document,
                    )
                )
                self._walk_yaml_node(
                    value_node,
                    path=key_path,
                    document=document,
                    depth=depth + 1,
                    entries=entries,
                    visited=visited,
                )
        elif isinstance(node, SequenceNode):
            for index, child in enumerate(node.value):
                indexed_path = f"{path}[{index}]" if path else f"[{index}]"
                self._walk_yaml_node(
                    child,
                    path=indexed_path,
                    document=document,
                    depth=depth + 1,
                    entries=entries,
                    visited=visited,
                )

    @staticmethod
    def _yaml_key(node: Node) -> str:
        if isinstance(node, ScalarNode):
            return str(node.value)
        return f"complex_key_{node.start_mark.line + 1}_{node.start_mark.column + 1}"

    @staticmethod
    def _yaml_node_type(node: Node) -> str:
        if isinstance(node, MappingNode):
            return "object"
        if isinstance(node, SequenceNode):
            return "array"
        if not isinstance(node, ScalarNode):
            return "unknown"
        tag = node.tag.rsplit(":", 1)[-1]
        return {
            "null": "null",
            "bool": "boolean",
            "int": "integer",
            "float": "number",
            "timestamp": "timestamp",
            "str": "string",
        }.get(tag, "string")

    def _toml_entries(self, text: str) -> list[_KeyEntry]:
        parsed = tomllib.loads(text)
        source_lines = self._toml_source_lines(text)
        entries: list[_KeyEntry] = []

        def walk(value: object, path: str) -> None:
            if not isinstance(value, dict):
                return
            for key, child in value.items():
                key_path = f"{path}.{key}" if path else str(key)
                line, column = source_lines.get(key_path, source_lines.get(str(key), (1, 1)))
                entries.append(_KeyEntry(key_path, line, column, scalar_type(child)))
                if isinstance(child, dict):
                    walk(child, key_path)
                elif isinstance(child, list):
                    for array_index, item in enumerate(child):
                        if isinstance(item, dict):
                            walk(item, f"{key_path}[{array_index}]")

        walk(parsed, "")
        return entries

    @staticmethod
    def _toml_source_lines(text: str) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        table = ""
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            table_match = re.match(r"^\[\[?\s*([^\]]+?)\s*\]\]?$", stripped)
            if table_match:
                table = table_match.group(1).strip().strip("\"'")
                result.setdefault(table, (line_no, raw.find("[") + 1))
                continue
            key_match = re.match(r"^\s*([A-Za-z0-9_.\-'\"]+)\s*=", raw)
            if not key_match:
                continue
            key = key_match.group(1).strip().strip("\"'")
            key_path = f"{table}.{key}" if table else key
            result.setdefault(key_path, (line_no, key_match.start(1) + 1))
            result.setdefault(key, (line_no, key_match.start(1) + 1))
        return result

    @staticmethod
    def _ini_entries(text: str) -> list[_KeyEntry]:
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=False,
            empty_lines_in_values=False,
            allow_no_value=True,
        )
        source = text
        if not re.search(r"^\s*\[[^\]]+\]", text, re.MULTILINE):
            source = "[default]\n" + text
        parser.read_string(source)
        line_map: dict[tuple[str, str], tuple[int, int]] = {}
        section = "default"
        line_offset = 0 if source is text else -1
        for line_no, raw in enumerate(source.splitlines(), start=1):
            section_match = re.match(r"^\s*\[([^\]]+)\]", raw)
            if section_match:
                section = section_match.group(1).strip()
                continue
            key_match = re.match(r"^\s*([^#;\s][^:=]*?)\s*(?:=|:)(.*)$", raw)
            if key_match:
                line_map[(section, key_match.group(1).strip().lower())] = (
                    max(1, line_no + line_offset),
                    key_match.start(1) + 1,
                )
        entries: list[_KeyEntry] = []
        for section_name in parser.sections():
            visible_section = "" if section_name == "default" and source is not text else section_name
            if visible_section:
                section_line = next(
                    (
                        index
                        for index, raw in enumerate(text.splitlines(), start=1)
                        if re.match(rf"^\s*\[{re.escape(section_name)}\]", raw)
                    ),
                    1,
                )
                entries.append(_KeyEntry(visible_section, section_line, 1, "object"))
            for key, value in parser.items(section_name, raw=True):
                path = f"{visible_section}.{key}" if visible_section else key
                line, column = line_map.get((section_name, key.lower()), (1, 1))
                entries.append(_KeyEntry(path, line, column, "null" if value is None else "string"))
        return entries

    @staticmethod
    def _properties_entries(text: str) -> list[_KeyEntry]:
        entries: list[_KeyEntry] = []
        logical_line = ""
        logical_start = 1
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not logical_line and (not stripped or stripped.startswith(("#", "!", ";"))):
                continue
            if not logical_line:
                logical_start = line_no
            logical_line += raw.lstrip() if logical_line else raw
            if logical_line.endswith("\\") and not logical_line.endswith("\\\\"):
                logical_line = logical_line[:-1]
                continue
            match = re.match(r"^\s*([^:=\s]+)\s*(?:[:=]|\s)\s*(.*)$", logical_line)
            if match:
                key = match.group(1).replace("\\ ", " ")
                entries.append(_KeyEntry(key, logical_start, match.start(1) + 1, "string"))
            logical_line = ""
        return entries

    @staticmethod
    def _diagnostic_from_exception(exc: Exception, format_name: str) -> tuple[str, int, int]:
        if isinstance(exc, ConfigurationLimitError):
            return exc.code, exc.line, exc.column
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            return (
                "yaml_parse_error",
                exc.problem_mark.line + 1,
                exc.problem_mark.column + 1,
            )
        if isinstance(exc, tomllib.TOMLDecodeError):
            return "toml_parse_error", getattr(exc, "lineno", 1), getattr(exc, "colno", 1)
        if isinstance(exc, configparser.Error):
            return f"{format_name}_parse_error", getattr(exc, "lineno", 1) or 1, 1
        return f"{format_name}_parse_error", 1, 1
