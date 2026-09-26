"""Pure, closed definitions for bounded one-shot intermediate BPMN catches.

Compiler hook: ``parse_event_definition(element, definitions)`` returns the
``bpmn_wait`` node metadata. Runtime/plan admission rechecks it with
``validate_event_definition``. No schema resolution, network access or execution
is performed here. Message schemas are pinned by their canonical digest.
"""

from __future__ import annotations

import copy
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from agent.services.workflow_runtime._serialization import sha256_json

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
ANANTA_NS = "https://ananta.local/bpmn"
MAX_WAIT_SECONDS = 86400.0
MAX_SCHEMA_FIELDS = 32
_DURATION = re.compile(
    r"P(?:(?P<days>[0-9]{1,6})D)?(?:T(?:(?P<hours>[0-9]{1,6})H)?"
    r"(?:(?P<minutes>[0-9]{1,6})M)?(?:(?P<seconds>[0-9]{1,6}(?:\.[0-9]{1,6})?)S)?)?"
)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")


class BpmnEventDefinitionError(ValueError):
    """A bounded event definition was rejected before runtime admission."""


def _reject(reason: str) -> None:
    raise BpmnEventDefinitionError("bpmn_wait_" + reason)


def _number(value: Any, name: str, *, zero: bool = False) -> float:
    if type(value) not in {int, float}:
        _reject(name + "_invalid")
    try:
        result = float(value)
    except OverflowError:
        _reject(name + "_invalid")
    if not math.isfinite(result) or result < 0 or (not zero and result == 0):
        _reject(name + "_invalid")
    return result


def parse_timer_duration(value: str) -> float:
    """Fixed ISO day/hour/minute/second durations only; no calendar or cycles."""
    match = _DURATION.fullmatch(value) if type(value) is str and len(value) <= 80 else None
    if match is None or not any(match.groupdict().values()) or value.endswith("T"):
        _reject("duration_invalid")
    result = sum(
        float(match[name] or 0) * unit
        for name, unit in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1))
    )
    if result >= MAX_WAIT_SECONDS:
        _reject("duration_exceeds_limit")
    return result


def parse_timer_date(value: str) -> float:
    if type(value) is not str or len(value) > 80 or not _DATE.fullmatch(value):
        _reject("timezone_date_required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # datetime normalizes invalid offset minutes, so check those explicitly.
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError
        return _number(parsed.timestamp(), "due_at")
    except (ValueError, OverflowError):
        _reject("timezone_date_required")


def validate_payload_schema(schema: Any) -> dict[str, Any]:
    """Small JSON Schema subset: closed objects containing scalar fields only."""
    if type(schema) is not dict or set(schema) != {"type", "properties", "required", "additionalProperties"}:
        _reject("schema_invalid")
    properties, required = schema["properties"], schema["required"]
    if schema["type"] != "object" or schema["additionalProperties"] is not False:
        _reject("schema_closed_object_required")
    if type(properties) is not dict or len(properties) > MAX_SCHEMA_FIELDS or type(required) is not list:
        _reject("schema_fields_invalid")
    if any(type(key) is not str or not _NAME.fullmatch(key) for key in properties):
        _reject("schema_field_name_invalid")
    if any(type(key) is not str or key not in properties for key in required) or len(set(required)) != len(required):
        _reject("schema_required_invalid")
    for spec in properties.values():
        if type(spec) is not dict or type(spec.get("type")) is not str:
            _reject("schema_scalar_required")
        kind = spec["type"]
        if kind not in {"string", "integer", "number", "boolean", "null"}:
            _reject("schema_scalar_required")
        allowed = {"type", "maxLength"} if kind == "string" else {"type"}
        if set(spec) - allowed:
            _reject("schema_keyword_unsupported")
        if kind == "string" and (type(spec.get("maxLength")) is not int or not 1 <= spec["maxLength"] <= 4096):
            _reject("schema_string_bound_required")
    return copy.deepcopy(schema)


def validate_message_payload(schema: dict[str, Any], payload: Any) -> None:
    schema = validate_payload_schema(schema)
    if type(payload) is not dict or set(payload) - set(schema["properties"]) or set(schema["required"]) - set(payload):
        _reject("payload_schema_mismatch")
    kinds = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "null": (type(None),)}
    for name, value in payload.items():
        spec = schema["properties"][name]
        kind = spec["type"]
        if type(value) not in kinds[kind]:
            _reject("payload_schema_mismatch")
        if kind == "string" and len(value) > spec["maxLength"]:
            _reject("payload_schema_mismatch")
        if kind == "number":
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                _reject("payload_schema_mismatch")


def validate_event_definition(raw: Any) -> dict[str, Any]:
    if type(raw) is not dict or type(raw.get("kind")) is not str:
        _reject("definition_invalid")
    timeout = _number(raw.get("timeout_seconds"), "timeout")
    if timeout > MAX_WAIT_SECONDS:
        _reject("timeout_exceeds_limit")
    if raw["kind"] == "timer":
        if set(raw) != {"kind", "timer", "timeout_seconds"} or type(raw["timer"]) is not dict:
            _reject("timer_form_invalid")
        timer = raw["timer"]
        if set(timer) == {"duration_seconds"}:
            if _number(timer["duration_seconds"], "duration", zero=True) >= timeout:
                _reject("timer_exceeds_deadline")
        elif set(timer) == {"due_at"}:
            _number(timer["due_at"], "due_at")
        else:
            _reject("timer_form_invalid")
    elif raw["kind"] == "message":
        if set(raw) != {"kind", "name", "correlation_key", "schema_id", "payload_schema", "timeout_seconds"}:
            _reject("message_form_invalid")
        for field in ("name", "correlation_key"):
            if type(raw[field]) is not str or not _NAME.fullmatch(raw[field]):
                _reject(field + "_invalid")
        schema = validate_payload_schema(raw["payload_schema"])
        if raw["schema_id"] != sha256_json(schema):
            _reject("schema_binding_mismatch")
    else:
        _reject("kind_unsupported")
    return copy.deepcopy(raw)


def validate_event_node(node) -> tuple[str, ...]:
    """Neutral plan admission; event semantics cannot be hidden on task nodes."""
    if node.node_type != "bpmn_wait":
        return ("bpmn_wait_on_task",) if "bpmn_wait" in node.metadata else ()
    if (
        node.allowed_tools
        or node.input_artifacts
        or node.output_artifacts
        or node.gate_id
        or node.side_effect_class != "none"
    ):
        return ("bpmn_wait_worker_effects_forbidden",)
    try:
        validate_event_definition(node.metadata.get("bpmn_wait"))
    except BpmnEventDefinitionError as exc:
        return (str(exc),)
    return ()


def _metadata(element: ET.Element) -> dict:
    from agent.visual_process.bpmn_execution_support import parse_ananta_metadata

    extensions = element.findall(f"{{{BPMN_NS}}}extensionElements")
    if not extensions:
        return {}
    if len(extensions) != 1 or extensions[0].attrib or len(extensions[0]) != 1:
        _reject("extension_unsupported")
    child = extensions[0][0]
    if child.tag != f"{{{ANANTA_NS}}}metadata" or child.attrib or len(child):
        _reject("extension_unsupported")
    return parse_ananta_metadata(child.text or "{}")


def parse_event_definition(element: ET.Element, definitions: ET.Element | None = None) -> dict[str, Any]:
    """Parse an intermediate catch from already size/depth-bounded BPMN XML.

    The Ananta metadata's ``bpmn_wait`` object accepts ``timeout_seconds`` and,
    for messages, ``correlation_key`` and ``payload_schema``. The message name
    must resolve from an explicit top-level BPMN ``message`` declaration.
    """
    if element.tag != f"{{{BPMN_NS}}}intermediateCatchEvent":
        _reject("intermediate_catch_required")
    if set(element.attrib) - {"id", "name"} or not element.get("id"):
        _reject("event_attribute_unsupported")
    events = [
        child
        for child in element
        if child.tag
        not in {f"{{{BPMN_NS}}}{name}" for name in ("incoming", "outgoing", "documentation", "extensionElements")}
    ]
    if len(events) != 1:
        _reject("single_event_definition_required")
    event = events[0]
    options = _metadata(element).get("bpmn_wait", {})
    if type(options) is not dict:
        _reject("options_invalid")
    timeout = options.get("timeout_seconds", MAX_WAIT_SECONDS)
    if event.tag == f"{{{BPMN_NS}}}timerEventDefinition":
        if set(options) - {"timeout_seconds"} or set(event.attrib) - {"id"} or len(event) != 1:
            _reject("timer_form_invalid")
        expression = event[0]
        if expression.attrib or len(expression):
            _reject("timer_expression_unsupported")
        value = (expression.text or "").strip()
        if expression.tag == f"{{{BPMN_NS}}}timeDuration":
            timer = {"duration_seconds": parse_timer_duration(value)}
        elif expression.tag == f"{{{BPMN_NS}}}timeDate":
            timer = {"due_at": parse_timer_date(value)}
        else:
            _reject("timer_form_unsupported")
        raw = {"kind": "timer", "timer": timer, "timeout_seconds": timeout}
    elif event.tag == f"{{{BPMN_NS}}}messageEventDefinition":
        if set(options) - {"timeout_seconds", "correlation_key", "payload_schema"}:
            _reject("message_form_invalid")
        if set(event.attrib) - {"id", "messageRef"} or len(event) or definitions is None:
            _reject("message_declaration_required")
        matches = [
            node for node in definitions.findall(f"{{{BPMN_NS}}}message") if node.get("id") == event.get("messageRef")
        ]
        if len(matches) != 1 or not event.get("messageRef"):
            _reject("message_declaration_required")
        declaration = matches[0]
        if set(declaration.attrib) - {"id", "name"} or len(declaration):
            _reject("message_declaration_unsupported")
        schema = validate_payload_schema(options.get("payload_schema"))
        raw = {
            "kind": "message",
            "name": declaration.get("name"),
            "correlation_key": options.get("correlation_key"),
            "payload_schema": schema,
            "schema_id": sha256_json(schema),
            "timeout_seconds": timeout,
        }
    else:
        _reject("event_kind_unsupported")
    return validate_event_definition(raw)
