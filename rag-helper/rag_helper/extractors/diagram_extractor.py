"""Static diagram extraction; includes are recorded but never dereferenced."""

from __future__ import annotations

import base64
import re
import zlib
from html import unescape
from pathlib import PurePosixPath
from urllib.parse import unquote

from rag_helper.domain.xml_security import (
    DEFAULT_MAX_XML_ATTRIBUTES,
    DEFAULT_MAX_XML_DEPTH,
    DEFAULT_MAX_XML_INPUT_SIZE_KB,
    DEFAULT_MAX_XML_NODES,
)
from rag_helper.extractors.structured_support import StructuredRecordFactory, line_number, stats_for
from rag_helper.extractors.xml_security import XmlSecurityError, parse_untrusted_xml


class DiagramExtractor:
    SUPPORTED_EXTENSIONS = {"mmd", "mermaid", "puml", "plantuml", "dot", "gv"}

    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 10_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext in {"mmd", "mermaid"}:
            return self._parse_mermaid(rel_path, text)
        if ext in {"puml", "plantuml"}:
            return self._parse_plantuml(rel_path, text)
        if ext in {"dot", "gv"}:
            return self._parse_dot(rel_path, text)
        raise ValueError(f"unsupported_diagram_extension:{ext}")

    def _parse_mermaid(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "mermaid", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        nodes: dict[str, dict] = {}
        subgraphs: list[str] = []
        diagram_kind = "unknown"
        first_command = next(
            (line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("%%")), ""
        )
        if first_command:
            diagram_kind = first_command.split()[0].lower()

        def ensure_node(identifier: str, label: str | None, line: int) -> dict:
            identifier = identifier.strip()
            if identifier in nodes:
                if label and nodes[identifier].get("label") in {None, identifier}:
                    nodes[identifier]["label"] = label
                return nodes[identifier]
            record = factory.symbol(
                kind="mermaid_node",
                name=identifier,
                line=line,
                ordinal=len(nodes) + 1,
                label=(label or identifier).strip("\"'"),
            )
            nodes[identifier] = record
            details.append(record)
            return record

        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("%%"):
                continue
            click_match = re.match(
                r"click\s+([A-Za-z_][\w.-]*)\s+(?:href\s+)?(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
                stripped,
                re.IGNORECASE,
            )
            if click_match:
                node_name = click_match.group(1)
                target = next(item for item in click_match.group(2, 3, 4) if item)
                node = ensure_node(node_name, None, line_no)
                external = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target))
                relations.append(
                    factory.relation(
                        source_id=node["id"],
                        source_kind="mermaid_node",
                        source_name=node_name,
                        relation="opens_url" if external else "references_document",
                        target=target,
                        line=line_no,
                        link_explicit=True,
                    )
                )
                continue
            subgraph_match = re.match(
                r'subgraph\s+(?:([A-Za-z_][\w-]*)\s*)?(?:\[?"?([^\]"]+)"?\]?)?$',
                stripped,
                re.IGNORECASE,
            )
            if subgraph_match:
                name = (subgraph_match.group(2) or subgraph_match.group(1) or f"subgraph_{len(subgraphs) + 1}").strip()
                subgraphs.append(name)
                details.append(factory.symbol(kind="mermaid_subgraph", name=name, line=line_no, ordinal=len(subgraphs)))
                continue

            sequence = re.match(
                r"\s*([A-Za-z_][\w.-]*?)\s*(-{1,2}x?-?>{1,2}|-{1,2}>>|-->)\s*([A-Za-z_][\w.-]*)\s*:\s*(.*)$", stripped
            )
            if sequence:
                source, arrow, target, label = sequence.groups()
                source_record = ensure_node(source, None, line_no)
                target_record = ensure_node(target, None, line_no)
                relations.append(
                    factory.relation(
                        source_id=source_record["id"],
                        source_kind="mermaid_node",
                        source_name=source,
                        relation="diagram_edge",
                        target=target,
                        target_resolved=target_record["id"],
                        line=line_no,
                        label=label.strip(),
                        edge_syntax=arrow,
                    )
                )
                continue

            edge = re.match(
                r"\s*([A-Za-z_][\w.-]*)(?:\s*(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\}))?\s*"
                r"((?:-->|---|-.->|==>|--o|--x|<\|--|\*--|o--)(?:\|([^|]*)\|)?)\s*"
                r"([A-Za-z_][\w.-]*)(?:\s*(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\}))?",
                stripped,
            )
            if edge:
                source = edge.group(1)
                source_label = next((item for item in edge.group(2, 3, 4) if item), None)
                target = edge.group(7)
                target_label = next((item for item in edge.group(8, 9, 10) if item), None)
                label = edge.group(6)
                source_record = ensure_node(source, source_label, line_no)
                target_record = ensure_node(target, target_label, line_no)
                relations.append(
                    factory.relation(
                        source_id=source_record["id"],
                        source_kind="mermaid_node",
                        source_name=source,
                        relation="diagram_edge",
                        target=target,
                        target_resolved=target_record["id"],
                        line=line_no,
                        label=label.strip() if label else None,
                        edge_syntax=edge.group(5),
                    )
                )
                continue

            class_relation = re.match(r"([A-Za-z_][\w.-]*)\s+(<\|--|\*--|o--|-->)\s+([A-Za-z_][\w.-]*)", stripped)
            if class_relation:
                source, arrow, target = class_relation.groups()
                source_record = ensure_node(source, None, line_no)
                target_record = ensure_node(target, None, line_no)
                relations.append(
                    factory.relation(
                        source_id=source_record["id"],
                        source_kind="mermaid_node",
                        source_name=source,
                        relation="diagram_edge",
                        target=target,
                        target_resolved=target_record["id"],
                        line=line_no,
                        edge_syntax=arrow,
                    )
                )
                continue
            node_match = re.match(
                r"(?:class\s+)?([A-Za-z_][\w.-]*)\s*(?:\[([^\]]+)\]|\(([^)]+)\)|\{([^}]+)\})", stripped
            )
            if node_match:
                ensure_node(node_match.group(1), next(item for item in node_match.group(2, 3, 4) if item), line_no)

        if not first_command or diagram_kind not in {
            "flowchart",
            "graph",
            "sequencediagram",
            "classdiagram",
            "statediagram",
            "statediagram-v2",
            "erdiagram",
            "journey",
            "gantt",
            "pie",
            "mindmap",
            "timeline",
            "gitgraph",
            "quadrantchart",
        }:
            diagnostic = factory.diagnostic(
                "mermaid_unknown_diagram_declaration",
                "Mermaid diagram declaration is missing or unsupported.",
                line=1,
                fallback="text_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        return self._finish(
            factory, "mermaid", rel_path, details, relations, diagnostics, nodes, subgraphs, diagram_kind
        )

    def _parse_plantuml(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "plantuml", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        nodes: dict[str, dict] = {}
        packages: list[str] = []

        def ensure_node(identifier: str, label: str | None, line: int, kind: str = "component") -> dict:
            identifier = identifier.strip('"')
            if identifier in nodes:
                return nodes[identifier]
            record = factory.symbol(
                kind="plantuml_node",
                name=identifier,
                line=line,
                ordinal=len(nodes) + 1,
                label=(label or identifier).strip('"'),
                node_kind=kind,
            )
            nodes[identifier] = record
            details.append(record)
            return record

        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("'"):
                continue
            include_match = re.match(r"!(?:include|include_once|include_many)\s+(.+)$", stripped, re.IGNORECASE)
            if include_match:
                target = include_match.group(1).strip().strip('"<>')
                if self._unsafe_include(target):
                    diagnostic = factory.diagnostic(
                        "plantuml_include_outside_repository",
                        "PlantUML include was blocked because it is absolute, remote or traverses a parent directory.",
                        line=line_no,
                        severity="security",
                        fallback="include_reference_only",
                    )
                    diagnostics.append(diagnostic)
                    details.append(diagnostic)
                else:
                    relations.append(
                        factory.relation(
                            source_id=factory.file_id,
                            source_kind="plantuml_file",
                            source_name=rel_path,
                            relation="includes_diagram",
                            target=target,
                            line=line_no,
                            include_read=False,
                        )
                    )
                continue
            url_match = re.match(
                r"url\s+of\s+([\w.-]+)\s+is\s+\[\[([^\]\s]+)(?:\s+[^\]]+)?\]\]",
                stripped,
                re.IGNORECASE,
            )
            if url_match:
                node_name, target = url_match.groups()
                node = ensure_node(node_name, None, line_no)
                external = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target))
                relations.append(
                    factory.relation(
                        source_id=node["id"],
                        source_kind="plantuml_node",
                        source_name=node_name,
                        relation="opens_url" if external else "references_document",
                        target=target,
                        line=line_no,
                        link_explicit=True,
                    )
                )
                continue
            package_match = re.match(
                r"(?:package|namespace|rectangle|frame|cloud|node)\s+(?:\"([^\"]+)\"|([\w.-]+))\s*\{",
                stripped,
                re.IGNORECASE,
            )
            if package_match:
                name = package_match.group(1) or package_match.group(2)
                packages.append(name)
                details.append(factory.symbol(kind="plantuml_package", name=name, line=line_no, ordinal=len(packages)))
                continue
            node_match = re.match(
                r"(actor|boundary|control|entity|database|collections|queue|component|class|interface|enum|participant)\s+"
                r"(?:\"([^\"]+)\"\s+as\s+([\w.-]+)|([\w.-]+)(?:\s+as\s+\"([^\"]+)\")?)",
                stripped,
                re.IGNORECASE,
            )
            if node_match:
                kind = node_match.group(1).lower()
                identifier = node_match.group(3) or node_match.group(4)
                label = node_match.group(2) or node_match.group(5)
                ensure_node(identifier, label, line_no, kind)
                continue
            bracket_component = re.match(r"\[([^\]]+)\](?:\s+as\s+([\w.-]+))?", stripped)
            if bracket_component:
                ensure_node(
                    bracket_component.group(2) or bracket_component.group(1), bracket_component.group(1), line_no
                )
            edge = re.match(
                r"(?:\[([^\]]+)\]|\"([^\"]+)\"|([\w.-]+))\s*"
                r"([.<|*o#x+\-]+(?:left|right|up|down)?[.<|>*o#x+\-]+)\s*"
                r"(?:\[([^\]]+)\]|\"([^\"]+)\"|([\w.-]+))(?:\s*:\s*(.*))?",
                stripped,
                re.IGNORECASE,
            )
            if edge:
                source = next(item for item in edge.group(1, 2, 3) if item)
                target = next(item for item in edge.group(5, 6, 7) if item)
                source_record = ensure_node(source, source, line_no)
                target_record = ensure_node(target, target, line_no)
                relations.append(
                    factory.relation(
                        source_id=source_record["id"],
                        source_kind="plantuml_node",
                        source_name=source,
                        relation="diagram_edge",
                        target=target,
                        target_resolved=target_record["id"],
                        line=line_no,
                        edge_syntax=edge.group(4),
                        label=(edge.group(8) or "").strip() or None,
                    )
                )

        if "@startuml" not in text.lower() or "@enduml" not in text.lower():
            diagnostic = factory.diagnostic(
                "plantuml_boundary_missing",
                "PlantUML source is missing @startuml or @enduml.",
                line=1,
                fallback="partial_declaration_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        return self._finish(
            factory, "plantuml", rel_path, details, relations, diagnostics, nodes, packages, "component"
        )

    def _parse_dot(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "dot", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        nodes: dict[str, dict] = {}
        subgraphs: list[str] = []
        source = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group(0)), text, flags=re.DOTALL)
        source = re.sub(r"(?m)//.*$|#.*$", "", source)

        def ensure_node(identifier: str, label: str | None, line: int) -> dict:
            identifier = identifier.strip().strip('"')
            if identifier in nodes:
                return nodes[identifier]
            record = factory.symbol(
                kind="dot_node",
                name=identifier,
                line=line,
                ordinal=len(nodes) + 1,
                label=(label or identifier).strip('"'),
            )
            nodes[identifier] = record
            details.append(record)
            return record

        for match in re.finditer(r"\bsubgraph\s+(?:\"([^\"]+)\"|([A-Za-z_][\w]*))\s*\{", source):
            name = match.group(1) or match.group(2)
            subgraphs.append(name)
            details.append(
                factory.symbol(
                    kind="dot_subgraph", name=name, line=line_number(source, match.start()), ordinal=len(subgraphs)
                )
            )
        for match in re.finditer(
            r"(?m)^\s*(\"[^\"]+\"|[A-Za-z_][\w.]*)\s*\[([^\]]*)\]\s*;?",
            source,
        ):
            attrs = match.group(2)
            label_match = re.search(r"\blabel\s*=\s*(?:\"([^\"]*)\"|([^,\]]+))", attrs)
            label = label_match.group(1) or label_match.group(2).strip() if label_match else None
            node = ensure_node(match.group(1), label, line_number(source, match.start()))
            url_match = re.search(r"\bURL\s*=\s*(?:\"([^\"]*)\"|([^,\]]+))", attrs, re.IGNORECASE)
            if url_match:
                target = (url_match.group(1) or url_match.group(2)).strip()
                external = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target))
                relations.append(
                    factory.relation(
                        source_id=node["id"],
                        source_kind="dot_node",
                        source_name=node["name"],
                        relation="opens_url" if external else "references_document",
                        target=target,
                        line=line_number(source, match.start()),
                        link_explicit=True,
                    )
                )
        for match in re.finditer(
            r"(\"[^\"]+\"|[A-Za-z_][\w.]*)\s*(->|--)\s*(\"[^\"]+\"|[A-Za-z_][\w.]*)(?:\s*\[([^\]]*)\])?",
            source,
        ):
            source_name = match.group(1).strip('"')
            target_name = match.group(3).strip('"')
            line = line_number(source, match.start())
            source_record = ensure_node(source_name, None, line)
            target_record = ensure_node(target_name, None, line)
            label_match = re.search(r"\blabel\s*=\s*(?:\"([^\"]*)\"|([^,\]]+))", match.group(4) or "")
            label = (label_match.group(1) or label_match.group(2).strip()) if label_match else None
            relations.append(
                factory.relation(
                    source_id=source_record["id"],
                    source_kind="dot_node",
                    source_name=source_name,
                    relation="diagram_edge",
                    target=target_name,
                    target_resolved=target_record["id"],
                    line=line,
                    edge_syntax=match.group(2),
                    label=label,
                )
            )
        if source.count("{") != source.count("}"):
            diagnostic = factory.diagnostic(
                "dot_unbalanced_braces",
                "DOT source has unbalanced braces.",
                line=max(1, len(text.splitlines())),
                severity="error",
                fallback="partial_declaration_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        return self._finish(factory, "dot", rel_path, details, relations, diagnostics, nodes, subgraphs, "graph")

    def _finish(
        self,
        factory: StructuredRecordFactory,
        format_name: str,
        rel_path: str,
        details: list[dict],
        relations: list[dict],
        diagnostics: list[dict],
        nodes: dict[str, dict],
        groups: list[str],
        diagram_kind: str,
    ):
        if len(details) + len(relations) > self.max_records:
            details = details[: self.max_records]
            relations = relations[: max(0, self.max_records - len(details))]
            diagnostic = factory.diagnostic(
                "diagram_record_limit_reached",
                f"Diagram extraction was truncated at {self.max_records} records.",
                fallback="partial_structured_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
        index = [
            factory.file_record(
                summary={
                    "diagram_kind": diagram_kind,
                    "node_count": len(nodes),
                    "edge_count": sum(item.get("relation") == "diagram_edge" for item in relations),
                    "group_count": len(groups),
                    "diagnostic_count": len(diagnostics),
                },
                labels=[item.get("label") or item["name"] for item in nodes.values()] + groups,
                parser_mode="static_diagram_lexer",
                confidence=0.75,
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
                parser_mode="static_diagram_lexer",
                diagnostics=diagnostics,
                diagram_kind=diagram_kind,
                node_count=len(nodes),
                edge_count=sum(item.get("relation") == "diagram_edge" for item in relations),
                group_count=len(groups),
            ),
        )

    @staticmethod
    def _unsafe_include(target: str) -> bool:
        normalized = target.replace("\\", "/")
        path = PurePosixPath(normalized)
        return (
            not target
            or path.is_absolute()
            or ".." in path.parts
            or bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized))
        )


