"""Static extraction for declarative data and API contract languages."""

from __future__ import annotations

import json
import re

from rag_helper.extractors.base import FileSkipped
from rag_helper.extractors.json_schema_contract_parser import JsonSchemaContractParser, is_json_schema
from rag_helper.extractors.planning_todo_extractor import PlanningTodoExtractor
from rag_helper.extractors.sql_ddl_lexer import SqlDdlLexer
from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    line_number,
    stats_for,
)


class JsonDocumentExtractor:
    """Recognize JSON Schema and delegate other JSON to an optional extractor."""

    def __init__(
        self,
        fallback_extractor: object | None = None,
        embedding_text_mode: str = "verbose",
        max_nodes: int = 20_000,
        max_depth: int = 64,
        planning_todo_extractor: PlanningTodoExtractor | None = None,
        *,
        schema_parser: JsonSchemaContractParser | None = None,
    ) -> None:
        self.fallback_extractor = fallback_extractor
        self.embedding_text_mode = embedding_text_mode
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.planning_todo_extractor = (
            planning_todo_extractor
            or PlanningTodoExtractor(
                embedding_text_mode=embedding_text_mode,
                max_records=max_nodes,
            )
        )
        self.schema_parser = schema_parser or JsonSchemaContractParser(
            embedding_text_mode=embedding_text_mode,
            max_nodes=max_nodes,
            max_depth=max_depth,
        )

    def parse(self, rel_path: str, text: str):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if self.fallback_extractor is not None:
                return self.fallback_extractor.parse(rel_path, text)
            raise FileSkipped(
                "unsupported_extension",
                {"format_candidate": "json_schema", "diagnostic": "invalid_json"},
            )
        if self.planning_todo_extractor.supports(rel_path, parsed):
            return self.planning_todo_extractor.parse(rel_path, text, parsed)
        if not is_json_schema(parsed):
            if self.fallback_extractor is not None:
                return self.fallback_extractor.parse(rel_path, text)
            raise FileSkipped(
                "unsupported_extension",
                {"format_candidate": "json_schema", "diagnostic": "not_json_schema"},
            )
        return self.schema_parser.parse(rel_path, text, parsed, extractor_name=type(self).__name__)


