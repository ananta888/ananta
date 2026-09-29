"""Bounded, redacted projection of runtime events and structured payloads into the public status view."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime_status_scalars import (
    _bounded_text,
    _canonical_step_ids,
    _identity,
    _identity_syntax,
    _nonnegative_integer,
    _nonnegative_number,
    _public_event_status,
    _public_event_type,
    _public_structured_enum,
    _redacted_public_text,
    _reference_syntax,
)
from agent.services.workflow_runtime_status_vocabulary import (
    _MAX_EVENT_BYTES,
    _MAX_EVENTS,
    _MAX_STRUCTURED_BYTES,
    _MAX_STRUCTURED_DEPTH,
    _MAX_STRUCTURED_ITEMS,
    _PUBLIC_EVENT_SCHEMAS,
    _PUBLIC_STRUCTURED_ALLOWED_KEYS,
    _PUBLIC_STRUCTURED_BOOLEAN_KEYS,
    _PUBLIC_STRUCTURED_CODE_KEYS,
    _PUBLIC_STRUCTURED_FREE_TEXT_KEYS,
    _PUBLIC_STRUCTURED_IDENTITY_KEYS,
    _PUBLIC_STRUCTURED_LOCAL_ROUTE_KEYS,
    _PUBLIC_STRUCTURED_NUMBER_KEYS,
    _PUBLIC_STRUCTURED_REFERENCE_KEYS,
    _REDACTED_PUBLIC_TEXT,
    _REDACTED_REASON_CODE,
    _SAFE_TOKEN_KEYS,
    _SENSITIVE_EVENT_KEY_PARTS,
)


def _project_events(
    *,
    previous: Any,
    observed: Any,
    binding: WorkflowControlRunBinding,
) -> list[dict[str, Any]]:
    previous_events = _event_sequence(previous, field_name="previous_events")
    observed_events = _event_sequence(observed, field_name="events")
    if len(observed_events) > _MAX_EVENTS:
        raise ValueError("workflow_runtime_source_events_too_many")
    combined = (*previous_events, *observed_events)
    return [_project_event(item, binding=binding) for item in combined[-_MAX_EVENTS:]]


def _event_sequence(raw: Any, *, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if raw is None or raw == ():
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(f"workflow_runtime_{field_name}_invalid")
    if len(raw) > _MAX_EVENTS:
        raise ValueError(f"workflow_runtime_{field_name}_too_many")
    if any(not isinstance(item, Mapping) for item in raw):
        raise ValueError(f"workflow_runtime_{field_name}_invalid")
    return tuple(raw)


def _project_event(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
) -> dict[str, Any]:
    schema = raw.get("schema")
    if schema not in _PUBLIC_EVENT_SCHEMAS:
        raise ValueError("workflow_runtime_event_schema_unsupported")
    for key, expected in (
        ("tenant_id", binding.tenant_id),
        ("workflow_id", binding.workflow_id),
        ("run_id", binding.run_id),
    ):
        if key in raw and raw.get(key) != expected:
            raise ValueError(f"workflow_runtime_event_{key}_mismatch")
    _reference_syntax(raw.get("event_id"), field_name="event_id")
    projected: dict[str, Any] = {
        "schema": schema,
        "event_type": _public_event_type(
            raw.get("event_type"),
            field_name="event_type",
        ),
        "tenant_id": binding.tenant_id,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
    }
    known_step_ids = frozenset(_canonical_step_ids(binding))
    details = raw.get("details")
    step_candidates = [raw.get("step_id")]
    if isinstance(details, Mapping):
        step_candidates.append(details.get("step_id"))
    event_step_ids = {
        _identity(candidate, field_name="event_step_id") for candidate in step_candidates if candidate not in {None, ""}
    }
    if len(event_step_ids) > 1:
        raise ValueError("workflow_runtime_event_step_id_conflict")
    if event_step_ids:
        event_step_id = next(iter(event_step_ids))
        if event_step_id not in known_step_ids:
            raise ValueError("workflow_runtime_event_step_id_unknown")
        projected["step_id"] = event_step_id

    expected_correlation_id = str(binding.request.correlation_id or binding.run_id)
    expected_correlation_id = _identity(
        expected_correlation_id,
        field_name="binding_correlation_id",
    )
    if "correlation_id" in raw and raw["correlation_id"] is not None:
        source_correlation_id = _identity(
            raw["correlation_id"],
            field_name="event_correlation_id",
        )
        if source_correlation_id != expected_correlation_id:
            raise ValueError("workflow_runtime_event_correlation_id_mismatch")
    projected["correlation_id"] = expected_correlation_id
    if "status" in raw and raw["status"] is not None:
        projected["status"] = _public_event_status(
            raw["status"],
            field_name="event_status",
        )
    # Actor provenance is not established by the infrastructure event. Use a
    # Hub-owned category on every projection so retries are both redacted and
    # byte-stable when previously projected events are admitted again.
    projected["actor"] = "runtime-source"
    for key in ("causation_id", "dedupe_key"):
        if key in raw and raw[key] is not None:
            _reference_syntax(raw[key], field_name=f"event_{key}")
    for key in ("sequence", "attempt"):
        if key in raw and raw[key] is not None:
            projected[key] = _nonnegative_integer(
                raw[key],
                field_name=f"event_{key}",
            )
    for key in ("timestamp", "occurred_at"):
        if key in raw and raw[key] is not None:
            projected[key] = _nonnegative_number(
                raw[key],
                field_name=f"event_{key}",
            )
    for key in ("payload", "details"):
        if key not in raw or raw[key] is None:
            continue
        if not isinstance(raw[key], Mapping):
            raise ValueError(f"workflow_runtime_event_{key}_invalid")
        projected[key] = _bounded_redacted_json(
            raw[key],
            field_name=f"event_{key}",
            maximum_bytes=_MAX_EVENT_BYTES,
        )
    event_identity = hashlib.sha256(canonical_json(projected).encode("utf-8")).hexdigest()
    projected["event_id"] = f"wfe-runtime-{event_identity[:32]}"
    projected["causation_id"] = f"runtime-source:{binding.run_id}"
    projected["dedupe_key"] = f"runtime-event:{event_identity}"
    if len(canonical_json(projected).encode("utf-8")) > _MAX_EVENT_BYTES:
        raise ValueError("workflow_runtime_event_too_large")
    return projected


def _bounded_redacted_json(
    raw: Any,
    *,
    field_name: str,
    maximum_bytes: int = _MAX_STRUCTURED_BYTES,
) -> Any:
    redacted = _bounded_redacted_value(
        raw,
        field_name=field_name,
        depth=0,
        structured_key="",
    )
    try:
        encoded = canonical_json(redacted).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid") from exc
    if len(encoded) > maximum_bytes:
        raise ValueError(f"workflow_runtime_source_{field_name}_too_large")
    return redacted


def _bounded_redacted_value(
    raw: Any,
    *,
    field_name: str,
    depth: int,
    structured_key: str,
) -> Any:
    if depth > _MAX_STRUCTURED_DEPTH:
        raise ValueError(f"workflow_runtime_source_{field_name}_too_deep")
    if isinstance(raw, Mapping):
        if len(raw) > _MAX_STRUCTURED_ITEMS:
            raise ValueError(f"workflow_runtime_source_{field_name}_too_many_items")
        result: dict[str, Any] = {}
        for raw_key, item in raw.items():
            if not isinstance(raw_key, str) or len(raw_key) > 160:
                raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
            snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_key)
            lowered = re.sub(r"[^a-z0-9]+", "_", snake_key.lower()).strip("_")
            compact = lowered.replace("_", "")
            sensitive_category = next(
                (
                    part
                    for part in _SENSITIVE_EVENT_KEY_PARTS
                    if lowered not in _SAFE_TOKEN_KEYS and (part in lowered or part.replace("_", "") in compact)
                ),
                None,
            )
            if sensitive_category is not None:
                # Never persist an attacker-controlled secret-bearing key. A
                # fixed category retains useful diagnostics without leaking
                # credentials through JSON object names.
                result[f"redacted_{sensitive_category}"] = _REDACTED_PUBLIC_TEXT
                continue
            if raw_key not in _PUBLIC_STRUCTURED_ALLOWED_KEYS:
                # Structured source fields are never an open JSON passthrough.
                # Unknown keys are discarded before their values are traversed,
                # so a neutral-looking key cannot smuggle prompts or PII into
                # the durable Hub read model.
                continue
            result[raw_key] = _bounded_redacted_value(
                item,
                field_name=field_name,
                depth=depth + 1,
                structured_key=raw_key,
            )
        return result
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if len(raw) > _MAX_STRUCTURED_ITEMS:
            raise ValueError(f"workflow_runtime_source_{field_name}_too_many_items")
        return [
            _bounded_redacted_value(
                value,
                field_name=field_name,
                depth=depth + 1,
                structured_key=structured_key,
            )
            for value in raw
        ]
    if raw is None:
        return raw
    if isinstance(raw, bool):
        if structured_key not in _PUBLIC_STRUCTURED_BOOLEAN_KEYS:
            raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
        return raw
    if isinstance(raw, str):
        bounded = _bounded_text(
            raw,
            field_name=field_name,
            maximum=4096,
            allow_empty=True,
        )
        if bounded == _REDACTED_PUBLIC_TEXT:
            return bounded
        if structured_key in _PUBLIC_STRUCTURED_FREE_TEXT_KEYS:
            return _redacted_public_text(
                bounded,
                field_name=field_name,
                maximum=4096,
                allow_empty=True,
            )
        if structured_key == "reason_code":
            _bounded_text(
                bounded,
                field_name=field_name,
                maximum=512,
                allow_empty=True,
            )
            return "" if not bounded else _REDACTED_REASON_CODE
        if structured_key in _PUBLIC_STRUCTURED_IDENTITY_KEYS:
            _identity_syntax(bounded, field_name=field_name)
            return "" if not bounded else _REDACTED_PUBLIC_TEXT
        if structured_key in _PUBLIC_STRUCTURED_REFERENCE_KEYS:
            _reference_syntax(bounded, field_name=field_name)
            return "" if not bounded else _REDACTED_PUBLIC_TEXT
        if structured_key in _PUBLIC_STRUCTURED_CODE_KEYS:
            return _public_structured_enum(
                bounded,
                structured_key=structured_key,
                field_name=field_name,
            )
        if structured_key in _PUBLIC_STRUCTURED_LOCAL_ROUTE_KEYS:
            _bounded_text(
                bounded,
                field_name=field_name,
                maximum=2048,
                allow_empty=True,
            )
            return "" if not bounded else _REDACTED_PUBLIC_TEXT
        # String elements inside an allowlisted collection (for example a raw
        # message list) retain only their presence, never their free content.
        return _redacted_public_text(
            bounded,
            field_name=field_name,
            maximum=4096,
            allow_empty=True,
        )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if structured_key not in _PUBLIC_STRUCTURED_NUMBER_KEYS:
            raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
        if isinstance(raw, float) and not math.isfinite(raw):
            raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
        if raw < 0:
            raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
        return raw
    raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