class DrawioExtractor:
    def __init__(
        self,
        max_xml_nodes: int | None = DEFAULT_MAX_XML_NODES,
        max_xml_input_size_kb: int | None = DEFAULT_MAX_XML_INPUT_SIZE_KB,
        max_xml_depth: int | None = DEFAULT_MAX_XML_DEPTH,
        max_xml_attributes: int | None = DEFAULT_MAX_XML_ATTRIBUTES,
        max_decoded_page_size_kb: int = 5 * 1024,
        embedding_text_mode: str = "verbose",
    ) -> None:
        self.max_xml_nodes = max_xml_nodes
        self.max_xml_input_size_kb = max_xml_input_size_kb
        self.max_xml_depth = max_xml_depth
        self.max_xml_attributes = max_xml_attributes
        self.max_decoded_page_size_kb = max_decoded_page_size_kb
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "drawio", self.embedding_text_mode)
        document = parse_untrusted_xml(
            text,
            max_input_size_kb=self.max_xml_input_size_kb,
            max_nodes=self.max_xml_nodes,
            max_depth=self.max_xml_depth,
            max_attributes=self.max_xml_attributes,
        )
        root = document.root
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        page_count = 0
        decoded_pages = 0
        failed_pages = 0
        node_ids: dict[tuple[int, str], str] = {}
        pending_edges: list[tuple[dict, int, str | None, str | None]] = []

        diagrams = root.xpath(".//*[local-name()='diagram']") if self._local_name(root.tag) != "diagram" else [root]
        for page_index, diagram in enumerate(diagrams, start=1):
            page_count += 1
            page_name = diagram.get("name") or f"Page {page_index}"
            page_id = factory.symbol(
                kind="drawio_page",
                name=page_name,
                line=getattr(diagram, "sourceline", None) or 1,
                ordinal=page_index,
            )
            details.append(page_id)
            graph_model = next(
                (
                    child
                    for child in diagram
                    if isinstance(child.tag, str) and self._local_name(child.tag) == "mxGraphModel"
                ),
                None,
            )
            if graph_model is None:
                payload = (diagram.text or "").strip()
                if not payload:
                    diagnostic = factory.diagnostic(
                        "drawio_page_content_missing",
                        f"draw.io page {page_name} has no graph content.",
                        line=getattr(diagram, "sourceline", None) or 1,
                        fallback="page_metadata_only",
                    )
                    diagnostics.append(diagnostic)
                    details.append(diagnostic)
                    failed_pages += 1
                    continue
                try:
                    decoded = self._decode_page(payload)
                    inner = parse_untrusted_xml(
                        decoded,
                        max_input_size_kb=self.max_decoded_page_size_kb,
                        max_nodes=self.max_xml_nodes,
                        max_depth=self.max_xml_depth,
                        max_attributes=self.max_xml_attributes,
                    )
                    graph_model = inner.root
                    decoded_pages += 1
                except (ValueError, UnicodeError, zlib.error, XmlSecurityError) as exc:
                    diagnostic = factory.diagnostic(
                        "drawio_page_decode_failed",
                        f"draw.io page {page_name} could not be decoded safely ({str(exc).split(':', 1)[0]}).",
                        line=getattr(diagram, "sourceline", None) or 1,
                        severity="error",
                        fallback="page_metadata_only",
                    )
                    diagnostics.append(diagnostic)
                    details.append(diagnostic)
                    failed_pages += 1
                    continue

            for cell in graph_model.xpath(".//*[local-name()='mxCell']"):
                cell_id = cell.get("id")
                if not cell_id:
                    continue
                line = getattr(cell, "sourceline", None) or getattr(diagram, "sourceline", None) or 1
                label = self._plain_label(cell.get("value") or "")
                if cell.get("vertex") == "1" or label:
                    record = factory.symbol(
                        kind="drawio_node",
                        name=label or cell_id,
                        line=line,
                        parent_id=page_id["id"],
                        ordinal=len(node_ids) + 1,
                        drawio_id=cell_id,
                        label=label or None,
                    )
                    details.append(record)
                    node_ids[(page_index, cell_id)] = record["id"]
                if cell.get("edge") == "1" or cell.get("source") or cell.get("target"):
                    pending_edges.append(
                        (
                            {
                                "source": cell.get("source"),
                                "target": cell.get("target"),
                                "label": label or None,
                                "page_name": page_name,
                            },
                            page_index,
                            cell.get("source"),
                            cell.get("target"),
                        )
                    )

        for edge, page_index, source, target in pending_edges:
            source_id = node_ids.get((page_index, source or ""), factory.file_id)
            target_id = node_ids.get((page_index, target or ""))
            relations.append(
                factory.relation(
                    source_id=source_id,
                    source_kind="drawio_node" if source_id != factory.file_id else "drawio_file",
                    source_name=source or edge["page_name"],
                    relation="diagram_edge",
                    target=target or "unknown",
                    target_resolved=target_id,
                    label=edge["label"],
                )
            )

        index = [
            factory.file_record(
                summary={
                    "page_count": page_count,
                    "decoded_page_count": decoded_pages,
                    "failed_page_count": failed_pages,
                    "node_count": len(node_ids),
                    "edge_count": len(relations),
                    "diagnostic_count": len(diagnostics),
                    "xml_node_count": document.node_count,
                },
                labels=[item["name"] for item in details if item.get("kind") in {"drawio_page", "drawio_node"}],
                parser_mode="secure_xml_drawio",
                confidence=1.0 if failed_pages == 0 else 0.6,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "drawio",
                rel_path,
                index,
                details,
                relations,
                parser_mode="secure_xml_drawio",
                diagnostics=diagnostics,
                page_count=page_count,
                decoded_page_count=decoded_pages,
                failed_page_count=failed_pages,
                node_count=len(node_ids),
                edge_count=len(relations),
            ),
        )

    def _decode_page(self, payload: str) -> str:
        compressed = base64.b64decode(payload, validate=True)
        limit = self.max_decoded_page_size_kb * 1024
        decompressor = zlib.decompressobj(-15)
        decoded = decompressor.decompress(compressed, limit + 1)
        if len(decoded) > limit or decompressor.unconsumed_tail:
            raise ValueError("drawio_decoded_page_size_exceeded")
        remaining = limit + 1 - len(decoded)
        decoded += decompressor.flush(max(1, remaining))
        if len(decoded) > limit:
            raise ValueError("drawio_decoded_page_size_exceeded")
        if not decompressor.eof:
            raise ValueError("drawio_compressed_stream_incomplete")
        return unquote(decoded.decode("utf-8", errors="strict"))

    @staticmethod
    def _plain_label(value: str) -> str:
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())[:500]

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag
