from __future__ import annotations

from dataclasses import dataclass

from rag_helper.utils.embedding_text import build_embedding_text, compact_text
from rag_helper.utils.ids import safe_id


@dataclass(frozen=True)
class AdocSection:
    level: int
    heading: str
    section_path: list[str]
    title: str
    body_lines: list[str]
    list_items: list[str]
    code_blocks: list[str]


class AdocExtractor:
    def __init__(self, include_code_blocks: bool = True, embedding_text_mode: str = "verbose") -> None:
        self.include_code_blocks = include_code_blocks
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        document_title, sections = self._parse_sections(text)
        file_id = f"adoc_file:{safe_id(rel_path)}"

        index_records: list[dict] = [{
            "kind": "adoc_file",
            "file": rel_path,
            "id": file_id,
            "title": document_title,
            "section_count": len(sections),
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                f"AsciiDoc file {rel_path}. "
                f"Document title {document_title or 'none'}. "
                f"Section count {len(sections)}."
                ),
                (
                f"AsciiDoc {rel_path}. "
                f"Title {document_title or 'none'}. Sections {len(sections)}."
                ),
            ),
        }]
        detail_records: list[dict] = []
        relation_records: list[dict] = []

        for section in sections:
            section_path = " > ".join(section.section_path)
            section_id = f"adoc_section:{safe_id(rel_path, section_path)}"
            content_text = self._normalize_text(section.body_lines)
            list_text = self._normalize_list_text(section.list_items)

            index_records.append({
                "kind": "adoc_section",
                "file": rel_path,
                "id": section_id,
                "parent_id": file_id if len(section.section_path) == 1 else f"adoc_section:{safe_id(rel_path, ' > '.join(section.section_path[:-1]))}",
                "title": section.title,
                "document_title": document_title,
                "heading": section.heading,
                "level": section.level,
                "section_path": section.section_path,
                "content": content_text,
                "lists": section.list_items,
                "code_block_count": len(section.code_blocks),
                "embedding_text": build_embedding_text(
                    self.embedding_text_mode,
                    (
                    f"AsciiDoc section {section.title} in file {rel_path}. "
                    f"Document title {document_title or 'none'}. "
                    f"Section path {section_path or section.title}. "
                    f"Heading {section.heading}. "
                    f"Text: {content_text[:400] or 'none'}. "
                    f"Lists: {list_text[:200] or 'none'}."
                    ),
                    (
                    f"Section {section.title} in {rel_path}. "
                    f"Path {section_path or section.title}. "
                    f"Text {compact_text(content_text, 120)}. "
                    f"Lists {compact_text(list_text, 80)}."
                    ),
                ),
            })

            detail_records.append({
                "kind": "adoc_section_detail",
                "file": rel_path,
                "id": f"adoc_section_detail:{safe_id(rel_path, section_path)}",
                "parent_id": section_id,
                "section_id": section_id,
                "document_title": document_title,
                "heading": section.heading,
                "level": section.level,
                "section_path": section.section_path,
                "content": content_text,
                "lists": section.list_items,
                "code_blocks": section.code_blocks,
                "embedding_text": build_embedding_text(
                    self.embedding_text_mode,
                    (
                    f"AsciiDoc section detail {section.title} in file {rel_path}. "
                    f"Contains {len(section.list_items)} list items and "
                    f"{len(section.code_blocks)} code blocks."
                    ),
                    (
                    f"Section detail {section.title}. "
                    f"Lists {len(section.list_items)}. Code blocks {len(section.code_blocks)}."
                    ),
                ),
            })

            relation_records.append({
                "kind": "relation",
                "file": rel_path,
                "id": f"relation:{safe_id(rel_path, 'adoc_file', section_id, 'contains_section')}",
                "source_id": file_id,
                "source_kind": "adoc_file",
                "source_name": rel_path,
                "relation": "contains_section",
                "target": section.title,
                "target_resolved": section_id,
                "weight": 1,
            })

            if len(section.section_path) > 1:
                parent_path = " > ".join(section.section_path[:-1])
                relation_records.append({
                    "kind": "relation",
                    "file": rel_path,
                    "id": f"relation:{safe_id(rel_path, section_id, 'child_of', parent_path)}",
                    "source_id": section_id,
                    "source_kind": "adoc_section",
                    "source_name": section.title,
                    "relation": "child_of_section",
                    "target": section.section_path[-2],
                    "target_resolved": f"adoc_section:{safe_id(rel_path, parent_path)}",
                    "weight": 1,
                })
            else:
                relation_records.append({
                    "kind": "relation",
                    "file": rel_path,
                    "id": f"relation:{safe_id(rel_path, section_id, 'child_of_file', file_id)}",
                    "source_id": section_id,
                    "source_kind": "adoc_section",
                    "source_name": section.title,
                    "relation": "child_of_file",
                    "target": rel_path,
                    "target_resolved": file_id,
                    "weight": 1,
                })

            if self.include_code_blocks:
                for index, code_block in enumerate(section.code_blocks, start=1):
                    code_id = f"adoc_code_block:{safe_id(rel_path, section_path, str(index))}"
                    detail_records.append({
                        "kind": "adoc_code_block",
                        "file": rel_path,
                        "id": code_id,
                        "parent_id": section_id,
                        "section_id": section_id,
                        "document_title": document_title,
                        "section_path": section.section_path,
                        "ordinal": index,
                        "content": code_block,
                        "embedding_text": build_embedding_text(
                            self.embedding_text_mode,
                            (
                            f"AsciiDoc code block {index} in section {section.title} "
                            f"from file {rel_path}. Code: {code_block[:400]}"
                            ),
                            (
                            f"Code block {index} in {section.title}. "
                            f"Code {compact_text(code_block, 120)}"
                            ),
                        ),
                    })
                    relation_records.append({
                        "kind": "relation",
                        "file": rel_path,
                        "id": f"relation:{safe_id(rel_path, section_id, 'contains_code_block', str(index))}",
                        "source_id": section_id,
                        "source_kind": "adoc_section",
                        "source_name": section.title,
                        "relation": "contains_code_block",
                        "target": f"code_block_{index}",
                        "target_resolved": code_id,
                        "weight": 1,
                    })

        stats = {
            "kind": "adoc",
            "file": rel_path,
            "title": document_title,
            "section_count": len(sections),
            "code_block_count": sum(len(section.code_blocks) for section in sections),
            "list_item_count": sum(len(section.list_items) for section in sections),
            "index_count": len(index_records),
            "detail_count": len(detail_records),
            "relation_count": len(relation_records),
        }
        return index_records, detail_records, relation_records, stats

    def _parse_sections(self, text: str) -> tuple[str | None, list[AdocSection]]:
        lines = text.splitlines()
        document_title: str | None = None
        sections: list[AdocSection] = []
        active_heading: str | None = None
        active_level = 0
        active_body: list[str] = []
        active_lists: list[str] = []
        active_code_blocks: list[str] = []
        heading_stack: list[str] = []

        in_code_block = False
        code_lines: list[str] = []

        def flush_section() -> None:
            nonlocal active_heading, active_level, active_body, active_lists, active_code_blocks
            if not active_heading:
                active_body = []
                active_lists = []
                active_code_blocks = []
                return
            sections.append(AdocSection(
                level=active_level,
                heading=active_heading,
                section_path=heading_stack[:active_level],
                title=heading_stack[active_level - 1],
                body_lines=active_body[:],
                list_items=active_lists[:],
                code_blocks=active_code_blocks[:],
            ))
            active_body = []
            active_lists = []
            active_code_blocks = []

        for raw_line in lines:
            line = raw_line.rstrip()

            if in_code_block:
                if line.strip() == "----":
                    in_code_block = False
                    active_code_blocks.append("\n".join(code_lines).strip())
                    code_lines = []
                else:
                    code_lines.append(raw_line)
                continue

            stripped = line.strip()
            if stripped.startswith("= ") and document_title is None and active_heading is None:
                document_title = stripped[2:].strip()
                continue

            if stripped == "----":
                in_code_block = True
                code_lines = []
                continue

            if self._is_heading(stripped):
                flush_section()
                level = self._heading_level(stripped)
                heading = stripped[level + 1:].strip()
                while len(heading_stack) >= level:
                    heading_stack.pop()
                heading_stack.append(heading)
                active_heading = heading
                active_level = level
                continue

            if active_heading is None:
                continue

            if self._is_list_item(stripped):
                active_lists.append(self._normalize_list_item(stripped))
                continue

            if stripped:
                active_body.append(stripped)

        if in_code_block and code_lines:
            active_code_blocks.append("\n".join(code_lines).strip())

        flush_section()
        return document_title, sections

    def _is_heading(self, line: str) -> bool:
        if not line or not line.startswith("="):
            return False
        marker_count = len(line) - len(line.lstrip("="))
        return 2 <= marker_count <= 6 and line[marker_count:marker_count + 1] == " "

    def _heading_level(self, line: str) -> int:
        marker_count = len(line) - len(line.lstrip("="))
        return marker_count - 1

    def _is_list_item(self, line: str) -> bool:
        return line.startswith("* ") or line.startswith("- ") or line.startswith(". ")

    def _normalize_list_item(self, line: str) -> str:
        return line[2:].strip()

    def _normalize_text(self, lines: list[str]) -> str:
        return " ".join(line.strip() for line in lines if line.strip())

    def _normalize_list_text(self, items: list[str]) -> str:
        return "; ".join(item.strip() for item in items if item.strip())