class SqlExtractor:
    """Bounded DDL outline parser; SQL is tokenized but never sent to a DB."""

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_statements: int = 2_000,
        *,
        lexer: SqlDdlLexer | None = None,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_statements = max_statements
        self.lexer = lexer or SqlDdlLexer()

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "sql", self.embedding_text_mode)
        statements, lexer_diagnostics = self.lexer.split_statements(text)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        tables: dict[str, str] = {}
        views: dict[str, str] = {}
        indexes: list[str] = []
        column_count = 0

        for code, offset in statements[: self.max_statements]:
            normalized = self.lexer.without_leading_comments(code).strip()
            if not normalized:
                continue
            line = line_number(text, offset)
            table_match = re.match(
                rf"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({self.lexer.IDENTIFIER})\s*\(",
                normalized,
                re.IGNORECASE | re.DOTALL,
            )
            if table_match:
                name = self.lexer.clean_identifier(table_match.group(1))
                record = factory.symbol(
                    kind="sql_table",
                    name=name,
                    line=line,
                    ordinal=len(tables) + 1,
                )
                details.append(record)
                tables[name.lower()] = record["id"]
                body = self.lexer.parenthesized_body(normalized, table_match.end() - 1)
                if body is None:
                    diagnostic = factory.diagnostic(
                        "sql_unbalanced_table_definition",
                        f"Table definition for {name} has unbalanced parentheses.",
                        line=line,
                        severity="error",
                        fallback="statement_index",
                    )
                    diagnostics.append(diagnostic)
                    details.append(diagnostic)
                    continue
                for part_ordinal, part in enumerate(self.lexer.split_top_level(body), start=1):
                    part_text = part.strip()
                    part_line = line + body[: body.find(part)].count("\n") if part else line
                    reference = re.search(rf"\bREFERENCES\s+({self.lexer.IDENTIFIER})", part_text, re.IGNORECASE)
                    constraint_prefix = re.match(
                        r"^(?:CONSTRAINT\s+\S+\s+)?(PRIMARY|FOREIGN|UNIQUE|CHECK)\b", part_text, re.IGNORECASE
                    )
                    if constraint_prefix:
                        if reference:
                            target = self.lexer.clean_identifier(reference.group(1))
                            relations.append(
                                factory.relation(
                                    source_id=record["id"],
                                    source_kind="sql_table",
                                    source_name=name,
                                    relation="references_table",
                                    target=target,
                                    line=part_line,
                                )
                            )
                        continue
                    column_match = re.match(
                        rf"({self.lexer.IDENTIFIER})\s+([^\s,()]+(?:\s*\([^)]*\))?)", part_text, re.IGNORECASE
                    )
                    if not column_match:
                        continue
                    column_name = self.lexer.clean_identifier(column_match.group(1))
                    column_count += 1
                    column = factory.symbol(
                        kind="sql_column",
                        name=column_name,
                        line=part_line,
                        parent_id=record["id"],
                        ordinal=part_ordinal,
                        data_type=" ".join(column_match.group(2).split()),
                        nullable=not bool(re.search(r"\bNOT\s+NULL\b", part_text, re.IGNORECASE)),
                        primary_key=bool(re.search(r"\bPRIMARY\s+KEY\b", part_text, re.IGNORECASE)),
                    )
                    details.append(column)
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind="sql_table",
                            source_name=name,
                            relation="defines_column",
                            target=column_name,
                            target_resolved=column["id"],
                            line=part_line,
                        )
                    )
                    if reference:
                        target = self.lexer.clean_identifier(reference.group(1))
                        relations.append(
                            factory.relation(
                                source_id=column["id"],
                                source_kind="sql_column",
                                source_name=column_name,
                                relation="references_table",
                                target=target,
                                line=part_line,
                            )
                        )
                continue

            view_match = re.match(
                rf"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+({self.lexer.IDENTIFIER})\s+AS\s+",
                normalized,
                re.IGNORECASE | re.DOTALL,
            )
            if view_match:
                name = self.lexer.clean_identifier(view_match.group(1))
                record = factory.symbol(
                    kind="sql_view",
                    name=name,
                    line=line,
                    ordinal=len(views) + 1,
                )
                details.append(record)
                views[name.lower()] = record["id"]
                query = normalized[view_match.end() :]
                for target_match in re.finditer(rf"\b(?:FROM|JOIN)\s+({self.lexer.IDENTIFIER})", query, re.IGNORECASE):
                    target = self.lexer.clean_identifier(target_match.group(1))
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind="sql_view",
                            source_name=name,
                            relation="reads_from_relation",
                            target=target,
                            line=line + query[: target_match.start()].count("\n"),
                        )
                    )
                continue

            index_match = re.match(
                rf"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?({self.lexer.IDENTIFIER})\s+ON\s+({self.lexer.IDENTIFIER})",
                normalized,
                re.IGNORECASE | re.DOTALL,
            )
            if index_match:
                name = self.lexer.clean_identifier(index_match.group(1))
                table = self.lexer.clean_identifier(index_match.group(2))
                indexes.append(name)
                record = factory.symbol(
                    kind="sql_index",
                    name=name,
                    line=line,
                    ordinal=len(indexes),
                    table=table,
                )
                details.append(record)
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind="sql_index",
                        source_name=name,
                        relation="indexes_table",
                        target=table,
                        line=line,
                    )
                )

        for relation in relations:
            target_id = tables.get(str(relation["target"]).lower()) or views.get(str(relation["target"]).lower())
            if target_id:
                relation["target_resolved"] = target_id
                relation["resolution_status"] = "resolved"

        for code, offset in lexer_diagnostics:
            diagnostic = factory.diagnostic(
                code,
                "SQL lexer reached the end of input inside a quoted or commented region.",
                line=line_number(text, offset),
                severity="error",
                fallback="partial_ddl_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        if len(statements) > self.max_statements:
            diagnostic = factory.diagnostic(
                "sql_statement_limit_reached",
                f"Only the first {self.max_statements} SQL statements were inspected.",
                fallback="partial_ddl_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        index = [
            factory.file_record(
                summary={
                    "statement_count": len(statements),
                    "table_count": len(tables),
                    "view_count": len(views),
                    "index_count": len(indexes),
                    "column_count": column_count,
                    "diagnostic_count": len(diagnostics),
                },
                labels=list(tables) + list(views) + indexes,
                parser_mode="ddl_lexer",
                confidence=0.8,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "sql",
                rel_path,
                index,
                details,
                relations,
                parser_mode="ddl_lexer",
                diagnostics=diagnostics,
                statement_count=len(statements),
                table_count=len(tables),
                view_count=len(views),
                sql_index_count=len(indexes),
                column_count=column_count,
            ),
        )


class ProtoExtractor:
    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "proto", self.embedding_text_mode)
        source = self._strip_comments(text)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        package_match = re.search(r"\bpackage\s+([A-Za-z_][\w.]*)\s*;", source)
        package = package_match.group(1) if package_match else None
        imports: list[str] = []
        symbols: dict[str, str] = {}

        for ordinal, match in enumerate(
            re.finditer(r"\bimport\s+(?:public\s+|weak\s+)?['\"]([^'\"]+)['\"]\s*;", source), start=1
        ):
            target = match.group(1)
            imports.append(target)
            line = line_number(source, match.start())
            record = factory.symbol(kind="proto_import", name=target, line=line, ordinal=ordinal)
            details.append(record)
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind="proto_file",
                    source_name=rel_path,
                    relation="imports_proto",
                    target=target,
                    line=line,
                )
            )

        blocks = self._blocks(source, {"message", "enum", "service"})
        for ordinal, (kind, name, body, start, body_start) in enumerate(blocks, start=1):
            line = line_number(source, start)
            record = factory.symbol(
                kind=f"proto_{kind}",
                name=name,
                line=line,
                ordinal=ordinal,
                package=package,
            )
            details.append(record)
            symbols[name] = record["id"]
            if kind == "message":
                for field_ordinal, match in enumerate(
                    re.finditer(
                        r"(?m)(?:^|;)\s*(?:optional|required|repeated)?\s*([A-Za-z_.][\w.]*(?:\s*<[^>]+>)?)\s+([A-Za-z_][\w]*)\s*=\s*(\d+)",
                        body,
                    ),
                    start=1,
                ):
                    field_type, field_name, number = match.groups()
                    field_line = line_number(source, body_start + match.start())
                    field = factory.symbol(
                        kind="proto_field",
                        name=field_name,
                        line=field_line,
                        parent_id=record["id"],
                        ordinal=field_ordinal,
                        field_type=field_type,
                        number=int(number),
                        repeated=bool(re.match(r"\s*repeated\b", match.group(0))),
                    )
                    details.append(field)
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind="proto_message",
                            source_name=name,
                            relation="defines_field",
                            target=field_name,
                            target_resolved=field["id"],
                            line=field_line,
                        )
                    )
                    if self._is_named_proto_type(field_type):
                        relations.append(
                            factory.relation(
                                source_id=field["id"],
                                source_kind="proto_field",
                                source_name=field_name,
                                relation="has_type",
                                target=field_type,
                                line=field_line,
                            )
                        )
            elif kind == "service":
                for rpc_ordinal, match in enumerate(
                    re.finditer(
                        r"\brpc\s+([A-Za-z_][\w]*)\s*\(\s*(stream\s+)?([A-Za-z_.][\w.]*)\s*\)\s+returns\s*\(\s*(stream\s+)?([A-Za-z_.][\w.]*)\s*\)",
                        body,
                    ),
                    start=1,
                ):
                    rpc_name = match.group(1)
                    rpc_line = line_number(source, body_start + match.start())
                    rpc = factory.symbol(
                        kind="proto_rpc",
                        name=rpc_name,
                        line=rpc_line,
                        parent_id=record["id"],
                        ordinal=rpc_ordinal,
                        request_type=match.group(3),
                        response_type=match.group(5),
                        client_streaming=bool(match.group(2)),
                        server_streaming=bool(match.group(4)),
                    )
                    details.append(rpc)
                    for relation_type, target in (("accepts_type", match.group(3)), ("returns_type", match.group(5))):
                        relations.append(
                            factory.relation(
                                source_id=rpc["id"],
                                source_kind="proto_rpc",
                                source_name=rpc_name,
                                relation=relation_type,
                                target=target,
                                line=rpc_line,
                            )
                        )

        for relation in relations:
            if relation["target"] in symbols:
                relation["target_resolved"] = symbols[relation["target"]]
                relation["resolution_status"] = "resolved"
        if source.count("{") != source.count("}"):
            diagnostic = factory.diagnostic(
                "proto_unbalanced_braces",
                "Protocol Buffer source has unbalanced braces.",
                line=max(1, len(source.splitlines())),
                severity="error",
                fallback="partial_declaration_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        index = [
            factory.file_record(
                summary={
                    "package": package,
                    "message_count": sum(item.get("kind") == "proto_message" for item in details),
                    "enum_count": sum(item.get("kind") == "proto_enum" for item in details),
                    "service_count": sum(item.get("kind") == "proto_service" for item in details),
                    "field_count": sum(item.get("kind") == "proto_field" for item in details),
                    "rpc_count": sum(item.get("kind") == "proto_rpc" for item in details),
                    "import_count": len(imports),
                    "diagnostic_count": len(diagnostics),
                },
                labels=list(symbols) + imports,
                parser_mode="declaration_lexer",
                confidence=0.8,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "proto",
                rel_path,
                index,
                details,
                relations,
                parser_mode="declaration_lexer",
                diagnostics=diagnostics,
                package=package,
                message_count=sum(item.get("kind") == "proto_message" for item in details),
                service_count=sum(item.get("kind") == "proto_service" for item in details),
                field_count=sum(item.get("kind") == "proto_field" for item in details),
                rpc_count=sum(item.get("kind") == "proto_rpc" for item in details),
            ),
        )

    @staticmethod
    def _strip_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group(0)), text, flags=re.DOTALL)
        return re.sub(r"//[^\n]*", "", text)

    @staticmethod
    def _blocks(text: str, kinds: set[str]) -> list[tuple[str, str, str, int, int]]:
        pattern = re.compile(rf"\b({'|'.join(sorted(kinds))})\s+([A-Za-z_][\w]*)\s*\{{")
        result: list[tuple[str, str, str, int, int]] = []
        for match in pattern.finditer(text):
            depth = 1
            index = match.end()
            while index < len(text) and depth:
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                index += 1
            body_end = index - 1 if depth == 0 else len(text)
            result.append((match.group(1), match.group(2), text[match.end() : body_end], match.start(), match.end()))
        return result

    @staticmethod
    def _is_named_proto_type(value: str) -> bool:
        base = value.rsplit(".", 1)[-1]
        return base not in {
            "double",
            "float",
            "int32",
            "int64",
            "uint32",
            "uint64",
            "sint32",
            "sint64",
            "fixed32",
            "fixed64",
            "sfixed32",
            "sfixed64",
            "bool",
            "string",
            "bytes",
        } and not value.startswith("map<")


