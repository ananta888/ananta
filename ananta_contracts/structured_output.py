from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ValidationError

TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(frozen=True)
class StructuredOutputIssue:
    path: str
    reason_code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "reason_code": self.reason_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class StructuredOutputResult(Generic[TModel]):
    valid: bool
    value: Any = None
    model: TModel | None = None
    issues: tuple[StructuredOutputIssue, ...] = ()
    repaired: bool = False
    repair_attempts: int = 0
    content_hash: str = ""
    audit_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self, *, include_value: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "ananta.structured_output_result.v1",
            "valid": self.valid,
            "issues": [issue.as_dict() for issue in self.issues],
            "repaired": self.repaired,
            "repair_attempts": self.repair_attempts,
            "content_hash": self.content_hash,
            "audit_events": [dict(item) for item in self.audit_events],
        }
        if include_value:
            payload["value"] = self.value
        return payload


class StructuredOutputValidationError(ValueError):
    def __init__(self, result: StructuredOutputResult[Any]) -> None:
        self.result = result
        reason = result.issues[0].reason_code if result.issues else "structured_output_invalid"
        super().__init__(reason)


class StructuredOutputService:
    """Strict, provider-neutral structured-output parsing and validation.

    Format repair is deliberately conservative: it may remove one Markdown JSON
    fence, but it never invents fields or relaxes the caller-provided schema.
    """

    def __init__(
        self,
        *,
        max_repair_attempts: int = 1,
        maximum_content_bytes: int = 1_048_576,
        maximum_schema_bytes: int = 131_072,
        maximum_schema_depth: int = 32,
        maximum_schema_nodes: int = 4096,
    ) -> None:
        self.max_repair_attempts = max(0, min(1, int(max_repair_attempts)))
        self.maximum_content_bytes = max(1_024, min(int(maximum_content_bytes), 16_777_216))
        self.maximum_schema_bytes = max(1_024, min(int(maximum_schema_bytes), 1_048_576))
        self.maximum_schema_depth = max(1, min(int(maximum_schema_depth), 64))
        self.maximum_schema_nodes = max(1, min(int(maximum_schema_nodes), 65_536))

    @staticmethod
    def _content_hash(raw: Any) -> str:
        if isinstance(raw, str):
            rendered = raw
        else:
            rendered = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse(raw: Any) -> tuple[Any, StructuredOutputIssue | None]:
        if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
            return raw, None
        if not isinstance(raw, str):
            return None, StructuredOutputIssue("$", "unsupported_output_type", type(raw).__name__)
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as exc:
            return None, StructuredOutputIssue(
                "$",
                "invalid_json",
                f"line={exc.lineno},column={exc.colno}:{exc.msg}",
            )

    @staticmethod
    def _strip_json_fence(raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        text = raw.strip()
        fence = chr(96) * 3
        if not text.startswith(fence) or not text.endswith(fence):
            return raw
        first_newline = text.find("\n")
        if first_newline < 0:
            return raw
        return text[first_newline + 1 : -3].strip()

    @staticmethod
    def _schema_issues(value: Any, schema: dict[str, Any]) -> tuple[StructuredOutputIssue, ...]:
        validator = Draft202012Validator(dict(schema or {}))
        issues: list[StructuredOutputIssue] = []
        for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
            path = "/".join(str(item) for item in error.absolute_path) or "$"
            issues.append(
                StructuredOutputIssue(
                    path=path,
                    reason_code=f"json_schema_{error.validator}",
                    message=error.message,
                )
            )
        return tuple(issues)

    def _schema_safety_issues(self, schema: Any) -> tuple[StructuredOutputIssue, ...]:
        if not isinstance(schema, dict):
            return (StructuredOutputIssue("$", "json_schema_mapping_required", "schema must be an object"),)
        try:
            rendered = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError):
            return (StructuredOutputIssue("$", "json_schema_not_json", "schema must be JSON serializable"),)
        if len(rendered.encode("utf-8")) > self.maximum_schema_bytes:
            return (StructuredOutputIssue("$", "json_schema_too_large", "schema exceeds its byte limit"),)

        visited = 0
        stack: list[tuple[Any, int, str]] = [(schema, 0, "$")]
        while stack:
            value, depth, path = stack.pop()
            visited += 1
            if visited > self.maximum_schema_nodes:
                return (
                    StructuredOutputIssue(path, "json_schema_node_limit_exceeded", "schema is too complex"),
                )
            if depth > self.maximum_schema_depth:
                return (
                    StructuredOutputIssue(path, "json_schema_depth_exceeded", "schema is too deeply nested"),
                )
            if isinstance(value, dict):
                for key, item in value.items():
                    item_path = f"{path}/{key}"
                    if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                        not isinstance(item, str) or not item.startswith("#")
                    ):
                        return (
                            StructuredOutputIssue(
                                item_path,
                                "json_schema_external_reference_denied",
                                "only local schema references are allowed",
                            ),
                        )
                    if key == "pattern" and (not isinstance(item, str) or len(item) > 512):
                        return (
                            StructuredOutputIssue(
                                item_path,
                                "json_schema_pattern_invalid",
                                "pattern exceeds its safety limit",
                            ),
                        )
                    stack.append((item, depth + 1, item_path))
            elif isinstance(value, list):
                stack.extend(
                    (item, depth + 1, f"{path}/{index}") for index, item in enumerate(value)
                )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            return (
                StructuredOutputIssue("$", "json_schema_invalid", str(exc.message)),
            )
        return ()

    def validate_json(
        self,
        raw: Any,
        schema: dict[str, Any],
        *,
        allow_format_repair: bool = False,
    ) -> StructuredOutputResult[Any]:
        content_hash = self._content_hash(raw)
        schema_issues = self._schema_safety_issues(schema)
        audit: list[dict[str, Any]] = [
            {
                "event_type": "structured_output_validation_started",
                "content_hash": content_hash,
                "schema_id": str(schema.get("$id") or ""),
            }
        ]
        if schema_issues:
            audit.append(
                {
                    "event_type": "structured_output_validation_failed",
                    "content_hash": content_hash,
                    "reason_codes": [issue.reason_code for issue in schema_issues],
                }
            )
            return StructuredOutputResult(
                valid=False,
                issues=schema_issues,
                content_hash=content_hash,
                audit_events=tuple(audit),
            )
        rendered_raw = raw if isinstance(raw, str) else json.dumps(
            raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
        )
        if len(rendered_raw.encode("utf-8")) > self.maximum_content_bytes:
            issue = StructuredOutputIssue("$", "structured_output_too_large", "output exceeds its byte limit")
            audit.append(
                {
                    "event_type": "structured_output_validation_failed",
                    "content_hash": content_hash,
                    "reason_codes": [issue.reason_code],
                }
            )
            return StructuredOutputResult(
                valid=False,
                issues=(issue,),
                content_hash=content_hash,
                audit_events=tuple(audit),
            )
        parsed, parse_issue = self._parse(raw)
        repaired = False
        attempts = 0
        if parse_issue and allow_format_repair and self.max_repair_attempts:
            attempts = 1
            candidate = self._strip_json_fence(raw)
            if candidate != raw:
                parsed, parse_issue = self._parse(candidate)
                repaired = parse_issue is None
            audit.append(
                {
                    "event_type": "structured_output_repair_attempted",
                    "content_hash": content_hash,
                    "success": repaired,
                    "attempt": attempts,
                }
            )
        if parse_issue:
            audit.append(
                {
                    "event_type": "structured_output_validation_failed",
                    "content_hash": content_hash,
                    "reason_codes": [parse_issue.reason_code],
                }
            )
            return StructuredOutputResult(
                valid=False,
                issues=(parse_issue,),
                repaired=repaired,
                repair_attempts=attempts,
                content_hash=content_hash,
                audit_events=tuple(audit),
            )

        issues = self._schema_issues(parsed, schema)
        valid = not issues
        audit.append(
            {
                "event_type": (
                    "structured_output_validation_succeeded" if valid else "structured_output_validation_failed"
                ),
                "content_hash": content_hash,
                "reason_codes": [issue.reason_code for issue in issues],
            }
        )
        return StructuredOutputResult(
            valid=valid,
            value=parsed if valid else None,
            issues=issues,
            repaired=repaired,
            repair_attempts=attempts,
            content_hash=content_hash,
            audit_events=tuple(audit),
        )

    def validate_model(
        self,
        raw: Any,
        model_type: type[TModel],
        *,
        allow_format_repair: bool = False,
    ) -> StructuredOutputResult[TModel]:
        json_result = self.validate_json(
            raw,
            model_type.model_json_schema(),
            allow_format_repair=allow_format_repair,
        )
        if not json_result.valid:
            return StructuredOutputResult(
                valid=False,
                issues=json_result.issues,
                repaired=json_result.repaired,
                repair_attempts=json_result.repair_attempts,
                content_hash=json_result.content_hash,
                audit_events=json_result.audit_events,
            )
        try:
            model = model_type.model_validate(json_result.value)
        except ValidationError as exc:
            issues = tuple(
                StructuredOutputIssue(
                    path="/".join(str(item) for item in error.get("loc") or ()) or "$",
                    reason_code=f"pydantic_{error.get('type') or 'validation'}",
                    message=str(error.get("msg") or "validation failed"),
                )
                for error in exc.errors()
            )
            return StructuredOutputResult(
                valid=False,
                issues=issues,
                repaired=json_result.repaired,
                repair_attempts=json_result.repair_attempts,
                content_hash=json_result.content_hash,
                audit_events=json_result.audit_events
                + (
                    {
                        "event_type": "structured_output_model_validation_failed",
                        "content_hash": json_result.content_hash,
                        "reason_codes": [issue.reason_code for issue in issues],
                    },
                ),
            )
        return StructuredOutputResult(
            valid=True,
            value=model.model_dump(mode="json"),
            model=model,
            repaired=json_result.repaired,
            repair_attempts=json_result.repair_attempts,
            content_hash=json_result.content_hash,
            audit_events=json_result.audit_events,
        )

    def require_json(
        self,
        raw: Any,
        schema: dict[str, Any],
        *,
        allow_format_repair: bool = False,
    ) -> Any:
        result = self.validate_json(raw, schema, allow_format_repair=allow_format_repair)
        if not result.valid:
            raise StructuredOutputValidationError(result)
        return result.value


structured_output_service = StructuredOutputService()
