"""Bounded extraction for delimited text and Jupyter notebooks."""

from __future__ import annotations

import csv
import io
import json
import re

from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    line_number,
    redact_sensitive_text,
    stats_for,
)
from rag_helper.utils.ids import safe_id


class DelimitedTextExtractor:
    SUPPORTED_EXTENSIONS = {"csv", "tsv"}

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_rows: int = 2_000,
        sample_rows: int = 100,
        max_columns: int = 500,
        max_cell_chars: int = 16_000,
    ) -> None:
        if min(max_rows, sample_rows, max_columns, max_cell_chars) <= 0:
            raise ValueError("tabular_limits_must_be_positive")
        self.embedding_text_mode = embedding_text_mode
        self.max_rows = max_rows
        self.sample_rows = min(sample_rows, max_rows)
        self.max_columns = max_columns
        self.max_cell_chars = max_cell_chars

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported_delimited_extension:{ext}")
        factory = StructuredRecordFactory(rel_path, ext, self.embedding_text_mode)
        delimiter = "\t" if ext == "tsv" else ","
        details: list[dict] = []
        diagnostics: list[dict] = []
        rows: list[list[str]] = []
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        try:
            for row_number, row in enumerate(reader, start=1):
                if row_number > self.max_rows + 1:
                    diagnostic = factory.diagnostic(
                        "tabular_row_limit_reached",
                        f"Only the first {self.max_rows} data rows were sampled.",
                        line=max(1, getattr(reader, "line_num", row_number)),
                        fallback="bounded_schema_sample",
                    )
                    diagnostics.append(diagnostic)
                    break
                if len(row) > self.max_columns:
                    diagnostic = factory.diagnostic(
                        "tabular_column_limit_exceeded",
                        f"Row exceeds the configured {self.max_columns} column limit.",
                        line=max(1, getattr(reader, "line_num", row_number)),
                        severity="error",
                        fallback="header_only_index",
                    )
                    diagnostics.append(diagnostic)
                    break
                if any(len(cell) > self.max_cell_chars for cell in row):
                    diagnostic = factory.diagnostic(
                        "tabular_cell_limit_exceeded",
                        f"A cell exceeds the configured {self.max_cell_chars} character limit.",
                        line=max(1, getattr(reader, "line_num", row_number)),
                        severity="error",
                        fallback="schema_only_index",
                    )
                    diagnostics.append(diagnostic)
                    row = [cell[: self.max_cell_chars] for cell in row]
                rows.append(row)
        except csv.Error as exc:
            diagnostic = factory.diagnostic(
                "tabular_parse_error",
                f"Delimited text parsing failed: {str(exc)[:120]}",
                line=max(1, getattr(reader, "line_num", 1)),
                severity="error",
                fallback="partial_schema_index",
            )
            diagnostics.append(diagnostic)

        headers = rows[0] if rows else []
        normalized_headers: list[str] = []
        seen: dict[str, int] = {}
        for index, raw_header in enumerate(headers, start=1):
            header = raw_header.strip() or f"column_{index}"
            seen[header] = seen.get(header, 0) + 1
            normalized_headers.append(header if seen[header] == 1 else f"{header}_{seen[header]}")
        samples = rows[1 : self.sample_rows + 1]
        ragged_count = sum(len(row) != len(headers) for row in rows[1:]) if headers else 0
        if ragged_count:
            diagnostics.append(
                factory.diagnostic(
                    "tabular_ragged_rows",
                    f"{ragged_count} sampled rows have a different width than the header.",
                    line=2,
                    fallback="nullable_schema_sample",
                )
            )
        for ordinal, header in enumerate(normalized_headers, start=1):
            values = [row[ordinal - 1] for row in samples if ordinal - 1 < len(row)]
            inferred = self._infer_type(values)
            non_empty = sum(bool(value.strip()) for value in values)
            details.append(
                factory.symbol(
                    kind=f"{ext}_column",
                    name=header,
                    line=1,
                    column=ordinal,
                    ordinal=ordinal,
                    inferred_type=inferred,
                    sampled_value_count=len(values),
                    non_empty_sample_count=non_empty,
                    values_included=False,
                )
            )
        details.extend(diagnostics)
        index = [
            factory.file_record(
                summary={
                    "column_count": len(normalized_headers),
                    "sampled_row_count": max(0, len(rows) - 1),
                    "ragged_row_count": ragged_count,
                    "values_included": False,
                    "diagnostic_count": len(diagnostics),
                },
                labels=normalized_headers,
                parser_mode="bounded_csv_reader",
                confidence=0.85,
            )
        ]
        return (
            index,
            details,
            [],
            stats_for(
                ext,
                rel_path,
                index,
                details,
                [],
                parser_mode="bounded_csv_reader",
                diagnostics=diagnostics,
                column_count=len(normalized_headers),
                sampled_row_count=max(0, len(rows) - 1),
                ragged_row_count=ragged_count,
            ),
        )

    @staticmethod
    def _infer_type(values: list[str]) -> str:
        non_empty = [value.strip() for value in values if value.strip()]
        if not non_empty:
            return "unknown"
        candidates = {"boolean", "integer", "number", "date"}
        for value in non_empty:
            lowered = value.lower()
            if lowered not in {"true", "false", "yes", "no"}:
                candidates.discard("boolean")
            if not re.fullmatch(r"[-+]?\d+", value):
                candidates.discard("integer")
            if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value):
                candidates.discard("number")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][^\s]+)?", value):
                candidates.discard("date")
        for candidate in ("boolean", "integer", "number", "date"):
            if candidate in candidates:
                return candidate
        return "string"


class NotebookExtractor:
    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_cells: int = 2_000,
        max_cell_chars: int = 32_000,
    ) -> None:
        if max_cells <= 0 or max_cell_chars <= 0:
            raise ValueError("notebook_limits_must_be_positive")
        self.embedding_text_mode = embedding_text_mode
        self.max_cells = max_cells
        self.max_cell_chars = max_cell_chars

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "ipynb", self.embedding_text_mode)
        try:
            notebook = json.loads(text)
        except json.JSONDecodeError as exc:
            diagnostic = factory.diagnostic(
                "notebook_json_parse_error",
                "Notebook JSON could not be parsed.",
                line=exc.lineno,
                column=exc.colno,
                severity="error",
                fallback="text_index",
            )
            index = [
                factory.file_record(
                    summary={"cell_count": 0, "diagnostic_count": 1}, parser_mode="text_index", confidence=0.2
                )
            ]
            return (
                index,
                [diagnostic],
                [],
                stats_for(
                    "ipynb",
                    rel_path,
                    index,
                    [diagnostic],
                    [],
                    parser_mode="text_index",
                    diagnostics=[diagnostic],
                    cell_count=0,
                ),
            )
        if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
            diagnostic = factory.diagnostic(
                "notebook_cells_missing",
                "Notebook does not contain a cells array.",
                severity="error",
                fallback="text_index",
            )
            index = [
                factory.file_record(
                    summary={"cell_count": 0, "diagnostic_count": 1}, parser_mode="text_index", confidence=0.2
                )
            ]
            return (
                index,
                [diagnostic],
                [],
                stats_for(
                    "ipynb",
                    rel_path,
                    index,
                    [diagnostic],
                    [],
                    parser_mode="text_index",
                    diagnostics=[diagnostic],
                    cell_count=0,
                ),
            )

        cells: list = notebook["cells"]
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        markdown_count = 0
        code_count = 0
        language = self._language(notebook)
        search_cursor = 0
        for cell_index, raw_cell in enumerate(cells[: self.max_cells]):
            if not isinstance(raw_cell, dict):
                continue
            cell_type = raw_cell.get("cell_type")
            if cell_type not in {"markdown", "code", "raw"}:
                continue
            source = raw_cell.get("source", "")
            source_text = "".join(source) if isinstance(source, list) else source if isinstance(source, str) else ""
            offset = text.find('"cell_type"', search_cursor)
            if offset >= 0:
                search_cursor = offset + len('"cell_type"')
            file_line = line_number(text, max(0, offset))
            truncated = len(source_text) > self.max_cell_chars
            inert_content, redacted_secret_count = redact_sensitive_text(
                source_text[: self.max_cell_chars]
            )
            cell_id = f"ipynb_cell:{safe_id(rel_path, str(cell_index))}"
            headings = re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", inert_content) if cell_type == "markdown" else []
            symbols = self._code_symbols(inert_content, language) if cell_type == "code" else []
            if cell_type == "markdown":
                markdown_count += 1
            elif cell_type == "code":
                code_count += 1
            details.append(
                {
                    "kind": f"ipynb_{cell_type}_cell",
                    "file": rel_path,
                    "id": cell_id,
                    "parent_id": factory.file_id,
                    "name": f"cell_{cell_index}",
                    "line": file_line,
                    "cell_index": cell_index,
                    "cell_type": cell_type,
                    "language": language if cell_type == "code" else "markdown" if cell_type == "markdown" else None,
                    "source": inert_content,
                    "source_truncated": truncated,
                    "source_redacted": redacted_secret_count > 0,
                    "headings": headings[:100],
                    "symbols": symbols[:200],
                    "outputs_included": False,
                    "attachments_included": False,
                    "executed": False,
                }
            )
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind="ipynb_file",
                    source_name=rel_path,
                    relation="contains_cell",
                    target=f"cell_{cell_index}",
                    target_resolved=cell_id,
                    line=file_line,
                )
            )
            if redacted_secret_count:
                diagnostic = factory.diagnostic(
                    "notebook_secret_value_redacted",
                    f"Redacted {redacted_secret_count} suspected secret value(s) in cell {cell_index}.",
                    line=file_line,
                    severity="security",
                    fallback="redacted_inert_source",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
            if truncated:
                diagnostic = factory.diagnostic(
                    "notebook_cell_truncated",
                    f"Cell {cell_index} exceeds {self.max_cell_chars} source characters.",
                    line=file_line,
                    fallback="truncated_inert_source",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
        if len(cells) > self.max_cells:
            diagnostic = factory.diagnostic(
                "notebook_cell_limit_reached",
                f"Only the first {self.max_cells} cells were indexed.",
                fallback="partial_notebook_index",
            )
            diagnostics.append(diagnostic)
            details.append(diagnostic)

        index = [
            factory.file_record(
                summary={
                    "cell_count": min(len(cells), self.max_cells),
                    "markdown_cell_count": markdown_count,
                    "code_cell_count": code_count,
                    "language": language,
                    "outputs_included": False,
                    "attachments_included": False,
                    "diagnostic_count": len(diagnostics),
                },
                labels=[item for record in details for item in record.get("headings", [])],
                parser_mode="stdlib_json",
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "ipynb",
                rel_path,
                index,
                details,
                relations,
                parser_mode="stdlib_json",
                diagnostics=diagnostics,
                cell_count=min(len(cells), self.max_cells),
                markdown_cell_count=markdown_count,
                code_cell_count=code_count,
                language=language,
                outputs_included=False,
            ),
        )

    @staticmethod
    def _language(notebook: dict) -> str | None:
        metadata = notebook.get("metadata")
        if not isinstance(metadata, dict):
            return None
        language_info = metadata.get("language_info")
        if isinstance(language_info, dict) and isinstance(language_info.get("name"), str):
            return language_info["name"]
        kernelspec = metadata.get("kernelspec")
        if isinstance(kernelspec, dict) and isinstance(kernelspec.get("language"), str):
            return kernelspec["language"]
        return None

    @staticmethod
    def _code_symbols(source: str, language: str | None) -> list[dict]:
        patterns = {
            "python": re.compile(r"(?m)^\s*(?:async\s+)?(def|class)\s+([A-Za-z_][\w]*)"),
            "javascript": re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(function|class)\s+([A-Za-z_$][\w$]*)"),
            "typescript": re.compile(
                r"(?m)^\s*(?:export\s+)?(?:async\s+)?(function|class|interface|type)\s+([A-Za-z_$][\w$]*)"
            ),
            "r": re.compile(r"(?m)^\s*([A-Za-z.][\w.]*)\s*<-\s*function\b"),
        }
        pattern = patterns.get((language or "").lower())
        if not pattern:
            return []
        result: list[dict] = []
        for match in pattern.finditer(source):
            if len(match.groups()) == 2:
                kind, name = match.groups()
            else:
                kind, name = "function", match.group(1)
            result.append({"kind": kind, "name": name, "source_line": line_number(source, match.start())})
        return result