class GraphqlExtractor:
    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "graphql", self.embedding_text_mode)
        source = self._mask_strings_and_comments(text)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        symbols: dict[str, str] = {}
        imports: list[str] = []

        for ordinal, match in enumerate(re.finditer(r"(?m)^\s*#\s*import\s+['\"]([^'\"]+)['\"]", text), start=1):
            imports.append(match.group(1))
            line = line_number(text, match.start())
            details.append(factory.symbol(kind="graphql_import", name=match.group(1), line=line, ordinal=ordinal))
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind="graphql_file",
                    source_name=rel_path,
                    relation="imports_graphql",
                    target=match.group(1),
                    line=line,
                )
            )

        block_pattern = re.compile(r"\b(type|interface|input|enum|extend\s+type)\s+([A-Za-z_][\w]*)([^{}]*)\{")
        for ordinal, match in enumerate(block_pattern.finditer(source), start=1):
            kind = match.group(1).replace(" ", "_")
            name = match.group(2)
            body, body_start = self._brace_body(source, match.end() - 1)
            line = line_number(text, match.start())
            record = factory.symbol(
                kind=f"graphql_{kind}",
                name=name,
                line=line,
                ordinal=ordinal,
            )
            details.append(record)
            symbols[name] = record["id"]
            implements = re.search(r"\bimplements\s+([^@{]+)", match.group(3))
            if implements:
                for target in re.findall(r"[A-Za-z_][\w]*", implements.group(1)):
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind=record["kind"],
                            source_name=name,
                            relation="implements_type",
                            target=target,
                            line=line,
                        )
                    )
            for field_ordinal, field_match in enumerate(
                re.finditer(r"(?m)^\s*([A-Za-z_][\w]*)\s*(?:\([^)]*\))?\s*:\s*([\[\]!A-Za-z_][\[\]!\w]*)", body),
                start=1,
            ):
                field_name, field_type = field_match.groups()
                field_line = line_number(text, body_start + field_match.start())
                field = factory.symbol(
                    kind="graphql_field",
                    name=field_name,
                    line=field_line,
                    parent_id=record["id"],
                    ordinal=field_ordinal,
                    field_type=field_type,
                )
                details.append(field)
                target_type = re.sub(r"[\[\]!]", "", field_type)
                relations.append(
                    factory.relation(
                        source_id=field["id"],
                        source_kind="graphql_field",
                        source_name=field_name,
                        relation="returns_type",
                        target=target_type,
                        line=field_line,
                    )
                )

        for ordinal, match in enumerate(
            re.finditer(r"\b(query|mutation|subscription)\s+([A-Za-z_][\w]*)", source), start=1
        ):
            line = line_number(text, match.start())
            details.append(
                factory.symbol(
                    kind="graphql_operation",
                    name=match.group(2),
                    line=line,
                    ordinal=ordinal,
                    operation_type=match.group(1),
                )
            )
        for ordinal, match in enumerate(re.finditer(r"\b(?:scalar|directive\s+@)\s*([A-Za-z_][\w]*)", source), start=1):
            line = line_number(text, match.start())
            kind = "graphql_directive" if "@" in match.group(0) else "graphql_scalar"
            record = factory.symbol(kind=kind, name=match.group(1), line=line, ordinal=ordinal)
            details.append(record)
            symbols[match.group(1)] = record["id"]
        for ordinal, match in enumerate(re.finditer(r"\bunion\s+([A-Za-z_][\w]*)\s*=\s*([^\n]+)", source), start=1):
            line = line_number(text, match.start())
            record = factory.symbol(kind="graphql_union", name=match.group(1), line=line, ordinal=ordinal)
            details.append(record)
            symbols[match.group(1)] = record["id"]
            for target in re.findall(r"[A-Za-z_][\w]*", match.group(2)):
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind="graphql_union",
                        source_name=record["name"],
                        relation="includes_type",
                        target=target,
                        line=line,
                    )
                )

        for relation in relations:
            if relation["target"] in symbols:
                relation["target_resolved"] = symbols[relation["target"]]
                relation["resolution_status"] = "resolved"
        if source.count("{") != source.count("}"):
            diagnostic = factory.diagnostic(
                "graphql_unbalanced_braces",
                "GraphQL source has unbalanced braces.",
                line=max(1, len(text.splitlines())),
                severity="error",
                fallback="partial_declaration_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        index = [
            factory.file_record(
                summary={
                    "type_count": sum(str(item.get("kind", "")).startswith("graphql_type") for item in details),
                    "field_count": sum(item.get("kind") == "graphql_field" for item in details),
                    "operation_count": sum(item.get("kind") == "graphql_operation" for item in details),
                    "import_count": len(imports),
                    "diagnostic_count": len(diagnostics),
                },
                labels=list(symbols) + imports,
                parser_mode="declaration_lexer",
                confidence=0.75,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "graphql",
                rel_path,
                index,
                details,
                relations,
                parser_mode="declaration_lexer",
                diagnostics=diagnostics,
                type_count=len(symbols),
                field_count=sum(item.get("kind") == "graphql_field" for item in details),
                operation_count=sum(item.get("kind") == "graphql_operation" for item in details),
                import_count=len(imports),
            ),
        )

    @staticmethod
    def _mask_strings_and_comments(text: str) -> str:
        source = re.sub(r'""".*?"""', lambda match: re.sub(r"[^\n]", " ", match.group(0)), text, flags=re.DOTALL)
        source = re.sub(r'"(?:\\.|[^"\\])*"', lambda match: " " * len(match.group(0)), source)
        return re.sub(r"#[^\n]*", "", source)

    @staticmethod
    def _brace_body(text: str, opening: int) -> tuple[str, int]:
        depth = 1
        index = opening + 1
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        return text[opening + 1 : index - 1 if depth == 0 else len(text)], opening + 1


