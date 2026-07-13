from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent.services.structured_output_service import (
    StructuredOutputService,
    StructuredOutputValidationError,
)

SCHEMA = {
    "$id": "test.output.v1",
    "type": "object",
    "required": ["name", "count"],
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


class OutputModel(BaseModel):
    name: str
    count: int


def test_valid_json_is_returned_with_hash_only_audit() -> None:
    result = StructuredOutputService().validate_json('{"name":"ok","count":2}', SCHEMA)

    assert result.valid is True
    assert result.value == {"name": "ok", "count": 2}
    assert len(result.content_hash) == 64
    assert all("ok" not in str(event) for event in result.audit_events)


def test_schema_rejects_extra_and_wrong_fields() -> None:
    result = StructuredOutputService().validate_json(
        '{"name":"bad","count":"2","extra":true}',
        SCHEMA,
    )

    assert result.valid is False
    assert result.value is None
    codes = {issue.reason_code for issue in result.issues}
    assert "json_schema_type" in codes
    assert "json_schema_additionalProperties" in codes


def test_format_repair_is_bounded_to_one_json_fence_removal() -> None:
    fence = chr(96) * 3
    service = StructuredOutputService(max_repair_attempts=1)
    repaired = service.validate_json(
        f'{fence}json\n{{"name":"ok","count":1}}\n{fence}',
        SCHEMA,
        allow_format_repair=True,
    )
    invented = service.validate_json(
        "name=ok,count=1",
        SCHEMA,
        allow_format_repair=True,
    )

    assert repaired.valid is True
    assert repaired.repaired is True
    assert repaired.repair_attempts == 1
    assert invented.valid is False
    assert invented.repair_attempts == 1


def test_require_json_fails_closed() -> None:
    with pytest.raises(StructuredOutputValidationError):
        StructuredOutputService().require_json('{"name":"missing-count"}', SCHEMA)


def test_pydantic_validation_returns_typed_model() -> None:
    result = StructuredOutputService().validate_model(
        {"name": "typed", "count": 3},
        OutputModel,
    )

    assert result.valid is True
    assert isinstance(result.model, OutputModel)
    assert result.value == {"name": "typed", "count": 3}


@pytest.mark.parametrize(
    ("schema", "reason"),
    [
        ({"$ref": "https://untrusted.example/schema.json"}, "json_schema_external_reference_denied"),
        ({"type": "not-a-json-schema-type"}, "json_schema_invalid"),
        ({"type": "string", "pattern": "x" * 513}, "json_schema_pattern_invalid"),
    ],
)
def test_schema_injection_and_remote_resolution_fail_closed(schema: dict, reason: str) -> None:
    result = StructuredOutputService().validate_json("{}", schema)

    assert result.valid is False
    assert result.issues[0].reason_code == reason


def test_content_schema_depth_and_node_limits_are_bounded() -> None:
    service = StructuredOutputService(
        maximum_content_bytes=1024,
        maximum_schema_depth=4,
        maximum_schema_nodes=8,
    )
    nested: dict = {"type": "string"}
    for _ in range(20):
        nested = {"allOf": [nested]}

    oversized = service.validate_json('"' + "x" * 2000 + '"', {"type": "string"})
    too_deep = service.validate_json("{}", nested)
    too_wide = service.validate_json(
        "{}",
        {"type": "object", "properties": {str(index): {"type": "string"} for index in range(20)}},
    )

    assert oversized.issues[0].reason_code == "structured_output_too_large"
    assert too_deep.issues[0].reason_code in {
        "json_schema_depth_exceeded",
        "json_schema_node_limit_exceeded",
    }
    assert too_wide.issues[0].reason_code == "json_schema_node_limit_exceeded"


def test_only_schema_valid_values_pass_small_property_corpus() -> None:
    service = StructuredOutputService()
    candidates = [
        {"name": name, "count": count, **extra}
        for name in ("", "ok", 1, None)
        for count in (-1, 0, 1, "1", None)
        for extra in ({}, {"extra": True})
    ]
    for candidate in candidates:
        result = service.validate_json(candidate, SCHEMA)
        expected = (
            isinstance(candidate["name"], str)
            and isinstance(candidate["count"], int)
            and not isinstance(candidate["count"], bool)
            and candidate["count"] >= 0
            and "extra" not in candidate
        )
        assert result.valid is expected
