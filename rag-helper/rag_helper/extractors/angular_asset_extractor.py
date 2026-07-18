"""Non-rendering extraction for Angular templates and stylesheet sources."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from rag_helper.extractors.structured_support import StructuredRecordFactory, stats_for

_ANGULAR_BLOCK = re.compile(r"@(if|for|switch|case|default|defer|let)\b", re.IGNORECASE)
_INTERPOLATION = re.compile(r"{{(.*?)}}", re.DOTALL)
_PIPE = re.compile(r"(?<!\|)\|\s*([A-Za-z_$][\w$]*)")


class _AngularHtmlParser(HTMLParser):
    def __init__(self, factory: StructuredRecordFactory) -> None:
        super().__init__(convert_charrefs=True)
        self.factory = factory
        self.details: list[dict] = []
        self.relations: list[dict] = []
        self.tags: list[str] = []
        self.template_refs: list[str] = []
        self.bindings: list[str] = []
        self.outputs: list[str] = []
        self.directives: list[str] = []
        self.pipes: list[str] = []
        self.stack: list[tuple[str, str]] = []
        self.ordinal = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self.getpos()[0], self.getpos()[1] + 1, is_void=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self.getpos()[0], self.getpos()[1] + 1, is_void=True)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if any(item[0] == normalized_tag for item in self.stack):
            while self.stack:
                popped_tag, _ = self.stack.pop()
                if popped_tag == normalized_tag:
                    break

    def handle_data(self, data: str) -> None:
        line, column = self.getpos()
        for interpolation in _INTERPOLATION.finditer(data):
            self._append_pipes(interpolation.group(1), line, column + interpolation.start(1) + 1)

    def _handle_tag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        line: int,
        column: int,
        *,
        is_void: bool,
    ) -> None:
        normalized_tag = tag.lower()
        self.ordinal += 1
        parent_id = self.stack[-1][1] if self.stack else self.factory.file_id
        record = self.factory.symbol(
            kind="html_element",
            name=normalized_tag,
            line=line,
            column=column,
            parent_id=parent_id,
            ordinal=self.ordinal,
            tag=normalized_tag,
            is_component="-" in normalized_tag,
        )
        self.details.append(record)
        self.tags.append(normalized_tag)
        self.relations.append(
            self.factory.relation(
                source_id=parent_id,
                source_kind="html_element" if parent_id != self.factory.file_id else "html_file",
                source_name=normalized_tag,
                relation="contains_element",
                target=normalized_tag,
                target_resolved=record["id"],
                line=line,
            )
        )
        if "-" in normalized_tag:
            self.relations.append(
                self.factory.relation(
                    source_id=self.factory.file_id,
                    source_kind="html_file",
                    source_name=self.factory.rel_path,
                    relation="uses_component_selector",
                    target=normalized_tag,
                    line=line,
                    selector=normalized_tag,
                )
            )

        for attr_ordinal, (raw_name, value) in enumerate(attrs, start=1):
            name = raw_name.strip()
            lowered = name.lower()
            value = value or ""
            category, normalized = self._classify_attribute(name)
            if category:
                attribute = self.factory.symbol(
                    kind=f"angular_{category}",
                    name=normalized,
                    line=line,
                    column=column,
                    parent_id=record["id"],
                    ordinal=attr_ordinal,
                    attribute=name,
                    expression_present=bool(value),
                )
                self.details.append(attribute)
                self.relations.append(
                    self.factory.relation(
                        source_id=record["id"],
                        source_kind="html_element",
                        source_name=normalized_tag,
                        relation=f"has_{category}",
                        target=normalized,
                        target_resolved=attribute["id"],
                        line=line,
                    )
                )
                if category == "template_reference":
                    self.template_refs.append(normalized)
                elif category in {"input_binding", "two_way_binding"}:
                    self.bindings.append(normalized)
                elif category == "output_binding":
                    self.outputs.append(normalized)
                elif category == "directive":
                    self.directives.append(normalized)
            if lowered in {"ngif", "ngfor", "ngswitch", "ngswitchcase", "ngswitchdefault"}:
                self.directives.append(lowered)
            self._append_pipes(value, line, column)

        if not is_void and normalized_tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((normalized_tag, record["id"]))

    @staticmethod
    def _classify_attribute(name: str) -> tuple[str | None, str]:
        if name.startswith("#"):
            return "template_reference", name[1:]
        if name.startswith("[(") and name.endswith(")]"):
            return "two_way_binding", name[2:-2]
        if name.startswith("[") and name.endswith("]"):
            return "input_binding", name[1:-1]
        if name.startswith("(") and name.endswith(")"):
            return "output_binding", name[1:-1]
        if name.startswith("*"):
            return "directive", name[1:]
        if name.lower().startswith(("ng-", "ng")):
            return "directive", name
        return None, name

    def _append_pipes(self, expression: str, line: int, column: int) -> None:
        for ordinal, match in enumerate(_PIPE.finditer(expression), start=1):
            pipe = match.group(1)
            self.pipes.append(pipe)
            record = self.factory.symbol(
                kind="angular_pipe_reference",
                name=pipe,
                line=line,
                column=column + match.start(1),
                parent_id=self.stack[-1][1] if self.stack else self.factory.file_id,
                ordinal=ordinal,
            )
            self.details.append(record)
            self.relations.append(
                self.factory.relation(
                    source_id=self.factory.file_id,
                    source_kind="html_file",
                    source_name=self.factory.rel_path,
                    relation="uses_pipe",
                    target=pipe,
                    line=line,
                )
            )


class AngularTemplateExtractor:
    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 10_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "html", self.embedding_text_mode)
        parser = _AngularHtmlParser(factory)
        diagnostics: list[dict] = []
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            diagnostic = factory.diagnostic(
                "html_parse_error",
                "HTML tokenization failed; partial records were retained.",
                line=parser.getpos()[0],
                column=parser.getpos()[1] + 1,
                severity="error",
                fallback="partial_text_index",
            )
            diagnostics.append(diagnostic)
            parser.details.append(diagnostic)

        block_directives: list[str] = []
        for match in _ANGULAR_BLOCK.finditer(text):
            directive = f"@{match.group(1).lower()}"
            block_directives.append(directive)
            line = text.count("\n", 0, match.start()) + 1
            record = factory.symbol(
                kind="angular_control_flow",
                name=directive,
                line=line,
                column=match.start() - text.rfind("\n", 0, match.start()),
                ordinal=len(block_directives),
            )
            parser.details.append(record)

        if len(parser.details) + len(parser.relations) > self.max_records:
            parser.details = parser.details[: self.max_records]
            parser.relations = parser.relations[: max(0, self.max_records - len(parser.details))]
            diagnostic = factory.diagnostic(
                "html_record_limit_reached",
                f"Template extraction was truncated at {self.max_records} records.",
                fallback="partial_structured_index",
            )
            diagnostics.append(diagnostic)
            parser.details.append(diagnostic)

        labels = list(dict.fromkeys(parser.tags + parser.directives + parser.pipes + block_directives))
        index = [
            factory.file_record(
                summary={
                    "element_count": len(parser.tags),
                    "component_tag_count": sum("-" in tag for tag in parser.tags),
                    "template_reference_count": len(parser.template_refs),
                    "input_binding_count": len(parser.bindings),
                    "output_binding_count": len(parser.outputs),
                    "directive_count": len(parser.directives) + len(block_directives),
                    "pipe_count": len(parser.pipes),
                    "diagnostic_count": len(diagnostics),
                },
                labels=labels,
                parser_mode="html_tokenizer",
                confidence=0.9,
            )
        ]
        return (
            index,
            parser.details,
            parser.relations,
            stats_for(
                "html",
                rel_path,
                index,
                parser.details,
                parser.relations,
                parser_mode="html_tokenizer",
                diagnostics=diagnostics,
                element_count=len(parser.tags),
                component_tag_count=sum("-" in tag for tag in parser.tags),
                directive_count=len(parser.directives) + len(block_directives),
                pipe_count=len(parser.pipes),
            ),
        )


class StylesheetExtractor:
    SUPPORTED_EXTENSIONS = {"css", "scss", "sass", "less"}
    _COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
    _IMPORT = re.compile(r"@(?:import|use|forward)\s+(?:url\(\s*)?[\"']?([^\"'\s);]+)", re.IGNORECASE)
    _CUSTOM_PROPERTY = re.compile(r"(?m)(?:^|[;{])\s*(--[A-Za-z0-9_-]+|\$[A-Za-z0-9_-]+|@[A-Za-z0-9_-]+)\s*:")
    _SELECTOR = re.compile(r"([^{}]+)\{")

    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 5_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported_stylesheet_extension:{ext}")
        factory = StructuredRecordFactory(rel_path, ext, self.embedding_text_mode)
        # Replace comment content with whitespace to keep character/line offsets stable.
        source = self._COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        imports: list[str] = []
        selectors: list[str] = []
        variables: list[str] = []

        for ordinal, match in enumerate(self._IMPORT.finditer(source), start=1):
            target = match.group(1)
            imports.append(target)
            line, column = self._position(source, match.start(1))
            record = factory.symbol(
                kind=f"{ext}_import",
                name=target,
                line=line,
                column=column,
                ordinal=ordinal,
            )
            details.append(record)
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind=f"{ext}_file",
                    source_name=rel_path,
                    relation="imports_stylesheet",
                    target=target,
                    line=line,
                )
            )

        for ordinal, match in enumerate(self._CUSTOM_PROPERTY.finditer(source), start=1):
            name = match.group(1)
            variables.append(name)
            line, column = self._position(source, match.start(1))
            details.append(
                factory.symbol(
                    kind=f"{ext}_variable",
                    name=name,
                    line=line,
                    column=column,
                    ordinal=ordinal,
                    variable_kind=("custom_property" if name.startswith("--") else "preprocessor_variable"),
                )
            )

        for ordinal, match in enumerate(self._SELECTOR.finditer(source), start=1):
            raw_selector = " ".join(match.group(1).split())
            if ";" in raw_selector:
                raw_selector = raw_selector.rsplit(";", 1)[-1].strip()
            # At-rules are containers/declarations rather than selectors.
            if not raw_selector or raw_selector.lstrip().startswith("@") or ":" in raw_selector and ";" in raw_selector:
                continue
            for selector in (item.strip() for item in raw_selector.split(",")):
                if not selector:
                    continue
                selectors.append(selector)
                line, column = self._position(source, match.start(1))
                details.append(
                    factory.symbol(
                        kind=f"{ext}_selector",
                        name=selector[:300],
                        line=line,
                        column=column,
                        ordinal=ordinal,
                    )
                )

        if ext == "sass":
            known_selectors = set(selectors)
            for ordinal, raw in enumerate(source.splitlines(), start=1):
                stripped = raw.strip()
                if (
                    not stripped
                    or raw[:1].isspace()
                    or stripped.startswith(("@", "$", "//"))
                    or ":" in stripped
                    and not stripped.startswith((".", "#", "[", ":", "&"))
                    or stripped in known_selectors
                ):
                    continue
                selectors.append(stripped)
                known_selectors.add(stripped)
                details.append(
                    factory.symbol(
                        kind="sass_selector",
                        name=stripped[:300],
                        line=ordinal,
                        column=1,
                        ordinal=len(selectors),
                    )
                )

        if source.count("{") != source.count("}") and ext != "sass":
            diagnostic = factory.diagnostic(
                "stylesheet_unbalanced_braces",
                "Stylesheet contains unbalanced braces; heuristic records were retained.",
                line=max(1, len(source.splitlines())),
                severity="error",
                fallback="heuristic_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        if len(details) + len(relations) > self.max_records:
            details = details[: self.max_records]
            relations = relations[: max(0, self.max_records - len(details))]
            diagnostic = factory.diagnostic(
                "stylesheet_record_limit_reached",
                f"Stylesheet extraction was truncated at {self.max_records} records.",
                fallback="partial_structured_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        index = [
            factory.file_record(
                summary={
                    "selector_count": len(selectors),
                    "variable_count": len(variables),
                    "import_count": len(imports),
                    "diagnostic_count": len(diagnostics),
                },
                labels=selectors + variables + imports,
                parser_mode="safe_heuristic",
                confidence=0.75,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                ext,
                rel_path,
                index,
                details,
                relations,
                parser_mode="safe_heuristic",
                diagnostics=diagnostics,
                selector_count=len(selectors),
                variable_count=len(variables),
                import_count=len(imports),
            ),
        )

    @staticmethod
    def _position(text: str, offset: int) -> tuple[int, int]:
        line = text.count("\n", 0, offset) + 1
        previous_newline = text.rfind("\n", 0, offset)
        return line, offset - previous_newline
