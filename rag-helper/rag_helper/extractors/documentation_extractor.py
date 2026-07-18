"""Safe structural extraction for Markdown, MDX and reStructuredText.

The implementation is intentionally a syntax reader rather than a renderer:
frontmatter is not constructed, MDX expressions are not evaluated and fenced
code is retained as inert text only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag_helper.extractors.diagram_extractor import DiagramExtractor
from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    normalize_extraction_records,
    stats_for,
)
from rag_helper.utils.ids import safe_id

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_SETEXT_UNDERLINE = re.compile(r"^\s*(=+|-+)\s*$")
_RST_UNDERLINE = re.compile(r"^\s*([=\-~^\"'`:+*#<>_])\1{2,}\s*$")
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
_REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
_WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_RST_LINK = re.compile(r"`([^`<]+?)\s*<([^>]+)>`_")
_LIST_ITEM = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])\s+(.+)$")
_MDX_IMPORT = re.compile(r"^\s*(?:import|export)\s+.+?\s+from\s+['\"]([^'\"]+)['\"]")
_JSX_TAG = re.compile(r"<([A-Z][\w.]*)\b")
_TABLE_DELIMITER_CELL = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True, slots=True)
class _Section:
    level: int
    heading: str
    line: int
    symbol_id: str


class DocumentationExtractor:
    SUPPORTED_EXTENSIONS = {"md", "mdx", "rst"}

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_code_block_chars: int = 16_000,
        max_records: int = 5_000,
        diagram_extractor: DiagramExtractor | None = None,
    ) -> None:
        if max_code_block_chars <= 0 or max_records <= 0:
            raise ValueError("documentation_limits_must_be_positive")
        self.embedding_text_mode = embedding_text_mode
        self.max_code_block_chars = max_code_block_chars
        self.max_records = max_records
        self.diagram_extractor = diagram_extractor or DiagramExtractor(
            embedding_text_mode=embedding_text_mode,
            max_records=max_records,
        )

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported_documentation_extension:{ext}")
        if ext == "rst":
            return self._parse_rst(rel_path, text)
        return self._parse_markdown(rel_path, text, is_mdx=ext == "mdx")

    def _parse_markdown(self, rel_path: str, text: str, *, is_mdx: bool):
        format_name = "mdx" if is_mdx else "md"
        factory = StructuredRecordFactory(rel_path, format_name, self.embedding_text_mode)
        lines = text.splitlines()
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        sections: list[_Section] = []
        stack: list[_Section] = []
        frontmatter_keys: list[str] = []
        code_blocks = 0
        link_count = 0
        list_count = 0
        table_count = 0
        embedded_diagram_count = 0

        cursor = 0
        if lines and lines[0].strip() == "---":
            end = next((i for i in range(1, min(len(lines), 1_000)) if lines[i].strip() in {"---", "..."}), None)
            if end is None:
                diagnostic = factory.diagnostic(
                    "frontmatter_unterminated",
                    "Frontmatter opening delimiter has no closing delimiter.",
                    line=1,
                    fallback="body_text",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
            else:
                frontmatter_id = f"{format_name}_frontmatter:{safe_id(rel_path, 'frontmatter')}"
                for line_index in range(1, end):
                    match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", lines[line_index])
                    if not match:
                        continue
                    key = match.group(1)
                    frontmatter_keys.append(key)
                    details.append(
                        factory.symbol(
                            kind=f"{format_name}_frontmatter_key",
                            name=key,
                            line=line_index + 1,
                            column=match.start(1) + 1,
                            parent_id=frontmatter_id,
                            key_path=key,
                        )
                    )
                details.append(
                    {
                        "kind": f"{format_name}_frontmatter",
                        "file": rel_path,
                        "id": frontmatter_id,
                        "parent_id": factory.file_id,
                        "line": 1,
                        "end_line": end + 1,
                        "keys": frontmatter_keys,
                    }
                )
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind=f"{format_name}_file",
                        source_name=rel_path,
                        relation="contains_frontmatter",
                        target="frontmatter",
                        target_resolved=frontmatter_id,
                        line=1,
                    )
                )
                cursor = end + 1

        reference_targets: dict[str, tuple[str, int]] = {}
        for index, line in enumerate(lines, start=1):
            match = re.match(r"^\s*\[([^\]]+)\]:\s*(\S+)", line)
            if match:
                reference_targets[match.group(1).strip().lower()] = (match.group(2), index)

        in_fence = False
        fence_marker = ""
        fence_language = ""
        fence_start = 0
        fence_lines: list[str] = []
        i = cursor
        while i < len(lines):
            line_no = i + 1
            raw = lines[i]
            stripped = raw.strip()

            fence_match = re.match(r"^\s*(`{3,}|~{3,})\s*([^\s`]*)?.*$", raw)
            if in_fence:
                if (
                    fence_match
                    and fence_match.group(1).startswith(fence_marker[0])
                    and len(fence_match.group(1)) >= len(fence_marker)
                ):
                    code_blocks += 1
                    parent_id = stack[-1].symbol_id if stack else factory.file_id
                    block_id = f"{format_name}_code_block:{safe_id(rel_path, str(fence_start), str(code_blocks))}"
                    content = "\n".join(fence_lines)
                    truncated = len(content) > self.max_code_block_chars
                    details.append(
                        code_block := {
                            "kind": f"{format_name}_code_block",
                            "file": rel_path,
                            "id": block_id,
                            "parent_id": parent_id,
                            "line": fence_start,
                            "end_line": line_no,
                            "language": fence_language or None,
                            "content": content[: self.max_code_block_chars],
                            "truncated": truncated,
                            "executed": False,
                        }
                    )
                    relations.append(
                        factory.relation(
                            source_id=parent_id,
                            source_kind=f"{format_name}_section" if stack else f"{format_name}_file",
                            source_name=stack[-1].heading if stack else rel_path,
                            relation="contains_code_block",
                            target=f"code_block_{code_blocks}",
                            target_resolved=block_id,
                            line=fence_start,
                        )
                    )
                    if truncated:
                        diagnostic = factory.diagnostic(
                            "code_block_truncated",
                            f"Code block exceeds {self.max_code_block_chars} characters.",
                            line=fence_start,
                            fallback="truncated_inert_text",
                        )
                        diagnostics.append(diagnostic)
                        details.append(diagnostic)
                    if self._is_mermaid_language(fence_language):
                        embedded_diagram_count += 1
                        self._append_embedded_mermaid(
                            factory=factory,
                            format_name=format_name,
                            rel_path=rel_path,
                            content=content,
                            fence_start=fence_start,
                            ordinal=embedded_diagram_count,
                            code_block=code_block,
                            truncated=truncated,
                            details=details,
                            relations=relations,
                            diagnostics=diagnostics,
                        )
                    in_fence = False
                    fence_lines = []
                    i += 1
                    continue
                fence_lines.append(raw)
                i += 1
                continue

            if fence_match:
                in_fence = True
                fence_marker = fence_match.group(1)
                fence_language = (fence_match.group(2) or "").lower()
                fence_start = line_no
                fence_lines = []
                i += 1
                continue

            heading_match = _ATX_HEADING.match(raw)
            table = self._markdown_table_at(lines, i)
            if table is not None:
                headers, alignments, last_index = table
                table_count += 1
                parent_id = stack[-1].symbol_id if stack else factory.file_id
                table_record = factory.symbol(
                    kind=f"{format_name}_table",
                    name=" | ".join(headers)[:240] or f"table_{table_count}",
                    line=line_no,
                    end_line=last_index + 1,
                    parent_id=parent_id,
                    ordinal=table_count,
                    columns=headers,
                    alignments=alignments,
                    row_count=max(0, last_index - i - 1),
                )
                details.append(table_record)
                relations.append(
                    factory.relation(
                        source_id=parent_id,
                        source_kind=f"{format_name}_section" if stack else f"{format_name}_file",
                        source_name=stack[-1].heading if stack else rel_path,
                        relation="contains_table",
                        target=f"table_{table_count}",
                        target_resolved=table_record["id"],
                        line=line_no,
                    )
                )
                for table_line_index in range(i, last_index + 1):
                    link_count += self._append_markdown_links(
                        factory,
                        format_name,
                        lines[table_line_index],
                        table_line_index + 1,
                        stack,
                        details,
                        relations,
                        reference_targets,
                    )
                i = last_index
            elif heading_match:
                level = len(heading_match.group(1))
                heading = self._plain_heading(heading_match.group(2))
                self._append_section(factory, format_name, sections, stack, details, relations, heading, level, line_no)
            elif i + 1 < len(lines) and stripped and _SETEXT_UNDERLINE.match(lines[i + 1]):
                level = 1 if lines[i + 1].strip().startswith("=") else 2
                heading = self._plain_heading(stripped)
                self._append_section(factory, format_name, sections, stack, details, relations, heading, level, line_no)
                i += 1
            else:
                list_match = _LIST_ITEM.match(raw)
                if list_match:
                    list_count += 1
                    parent_id = stack[-1].symbol_id if stack else factory.file_id
                    item = factory.symbol(
                        kind=f"{format_name}_list_item",
                        name=self._plain_heading(list_match.group(2))[:160],
                        line=line_no,
                        column=len(list_match.group(1)) + 1,
                        parent_id=parent_id,
                        ordinal=list_count,
                        level=(len(list_match.group(1).expandtabs(2)) // 2) + 1,
                    )
                    details.append(item)
                    relations.append(
                        factory.relation(
                            source_id=parent_id,
                            source_kind=f"{format_name}_section" if stack else f"{format_name}_file",
                            source_name=stack[-1].heading if stack else rel_path,
                            relation="contains_list_item",
                            target=item["name"],
                            target_resolved=item["id"],
                            line=line_no,
                        )
                    )

                link_count += self._append_markdown_links(
                    factory, format_name, raw, line_no, stack, details, relations, reference_targets
                )

                if is_mdx:
                    import_match = _MDX_IMPORT.match(raw)
                    if import_match:
                        target = import_match.group(1)
                        symbol = factory.symbol(
                            kind="mdx_import",
                            name=target,
                            line=line_no,
                            column=import_match.start(1) + 1,
                            parent_id=factory.file_id,
                        )
                        details.append(symbol)
                        relations.append(
                            factory.relation(
                                source_id=factory.file_id,
                                source_kind="mdx_file",
                                source_name=rel_path,
                                relation="imports_module",
                                target=target,
                                line=line_no,
                            )
                        )
                    for ordinal, tag_match in enumerate(_JSX_TAG.finditer(raw), start=1):
                        tag = tag_match.group(1)
                        details.append(
                            factory.symbol(
                                kind="mdx_component_reference",
                                name=tag,
                                line=line_no,
                                column=tag_match.start(1) + 1,
                                parent_id=stack[-1].symbol_id if stack else factory.file_id,
                                ordinal=ordinal,
                            )
                        )

            if len(details) + len(relations) > self.max_records:
                diagnostic = factory.diagnostic(
                    "documentation_record_limit_reached",
                    f"Extraction stopped at the configured {self.max_records} record limit.",
                    line=line_no,
                    fallback="partial_structured_index",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
                break
            i += 1

        if in_fence:
            diagnostic = factory.diagnostic(
                "code_fence_unterminated",
                "Fenced code block has no closing delimiter.",
                line=fence_start,
                fallback="inert_text",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        section_by_slug = {self._slug(section.heading): section for section in sections}
        for relation in relations:
            if relation.get("relation") != "references_anchor":
                continue
            slug = str(relation["target"]).lstrip("#")
            resolved = section_by_slug.get(slug)
            if resolved:
                relation["target_resolved"] = resolved.symbol_id
                relation["resolution_status"] = "resolved"

        index = [
            factory.file_record(
                summary={
                    "heading_count": len(sections),
                    "code_block_count": code_blocks,
                    "link_count": link_count,
                    "list_item_count": list_count,
                    "table_count": table_count,
                    "embedded_diagram_count": embedded_diagram_count,
                    "frontmatter_key_count": len(frontmatter_keys),
                    "diagnostic_count": len(diagnostics),
                },
                labels=[section.heading for section in sections],
            )
        ]
        normalize_extraction_records(
            (index, details, relations),
            rel_path=rel_path,
            source_text=text,
            extractor=type(self).__name__,
        )
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
                heading_count=len(sections),
                code_block_count=code_blocks,
                link_count=link_count,
                list_item_count=list_count,
                table_count=table_count,
                embedded_diagram_count=embedded_diagram_count,
                frontmatter_key_count=len(frontmatter_keys),
            ),
        )

    def _append_section(
        self,
        factory: StructuredRecordFactory,
        format_name: str,
        sections: list[_Section],
        stack: list[_Section],
        details: list[dict],
        relations: list[dict],
        heading: str,
        level: int,
        line: int,
    ) -> None:
        while stack and stack[-1].level >= level:
            stack.pop()
        parent_id = stack[-1].symbol_id if stack else factory.file_id
        symbol = factory.symbol(
            kind=f"{format_name}_section",
            name=heading,
            line=line,
            parent_id=parent_id,
            ordinal=len(sections),
            heading=heading,
            level=level,
            anchor=self._slug(heading),
            section_path=[item.heading for item in stack] + [heading],
        )
        section = _Section(level, heading, line, symbol["id"])
        sections.append(section)
        stack.append(section)
        details.append(symbol)
        relations.append(
            factory.relation(
                source_id=parent_id,
                source_kind=f"{format_name}_section" if len(stack) > 1 else f"{format_name}_file",
                source_name=stack[-2].heading if len(stack) > 1 else factory.rel_path,
                relation="contains_section",
                target=heading,
                target_resolved=symbol["id"],
                line=line,
            )
        )

    def _append_markdown_links(
        self,
        factory: StructuredRecordFactory,
        format_name: str,
        raw: str,
        line: int,
        stack: list[_Section],
        details: list[dict],
        relations: list[dict],
        reference_targets: dict[str, tuple[str, int]],
    ) -> int:
        matches: list[tuple[str, str, int]] = []
        for match in _MARKDOWN_LINK.finditer(raw):
            matches.append((match.group(1) or match.group(2), match.group(2), match.start(2) + 1))
        for match in _REFERENCE_LINK.finditer(raw):
            key = (match.group(2) or match.group(1)).strip().lower()
            if key in reference_targets:
                matches.append((match.group(1), reference_targets[key][0], match.start(1) + 1))
        for match in _WIKI_LINK.finditer(raw):
            matches.append((match.group(2) or match.group(1), match.group(1), match.start(1) + 1))

        parent_id = stack[-1].symbol_id if stack else factory.file_id
        for ordinal, (label, target, column) in enumerate(matches, start=1):
            link = factory.symbol(
                kind=f"{format_name}_link",
                name=label.strip() or target,
                line=line,
                column=column,
                parent_id=parent_id,
                ordinal=ordinal,
                target=target,
            )
            details.append(link)
            relation_type = "references_anchor" if target.startswith("#") else "references_document"
            relations.append(
                factory.relation(
                    source_id=parent_id,
                    source_kind=f"{format_name}_section" if stack else f"{format_name}_file",
                    source_name=stack[-1].heading if stack else factory.rel_path,
                    relation=relation_type,
                    target=target,
                    line=line,
                )
            )
        return len(matches)

    def _parse_rst(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "rst", self.embedding_text_mode)
        lines = text.splitlines()
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        sections: list[_Section] = []
        stack: list[_Section] = []
        adornment_levels: dict[str, int] = {}
        list_count = 0
        link_count = 0
        code_blocks = 0

        i = 0
        while i < len(lines):
            raw = lines[i]
            line_no = i + 1
            if i + 1 < len(lines) and raw.strip():
                underline = _RST_UNDERLINE.match(lines[i + 1])
                if underline:
                    marker = underline.group(1)
                    level = adornment_levels.setdefault(marker, len(adornment_levels) + 1)
                    self._append_section(
                        factory, "rst", sections, stack, details, relations, raw.strip(), level, line_no
                    )
                    i += 2
                    continue

            directive = re.match(r"^\s*\.\.\s+(?:code-block|sourcecode)::\s*([\w+-]*)", raw)
            if directive:
                start = line_no
                language = directive.group(1) or None
                content: list[str] = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or lines[i].startswith(("   ", "\t"))):
                    content.append(lines[i][3:] if lines[i].startswith("   ") else lines[i].lstrip("\t"))
                    i += 1
                code_blocks += 1
                parent_id = stack[-1].symbol_id if stack else factory.file_id
                block_id = f"rst_code_block:{safe_id(rel_path, str(start), str(code_blocks))}"
                content_text = "\n".join(content).strip("\n")
                details.append(
                    {
                        "kind": "rst_code_block",
                        "file": rel_path,
                        "id": block_id,
                        "parent_id": parent_id,
                        "line": start,
                        "end_line": max(start, i),
                        "language": language,
                        "content": content_text[: self.max_code_block_chars],
                        "truncated": len(content_text) > self.max_code_block_chars,
                        "executed": False,
                    }
                )
                relations.append(
                    factory.relation(
                        source_id=parent_id,
                        source_kind="rst_section" if stack else "rst_file",
                        source_name=stack[-1].heading if stack else rel_path,
                        relation="contains_code_block",
                        target=f"code_block_{code_blocks}",
                        target_resolved=block_id,
                        line=start,
                    )
                )
                continue

            list_match = _LIST_ITEM.match(raw)
            if list_match:
                list_count += 1
                parent_id = stack[-1].symbol_id if stack else factory.file_id
                details.append(
                    factory.symbol(
                        kind="rst_list_item",
                        name=list_match.group(2).strip()[:160],
                        line=line_no,
                        column=len(list_match.group(1)) + 1,
                        parent_id=parent_id,
                        ordinal=list_count,
                    )
                )

            for ordinal, match in enumerate(_RST_LINK.finditer(raw), start=1):
                link_count += 1
                parent_id = stack[-1].symbol_id if stack else factory.file_id
                link = factory.symbol(
                    kind="rst_link",
                    name=match.group(1).strip(),
                    line=line_no,
                    column=match.start(1) + 1,
                    parent_id=parent_id,
                    ordinal=ordinal,
                    target=match.group(2),
                )
                details.append(link)
                relations.append(
                    factory.relation(
                        source_id=parent_id,
                        source_kind="rst_section" if stack else "rst_file",
                        source_name=stack[-1].heading if stack else rel_path,
                        relation="references_document",
                        target=match.group(2),
                        line=line_no,
                    )
                )

            if len(details) + len(relations) > self.max_records:
                diagnostic = factory.diagnostic(
                    "documentation_record_limit_reached",
                    f"Extraction stopped at the configured {self.max_records} record limit.",
                    line=line_no,
                    fallback="partial_structured_index",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
                break
            i += 1

        index = [
            factory.file_record(
                summary={
                    "heading_count": len(sections),
                    "code_block_count": code_blocks,
                    "link_count": link_count,
                    "list_item_count": list_count,
                    "diagnostic_count": len(diagnostics),
                },
                labels=[section.heading for section in sections],
            )
        ]
        normalize_extraction_records(
            (index, details, relations),
            rel_path=rel_path,
            source_text=text,
            extractor=type(self).__name__,
        )
        return (
            index,
            details,
            relations,
            stats_for(
                "rst",
                rel_path,
                index,
                details,
                relations,
                diagnostics=diagnostics,
                heading_count=len(sections),
                code_block_count=code_blocks,
                link_count=link_count,
                list_item_count=list_count,
            ),
        )

    @staticmethod
    def _slug(heading: str) -> str:
        normalized = re.sub(r"[^a-z0-9\s-]", "", heading.lower())
        return re.sub(r"[\s-]+", "-", normalized).strip("-")

    @staticmethod
    def _plain_heading(value: str) -> str:
        value = re.sub(r"[`*_~]", "", value)
        value = re.sub(r"<[^>]+>", "", value)
        return " ".join(value.split())

    @staticmethod
    def _split_table_row(raw: str) -> list[str]:
        stripped = raw.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|") and not stripped.endswith("\\|"):
            stripped = stripped[:-1]
        return [
            cell.replace("\\|", "|").strip()
            for cell in re.split(r"(?<!\\)\|", stripped)
        ]

    @classmethod
    def _markdown_table_at(
        cls, lines: list[str], index: int
    ) -> tuple[list[str], list[str], int] | None:
        if index + 1 >= len(lines) or "|" not in lines[index]:
            return None
        headers = cls._split_table_row(lines[index])
        delimiters = cls._split_table_row(lines[index + 1])
        if not headers or len(headers) != len(delimiters):
            return None
        if not all(_TABLE_DELIMITER_CELL.fullmatch(cell) for cell in delimiters):
            return None
        alignments = [
            "center" if cell.startswith(":") and cell.endswith(":")
            else "right" if cell.endswith(":")
            else "left" if cell.startswith(":")
            else "default"
            for cell in delimiters
        ]
        last_index = index + 1
        while last_index + 1 < len(lines):
            candidate = lines[last_index + 1]
            if not candidate.strip() or "|" not in candidate:
                break
            if len(cls._split_table_row(candidate)) != len(headers):
                break
            last_index += 1
        return headers, alignments, last_index

    @staticmethod
    def _is_mermaid_language(language: str) -> bool:
        normalized = str(language or "").strip().lower().strip("{}")
        normalized = normalized.lstrip(".")
        return normalized in {"mermaid", "mmd"}

    def _append_embedded_mermaid(
        self,
        *,
        factory: StructuredRecordFactory,
        format_name: str,
        rel_path: str,
        content: str,
        fence_start: int,
        ordinal: int,
        code_block: dict,
        truncated: bool,
        details: list[dict],
        relations: list[dict],
        diagnostics: list[dict],
    ) -> None:
        if truncated:
            diagnostic = factory.diagnostic(
                "embedded_mermaid_block_truncated",
                "Embedded Mermaid was not parsed because the retained code block is truncated.",
                line=fence_start,
                fallback="inert_code_block_only",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)
            return

        virtual_path = f"{rel_path}#embedded-mermaid-{ordinal}.mmd"
        diagram_index, diagram_details, diagram_relations, diagram_stats = self.diagram_extractor.parse(
            virtual_path,
            content,
        )
        all_diagram_records = diagram_index + diagram_details + diagram_relations
        for record in all_diagram_records:
            record["file"] = rel_path
            record["document_id"] = factory.file_id
            record["embedded"] = True
            if "line" in record:
                record["line"] = int(record["line"]) + fence_start
            if "end_line" in record:
                record["end_line"] = int(record["end_line"]) + fence_start
            for normalized_field in (
                "content_hash",
                "evidence",
                "line_end",
                "line_start",
                "record_id",
                "record_kind",
            ):
                record.pop(normalized_field, None)

        embedded_file = diagram_index[0]
        embedded_file["parent_id"] = code_block["id"]
        embedded_file["line"] = fence_start + 1
        embedded_file["end_line"] = max(fence_start + 1, fence_start + len(content.splitlines()))
        embedded_file["embedded_diagnostic_count"] = diagram_stats.get("diagnostic_count", 0)
        details.append(embedded_file)
        details.extend(diagram_details)
        relations.append(
            factory.relation(
                source_id=code_block["id"],
                source_kind=f"{format_name}_code_block",
                source_name=f"embedded_mermaid_{ordinal}",
                relation="contains_embedded_diagram",
                target=f"embedded_mermaid_{ordinal}",
                target_resolved=embedded_file["id"],
                line=fence_start,
            )
        )
        relations.extend(diagram_relations)
