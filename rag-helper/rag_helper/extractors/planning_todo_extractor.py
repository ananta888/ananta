"""Deterministic structural extraction for tracked planning TODO JSON files."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Iterable

from rag_helper.extractors.structured_support import (
    StructuredRecordFactory,
    line_number,
    normalize_extraction_records,
    redact_sensitive_text,
    stats_for,
)

_DECISION_KEYS = (
    "architecture_decisions",
    "core_decisions",
    "decision_log",
    "design_decisions",
)
_TOP_LEVEL_ACCEPTANCE_KEYS = (
    "acceptance_criteria",
    "definition_of_done",
    "global_acceptance_criteria",
)


class PlanningTodoExtractor:
    """Emit task, decision and acceptance records without retaining raw JSON.

    Detection is conservative: a generic JSON document containing a ``tasks``
    property is not automatically claimed unless it also has planning metadata
    or follows the repository's ``todo.*.json`` naming convention.
    """

    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 20_000) -> None:
        if max_records <= 0:
            raise ValueError("planning_todo_max_records_must_be_positive")
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    @staticmethod
    def supports(rel_path: str, value: object) -> bool:
        if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
            return False
        basename = PurePosixPath(rel_path.replace("\\", "/")).name.lower()
        has_planning_identity = isinstance(value.get("track"), str) and (
            isinstance(value.get("goal"), (str, dict))
            or isinstance(value.get("milestones"), list)
        )
        schema = str(value.get("$schema") or "").lower()
        return basename.startswith("todo.") or "todo.track.schema" in schema or has_planning_identity

    def parse(self, rel_path: str, text: str, value: dict | None = None):
        payload = value if value is not None else json.loads(text)
        if not self.supports(rel_path, payload):
            raise ValueError("not_planning_todo_json")

        factory = StructuredRecordFactory(rel_path, "todo", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        spans = self._object_spans(text)
        task_count = 0
        decision_count = 0
        acceptance_count = 0
        tasks_offset = max(0, text.find('"tasks"'))
        limit_reached = False

        def reserve(record_count: int = 2) -> bool:
            nonlocal limit_reached
            if len(details) + len(relations) + record_count <= self.max_records:
                return True
            limit_reached = True
            if len(details) + len(relations) < self.max_records:
                diagnostic = self._record_limit_diagnostic(factory)
                diagnostics.append(diagnostic)
                details.append(diagnostic)
            return False

        for task_ordinal, task in enumerate(payload.get("tasks", []), start=1):
            if not isinstance(task, dict):
                continue
            if not reserve():
                break
            task_id = str(task.get("id") or f"task-{task_ordinal}")
            title = self._safe_text(task.get("title") or task.get("summary") or task_id)
            start_offset, end_offset = self._span_for_task_id(
                text, task_id, spans, lower_bound=tasks_offset
            )
            task_line_start = line_number(text, start_offset)
            task_line_end = line_number(text, max(start_offset, end_offset - 1))
            task_record = factory.symbol(
                kind="todo_task",
                name=title[:240],
                line=task_line_start,
                end_line=task_line_end,
                ordinal=task_ordinal,
                task_id=task_id,
                status=self._optional_scalar(task.get("status")),
                priority=self._optional_scalar(task.get("priority")),
                risk=self._optional_scalar(task.get("risk")),
                depends_on=self._string_list(task.get("depends_on") or task.get("dependencies")),
            )
            details.append(task_record)
            task_count += 1
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind="todo_file",
                    source_name=rel_path,
                    relation="contains_task",
                    target=task_id,
                    target_resolved=task_record["id"],
                    line=task_line_start,
                )
            )

            criteria = task.get("acceptance_criteria")
            if isinstance(criteria, list):
                for criterion_ordinal, criterion in enumerate(criteria, start=1):
                    criterion_text = self._criterion_text(criterion)
                    if not criterion_text:
                        continue
                    if not reserve():
                        break
                    acceptance_count += 1
                    criterion_start, criterion_end = self._span_for_token(
                        text,
                        criterion_text,
                        spans,
                        lower_bound=start_offset,
                        upper_bound=end_offset,
                        enclosing_object=False,
                    )
                    acceptance = factory.symbol(
                        kind="todo_acceptance",
                        name=self._safe_text(criterion_text)[:500],
                        line=line_number(text, criterion_start),
                        end_line=line_number(text, max(criterion_start, criterion_end - 1)),
                        parent_id=task_record["id"],
                        ordinal=criterion_ordinal,
                        task_id=task_id,
                        criterion_index=criterion_ordinal,
                    )
                    details.append(acceptance)
                    relations.append(
                        factory.relation(
                            source_id=task_record["id"],
                            source_kind="todo_task",
                            source_name=task_id,
                            relation="has_acceptance_criterion",
                            target=f"criterion_{criterion_ordinal}",
                            target_resolved=acceptance["id"],
                            line=acceptance["line"],
                        )
                    )
            if limit_reached:
                break

        for decision_key in (() if limit_reached else _DECISION_KEYS):
            values = payload.get(decision_key)
            if not isinstance(values, list):
                continue
            for ordinal, decision in enumerate(values, start=1):
                decision_text = self._decision_text(decision)
                if not decision_text:
                    continue
                if not reserve():
                    break
                decision_count += 1
                start_offset, end_offset = self._span_for_token(
                    text, decision_text, spans, enclosing_object=False
                )
                record = factory.symbol(
                    kind="todo_decision",
                    name=self._safe_text(decision_text)[:500],
                    line=line_number(text, start_offset),
                    end_line=line_number(text, max(start_offset, end_offset - 1)),
                    ordinal=decision_count,
                    decision_group=decision_key,
                )
                details.append(record)
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="todo_file",
                        source_name=rel_path,
                        relation="contains_decision",
                        target=f"{decision_key}_{ordinal}",
                        target_resolved=record["id"],
                        line=record["line"],
                    )
                )
            if limit_reached:
                break

        for acceptance_key in (() if limit_reached else _TOP_LEVEL_ACCEPTANCE_KEYS):
            values = payload.get(acceptance_key)
            if not isinstance(values, list):
                continue
            for ordinal, criterion in enumerate(values, start=1):
                criterion_text = self._criterion_text(criterion)
                if not criterion_text:
                    continue
                if not reserve():
                    break
                acceptance_count += 1
                start_offset, end_offset = self._span_for_token(
                    text, criterion_text, spans, enclosing_object=False
                )
                record = factory.symbol(
                    kind="todo_acceptance",
                    name=self._safe_text(criterion_text)[:500],
                    line=line_number(text, start_offset),
                    end_line=line_number(text, max(start_offset, end_offset - 1)),
                    ordinal=acceptance_count,
                    acceptance_group=acceptance_key,
                )
                details.append(record)
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="todo_file",
                        source_name=rel_path,
                        relation="has_acceptance_criterion",
                        target=f"{acceptance_key}_{ordinal}",
                        target_resolved=record["id"],
                        line=record["line"],
                    )
                )
            if limit_reached:
                break

        index = [
            factory.file_record(
                summary={
                    "task_count": task_count,
                    "decision_count": decision_count,
                    "acceptance_count": acceptance_count,
                    "diagnostic_count": len(diagnostics),
                },
                labels=[
                    self._safe_text(payload.get("track") or payload.get("title") or rel_path)[:240]
                ],
                parser_mode="stdlib_json_planning_todo",
                confidence=1.0,
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
                "todo",
                rel_path,
                index,
                details,
                relations,
                parser_mode="stdlib_json_planning_todo",
                diagnostics=diagnostics,
                task_count=task_count,
                decision_count=decision_count,
                acceptance_count=acceptance_count,
            ),
        )

    def _record_limit_diagnostic(self, factory: StructuredRecordFactory) -> dict:
        return factory.diagnostic(
            "planning_todo_record_limit_reached",
            f"Planning TODO extraction reached the configured {self.max_records} record limit.",
            fallback="partial_structured_index",
        )

    @staticmethod
    def _safe_text(value: object) -> str:
        redacted, _ = redact_sensitive_text(str(value or ""))
        return " ".join(redacted.split())

    @staticmethod
    def _optional_scalar(value: object) -> str | int | float | bool | None:
        return value if isinstance(value, (str, int, float, bool)) else None

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int, float))]

    @staticmethod
    def _criterion_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("criterion", "text", "title", "description"):
                if isinstance(value.get(key), str):
                    return value[key]
        return ""

    @staticmethod
    def _decision_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("decision", "title", "summary", "description", "id"):
                if isinstance(value.get(key), str):
                    return value[key]
        return ""

    @staticmethod
    def _object_spans(text: str) -> list[tuple[int, int]]:
        stack: list[int] = []
        spans: list[tuple[int, int]] = []
        in_string = False
        escaped = False
        for offset, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                stack.append(offset)
            elif char == "}" and stack:
                spans.append((stack.pop(), offset + 1))
        return spans

    @staticmethod
    def _span_for_token(
        text: str,
        token: str,
        object_spans: Iterable[tuple[int, int]],
        *,
        lower_bound: int = 0,
        upper_bound: int | None = None,
        enclosing_object: bool = True,
    ) -> tuple[int, int]:
        upper = len(text) if upper_bound is None else max(lower_bound, upper_bound)
        candidates = (
            json.dumps(str(token), ensure_ascii=False),
            json.dumps(str(token), ensure_ascii=True),
        )
        offset = -1
        token_length = 0
        for encoded in dict.fromkeys(candidates):
            found = text.find(encoded, lower_bound, upper)
            if found >= 0 and (offset < 0 or found < offset):
                offset = found
                token_length = len(encoded)
        if offset < 0:
            return lower_bound, max(lower_bound + 1, upper)
        if not enclosing_object:
            return offset, offset + token_length
        containing = [
            span
            for span in object_spans
            if span[0] <= offset and span[1] >= offset + token_length
            and span[0] >= lower_bound and span[1] <= upper
        ]
        if containing:
            return min(containing, key=lambda item: item[1] - item[0])
        return offset, offset + token_length

    @classmethod
    def _span_for_task_id(
        cls,
        text: str,
        task_id: str,
        object_spans: Iterable[tuple[int, int]],
        *,
        lower_bound: int = 0,
    ) -> tuple[int, int]:
        encoded_values = tuple(
            dict.fromkeys(
                (
                    json.dumps(str(task_id), ensure_ascii=False),
                    json.dumps(str(task_id), ensure_ascii=True),
                )
            )
        )
        value_offset = -1
        for encoded in encoded_values:
            match = re.search(
                rf'"id"\s*:\s*{re.escape(encoded)}',
                text[lower_bound:],
            )
            if match is not None:
                value_offset = lower_bound + match.start()
                break
        if value_offset < 0:
            return cls._span_for_token(
                text, task_id, object_spans, lower_bound=lower_bound
            )
        containing = [
            span for span in object_spans if span[0] <= value_offset < span[1]
        ]
        if containing:
            return min(containing, key=lambda item: item[1] - item[0])
        return value_offset, value_offset + 1
