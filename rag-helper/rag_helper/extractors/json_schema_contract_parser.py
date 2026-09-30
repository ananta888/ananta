"""JSON Schema contract parsing for :class:`JsonDocumentExtractor`.

The parser owns the bounded JSON Schema walk (pointers, ``$id``/``$ref``,
compositions, definitions and properties) so the document extractor only
decides *which* parser handles a JSON document.
"""

from __future__ import annotations

import json

from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    line_number,
    normalize_extraction_records,
    stats_for,
)


def is_json_schema(value: object) -> bool:
    """Return whether a decoded JSON value looks like a JSON Schema document."""
    return isinstance(value, dict) and (
        "$schema" in value
        or "$defs" in value
        or "definitions" in value
        or (value.get("type") == "object" and "properties" in value)
    )


class JsonSchemaContractParser:
    """Bounded JSON Schema outline parser producing structured index records."""

    def __init__(
        self,
        *,
        embedding_text_mode: str = "verbose",
        max_nodes: int = 20_000,
        max_depth: int = 64,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def parse(self, rel_path: str, text: str, schema: dict, *, extractor_name: str):
        factory = StructuredRecordFactory(rel_path, "json_schema", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        symbols: dict[str, str] = {"#": factory.file_id}
        node_count = 0
        cursor_by_token: dict[str, int] = {}

        def locate(token: str) -> tuple[int, int]:
            encoded = json.dumps(token)
            start = cursor_by_token.get(encoded, 0)
            offset = text.find(encoded, start)
            if offset < 0:
                offset = text.find(encoded)
            if offset < 0:
                return 1, 1
            cursor_by_token[encoded] = offset + len(encoded)
            previous_newline = text.rfind("\n", 0, offset)
            return line_number(text, offset), offset - previous_newline

        def visit(value: object, pointer: str, parent_id: str, depth: int) -> None:
            nonlocal node_count
            node_count += 1
            if node_count > self.max_nodes:
                raise ValueError("json_schema_node_limit_exceeded")
            if depth > self.max_depth:
                raise ValueError("json_schema_depth_limit_exceeded")
            if not isinstance(value, dict):
                return

            pointer_token = pointer.rsplit("/", 1)[-1] if "/" in pointer else "#"
            pointer_line, pointer_column = (1, 1) if pointer == "#" else locate(
                pointer_token.replace("~1", "/").replace("~0", "~")
            )
            pointer_record = factory.symbol(
                kind="json_schema_pointer",
                name=pointer,
                line=pointer_line,
                column=pointer_column,
                parent_id=parent_id,
                ordinal=node_count,
                pointer=pointer,
                schema_type=self._schema_type(value),
            )
            details.append(pointer_record)

            node_schema_id = value.get("$id")
            if isinstance(node_schema_id, str):
                id_line, id_column = locate("$id")
                details.append(
                    factory.symbol(
                        kind="json_schema_id",
                        name=node_schema_id,
                        line=id_line,
                        column=id_column,
                        parent_id=pointer_record["id"],
                        ordinal=node_count,
                        pointer=f"{pointer}/$id" if pointer != "#" else "#/$id",
                        schema_id=node_schema_id,
                    )
                )

            ref = value.get("$ref")
            if isinstance(ref, str):
                ref_line, ref_column = locate("$ref")
                details.append(
                    factory.symbol(
                        kind="json_schema_ref",
                        name=ref,
                        line=ref_line,
                        column=ref_column,
                        parent_id=pointer_record["id"],
                        ordinal=node_count,
                        pointer=f"{pointer}/$ref" if pointer != "#" else "#/$ref",
                        ref=ref,
                    )
                )
                relations.append(
                    factory.relation(
                        source_id=parent_id,
                        source_kind="json_schema_node",
                        source_name=pointer,
                        relation="references_schema",
                        target=ref,
                        target_resolved=symbols.get(ref),
                        line=ref_line,
                    )
                )

            for composition in ("allOf", "anyOf", "oneOf"):
                options = value.get(composition)
                if not isinstance(options, list):
                    continue
                for option_index, option in enumerate(options):
                    option_pointer = f"{pointer}/{composition}/{option_index}"
                    relations.append(
                        factory.relation(
                            source_id=parent_id,
                            source_kind="json_schema_node",
                            source_name=pointer,
                            relation=f"composes_{composition}",
                            target=option_pointer,
                            line=locate(composition)[0],
                        )
                    )
                    visit(option, option_pointer, parent_id, depth + 1)

            definitions = value.get("$defs") if isinstance(value.get("$defs"), dict) else value.get("definitions")
            if isinstance(definitions, dict):
                container = "$defs" if "$defs" in value else "definitions"
                for ordinal, (name, definition) in enumerate(definitions.items(), start=1):
                    definition_pointer = f"#/{container}/{self._escape_pointer(str(name))}"
                    line, column = locate(str(name))
                    record = factory.symbol(
                        kind="json_schema_definition",
                        name=str(name),
                        line=line,
                        column=column,
                        parent_id=parent_id,
                        ordinal=ordinal,
                        pointer=definition_pointer,
                        schema_type=self._schema_type(definition),
                    )
                    details.append(record)
                    symbols[definition_pointer] = record["id"]
                    relations.append(
                        factory.relation(
                            source_id=parent_id,
                            source_kind="json_schema_node",
                            source_name=pointer,
                            relation="defines_schema",
                            target=definition_pointer,
                            target_resolved=record["id"],
                            line=line,
                        )
                    )
                    visit(definition, definition_pointer, record["id"], depth + 1)

            properties = value.get("properties")
            required = set(value.get("required") if isinstance(value.get("required"), list) else [])
            if isinstance(properties, dict):
                for ordinal, (name, property_schema) in enumerate(properties.items(), start=1):
                    property_pointer = f"{pointer}/properties/{self._escape_pointer(str(name))}"
                    line, column = locate(str(name))
                    record = factory.symbol(
                        kind="json_schema_property",
                        name=str(name),
                        line=line,
                        column=column,
                        parent_id=parent_id,
                        ordinal=ordinal,
                        pointer=property_pointer,
                        schema_type=self._schema_type(property_schema),
                        required=name in required,
                    )
                    details.append(record)
                    symbols[property_pointer] = record["id"]
                    relations.append(
                        factory.relation(
                            source_id=parent_id,
                            source_kind="json_schema_node",
                            source_name=pointer,
                            relation="defines_property",
                            target=str(name),
                            target_resolved=record["id"],
                            line=line,
                        )
                    )
                    visit(property_schema, property_pointer, record["id"], depth + 1)

            items = value.get("items")
            if isinstance(items, dict):
                visit(items, f"{pointer}/items", parent_id, depth + 1)

        try:
            visit(schema, "#", factory.file_id, 1)
        except ValueError as exc:
            diagnostic = factory.diagnostic(
                str(exc),
                "JSON Schema resource limit reached; partial records were retained.",
                fallback="partial_structured_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        # Resolve local refs after every definition has been visited.
        for relation in relations:
            if relation["relation"] == "references_schema" and relation["target"] in symbols:
                relation["target_resolved"] = symbols[relation["target"]]
                relation["resolution_status"] = "resolved"

        schema_id = schema.get("$id") if isinstance(schema.get("$id"), str) else None
        index = [
            factory.file_record(
                summary={
                    "schema_id": schema_id,
                    "definition_count": sum(item.get("kind") == "json_schema_definition" for item in details),
                    "property_count": sum(item.get("kind") == "json_schema_property" for item in details),
                    "reference_count": sum(item.get("relation") == "references_schema" for item in relations),
                    "pointer_count": sum(item.get("kind") == "json_schema_pointer" for item in details),
                    "id_record_count": sum(item.get("kind") == "json_schema_id" for item in details),
                    "diagnostic_count": len(diagnostics),
                },
                labels=[item["name"] for item in details if item.get("name")],
                parser_mode="stdlib_json",
            )
        ]
        normalize_extraction_records(
            (index, details, relations),
            rel_path=rel_path,
            source_text=text,
            extractor=extractor_name,
        )
        return (
            index,
            details,
            relations,
            stats_for(
                "json_schema",
                rel_path,
                index,
                details,
                relations,
                parser_mode="stdlib_json",
                diagnostics=diagnostics,
                schema_id=schema_id,
                definition_count=sum(item.get("kind") == "json_schema_definition" for item in details),
                property_count=sum(item.get("kind") == "json_schema_property" for item in details),
                reference_count=sum(item.get("relation") == "references_schema" for item in relations),
                pointer_count=sum(item.get("kind") == "json_schema_pointer" for item in details),
                id_record_count=sum(item.get("kind") == "json_schema_id" for item in details),
            ),
        )

    @staticmethod
    def _escape_pointer(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _schema_type(value: object) -> str | list[str] | None:
        if not isinstance(value, dict):
            return None
        schema_type = value.get("type")
        if isinstance(schema_type, str) or (
            isinstance(schema_type, list) and all(isinstance(item, str) for item in schema_type)
        ):
            return schema_type
        return None