class TerraformExtractor:
    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "terraform", self.embedding_text_mode)
        source = self._mask_comments(text)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        symbols: dict[str, str] = {}
        block_pattern = re.compile(
            r'(?m)^\s*(resource|data|module|variable|output|provider|terraform|locals)\s*(?:"([^"]+)")?\s*(?:"([^"]+)")?\s*\{'
        )
        for ordinal, match in enumerate(block_pattern.finditer(source), start=1):
            block_kind, first_label, second_label = match.groups()
            name = ".".join(item for item in (first_label, second_label) if item) or block_kind
            address = (
                f"{block_kind}.{name}"
                if block_kind in {"resource", "data"}
                else f"{block_kind}.{first_label}"
                if first_label
                else block_kind
            )
            line = line_number(text, match.start())
            body, body_start = self._brace_body(source, match.end() - 1)
            record = factory.symbol(
                kind=f"terraform_{block_kind}",
                name=name,
                line=line,
                ordinal=ordinal,
                address=address,
                resource_type=first_label if block_kind in {"resource", "data"} else None,
            )
            details.append(record)
            symbols[address] = record["id"]
            if block_kind == "module":
                source_match = re.search(r'(?m)^\s*source\s*=\s*"([^"]+)"', body)
                if source_match:
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind=record["kind"],
                            source_name=name,
                            relation="loads_module_source",
                            target=source_match.group(1),
                            line=line_number(text, body_start + source_match.start()),
                        )
                    )
            for reference_match in re.finditer(r"\b((?:data\.)?[A-Za-z_][\w-]*\.[A-Za-z_][\w-]*)\b", body):
                target = reference_match.group(1)
                if target.startswith(("var.", "local.", "module.", "path.", "each.", "count.")):
                    continue
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind=record["kind"],
                        source_name=name,
                        relation="references_terraform_object",
                        target=target,
                        line=line_number(text, body_start + reference_match.start()),
                    )
                )

        for relation in relations:
            target_id = symbols.get(relation["target"])
            if target_id:
                relation["target_resolved"] = target_id
                relation["resolution_status"] = "resolved"
        if source.count("{") != source.count("}"):
            diagnostic = factory.diagnostic(
                "terraform_unbalanced_braces",
                "Terraform source has unbalanced braces.",
                line=max(1, len(text.splitlines())),
                severity="error",
                fallback="partial_block_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        index = [
            factory.file_record(
                summary={
                    "block_count": len(symbols),
                    "resource_count": sum(item.get("kind") == "terraform_resource" for item in details),
                    "module_count": sum(item.get("kind") == "terraform_module" for item in details),
                    "diagnostic_count": len(diagnostics),
                },
                labels=list(symbols),
                parser_mode="hcl_block_lexer",
                confidence=0.7,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "terraform",
                rel_path,
                index,
                details,
                relations,
                parser_mode="hcl_block_lexer",
                diagnostics=diagnostics,
                block_count=len(symbols),
                resource_count=sum(item.get("kind") == "terraform_resource" for item in details),
                module_count=sum(item.get("kind") == "terraform_module" for item in details),
            ),
        )

    @staticmethod
    def _mask_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group(0)), text, flags=re.DOTALL)
        return re.sub(r"(?m)(?:#|//).*?$", "", text)

    @staticmethod
    def _brace_body(text: str, opening: int) -> tuple[str, int]:
        depth = 1
        quote = False
        index = opening + 1
        while index < len(text) and depth:
            char = text[index]
            if char == '"' and (index == 0 or text[index - 1] != "\\"):
                quote = not quote
            elif not quote and char == "{":
                depth += 1
            elif not quote and char == "}":
                depth -= 1
            index += 1
        return text[opening + 1 : index - 1 if depth == 0 else len(text)], opening + 1
