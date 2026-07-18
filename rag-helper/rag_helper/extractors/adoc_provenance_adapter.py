"""Add source provenance records to the established AsciiDoc extractor."""

from __future__ import annotations

import re

from rag_helper.extractors.structured_support import StructuredRecordFactory


class AdocProvenanceAdapter:
    """Compatibility adapter preserving the injected AdocExtractor contract.

    The mature extractor remains the source of section/content records.  This
    adapter only enriches them with deterministic lines and adds inert link,
    list-item and document-attribute records that the original contract did
    not expose individually.
    """

    def __init__(self, delegate: object, embedding_text_mode: str = "verbose") -> None:
        self.delegate = delegate
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        index, details, relations, stats = self.delegate.parse(rel_path, text)
        factory = StructuredRecordFactory(rel_path, "adoc", self.embedding_text_mode)
        lines = text.splitlines()
        heading_lines: dict[str, list[int]] = {}
        active_section_id = factory.file_id
        section_by_line: dict[int, str] = {}
        for line_no, raw in enumerate(lines, start=1):
            match = re.match(r"^(={2,6})\s+(.+?)\s*$", raw)
            if match:
                heading_lines.setdefault(match.group(2).strip(), []).append(line_no)

        heading_cursors: dict[str, int] = {}
        section_records = [item for item in index if item.get("kind") == "adoc_section"]
        for record in section_records:
            heading = str(record.get("heading") or record.get("title") or "")
            positions = heading_lines.get(heading, [])
            cursor = heading_cursors.get(heading, 0)
            line = positions[cursor] if cursor < len(positions) else 1
            heading_cursors[heading] = cursor + 1
            record.setdefault("line", line)
            record.setdefault("column", 1)
            section_by_line[line] = str(record["id"])
        for record in details:
            if record.get("kind") == "adoc_section_detail":
                section_id = record.get("section_id")
                section = next((item for item in section_records if item.get("id") == section_id), None)
                if section:
                    record.setdefault("line", section.get("line", 1))
                    record.setdefault("column", 1)
            elif record.get("kind") == "adoc_code_block":
                content = str(record.get("content") or "")
                offset = text.find(content) if content else -1
                line = text.count("\n", 0, offset) + 1 if offset >= 0 else 1
                record.setdefault("line", line)
                record.setdefault("end_line", line + content.count("\n"))
                record["executed"] = False

        extra_details: list[dict] = []
        extra_relations: list[dict] = []
        section_lines = sorted(section_by_line)
        list_count = 0
        link_count = 0
        attribute_count = 0
        for line_no, raw in enumerate(lines, start=1):
            active_candidates = [value for value in section_lines if value <= line_no]
            if active_candidates:
                active_section_id = section_by_line[active_candidates[-1]]
            attribute = re.match(r"^:([A-Za-z0-9_.-]+):", raw)
            if attribute:
                attribute_count += 1
                extra_details.append(
                    factory.symbol(
                        kind="adoc_document_attribute",
                        name=attribute.group(1),
                        line=line_no,
                        column=attribute.start(1) + 1,
                        ordinal=attribute_count,
                        value_redacted=True,
                    )
                )
            list_item = re.match(r"^\s*(?:[.*+-]|\d+[.)])\s+(.+)$", raw)
            if list_item:
                list_count += 1
                record = factory.symbol(
                    kind="adoc_list_item",
                    name=list_item.group(1).strip()[:200],
                    line=line_no,
                    column=list_item.start(1) + 1,
                    parent_id=active_section_id,
                    ordinal=list_count,
                )
                extra_details.append(record)
                extra_relations.append(
                    factory.relation(
                        source_id=active_section_id,
                        source_kind="adoc_section" if active_section_id != factory.file_id else "adoc_file",
                        source_name=rel_path,
                        relation="contains_list_item",
                        target=record["name"],
                        target_resolved=record["id"],
                        line=line_no,
                    )
                )
            link_patterns = (
                re.finditer(r"(?:xref|link):([^\[]+)\[([^\]]*)\]", raw),
                re.finditer(r"<<([^>,]+)(?:,([^>]+))?>>", raw),
            )
            for matches in link_patterns:
                for match in matches:
                    link_count += 1
                    target = match.group(1).strip()
                    label = (match.group(2) or target).strip()
                    record = factory.symbol(
                        kind="adoc_link",
                        name=label,
                        line=line_no,
                        column=match.start(1) + 1,
                        parent_id=active_section_id,
                        ordinal=link_count,
                        target=target,
                    )
                    extra_details.append(record)
                    extra_relations.append(
                        factory.relation(
                            source_id=active_section_id,
                            source_kind="adoc_section" if active_section_id != factory.file_id else "adoc_file",
                            source_name=rel_path,
                            relation="references_document",
                            target=target,
                            line=line_no,
                        )
                    )

        details.extend(extra_details)
        relations.extend(extra_relations)
        stats = {
            **stats,
            "list_item_record_count": list_count,
            "link_count": link_count,
            "document_attribute_count": attribute_count,
            "detail_count": len(details),
            "relation_count": len(relations),
            "provenance_adapter": True,
        }
        return index, details, relations, stats
