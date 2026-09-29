"""Fail-closed infrastructure-to-Hub workflow status projection policy.

Vocabulary, scalar normalization, event redaction, and step projection live in
the ``workflow_runtime_status_*`` sibling modules and are re-exported here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime_status_redaction import (  # noqa: F401 - public re-export
    _bounded_redacted_json,
    _bounded_redacted_value,
    _event_sequence,
    _project_event,
    _project_events,
)
from agent.services.workflow_runtime_status_scalars import (  # noqa: F401 - public re-export
    _bounded_text,
    _canonical_step_ids,
    _contains_sensitive_public_scalar,
    _cursor,
    _identity,
    _identity_syntax,
    _local_public_route,
    _nonnegative_integer,
    _nonnegative_number,
    _normalized_public_status,
    _normalized_public_step_status,
    _observed_timestamp,
    _optional_identity,
    _optional_reference,
    _positive_integer,
    _public_code,
    _public_event_status,
    _public_event_type,
    _public_reason_code,
    _public_structured_enum,
    _redacted_public_text,
    _reference,
    _reference_syntax,
    _required_bounded_text,
)
from agent.services.workflow_runtime_status_steps import (  # noqa: F401 - public re-export
    _assert_projected_step_consistency,
    _project_optional_public_fields,
    _project_public_step,
    _project_public_steps,
    _project_steps,
    _project_temporal_steps,
    _source_step_identity,
    _temporal_source_ids,
)
from agent.services.workflow_runtime_status_vocabulary import (  # noqa: F401 - public re-export
    _DEFAULT_SOURCE_SCHEMAS,
    _INCOMPLETE_PUBLIC_STEP_STATUSES,
    _LIVE_PUBLIC_STEP_STATUSES,
    _MAX_EVENT_BYTES,
    _MAX_EVENTS,
    _MAX_SOURCE_BACKEND_CHARS,
    _MAX_SOURCE_SCHEMA_CHARS,
    _MAX_SOURCE_STATUS_CHARS,
    _MAX_STRUCTURED_BYTES,
    _MAX_STRUCTURED_DEPTH,
    _MAX_STRUCTURED_ITEMS,
    _PUBLIC_EVENT_SCHEMAS,
    _PUBLIC_EVENT_TYPES,
    _PUBLIC_RUNTIME_REASON_CODES,
    _PUBLIC_SOURCE_STATUSES,
    _PUBLIC_STATUS_ALIASES,
    _PUBLIC_STRUCTURED_ALLOWED_KEYS,
    _PUBLIC_STRUCTURED_BOOLEAN_KEYS,
    _PUBLIC_STRUCTURED_CODE_KEYS,
    _PUBLIC_STRUCTURED_CONTAINER_KEYS,
    _PUBLIC_STRUCTURED_ENUM_VALUES,
    _PUBLIC_STRUCTURED_FREE_TEXT_KEYS,
    _PUBLIC_STRUCTURED_IDENTITY_KEYS,
    _PUBLIC_STRUCTURED_LOCAL_ROUTE_KEYS,
    _PUBLIC_STRUCTURED_NUMBER_KEYS,
    _PUBLIC_STRUCTURED_PHASE_VALUES,
    _PUBLIC_STRUCTURED_REFERENCE_KEYS,
    _PUBLIC_STRUCTURED_STATUS_VALUES,
    _REDACTED_PUBLIC_TEXT,
    _REDACTED_REASON_CODE,
    _REFERENCE_RE,
    _SAFE_TOKEN_KEYS,
    _SENSITIVE_EVENT_KEY_PARTS,
    _SUCCESS_PUBLIC_STATUSES,
    _TEMPORAL_SOURCE_SCHEMAS,
    _TEMPORAL_SOURCE_STATUSES,
    _TEMPORAL_STEP_STATE_FIELDS,
    _TERMINAL_PUBLIC_STATUSES,
)
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA


def authoritative_runtime_status(
    raw: dict[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    previous: dict[str, Any] | None,
    runtime_id: str,
    events: tuple[dict[str, Any], ...] = (),
    event_cursor: str = "",
    observed_at: float | None = None,
    allow_initial_ack: bool = False,
) -> dict[str, Any]:
    """Bind one infrastructure observation to the stable public Hub contract.

    ``allow_initial_ack`` is deliberately explicit.  Only the synchronous start
    response may omit authoritative runtime identity, revision, checkpoint and
    step fields.  Every later observation must be a fully bound status.
    """

    if not isinstance(raw, dict):
        raise TypeError("workflow_runtime_source_status_invalid")
    # Read only explicitly allowlisted fields.  Copying the entire source map
    # would traverse attacker-controlled unknown top-level data before policy
    # fences are applied.
    value: Mapping[str, Any] = raw
    old: Mapping[str, Any] = previous or {}
    source_schema = _source_schema(value, runtime_id=runtime_id)
    initial_ack = bool(allow_initial_ack and not old and source_schema == WORKFLOW_STATUS_SCHEMA)
    _assert_source_identity(
        value,
        binding=binding,
        require_bound_fields=not initial_ack,
    )
    _assert_source_backend(value, runtime_id=runtime_id)
    source_status = _source_status(value, source_schema=source_schema)
    source_revision = _source_revision(value, allow_missing=initial_ack)
    projected_status = _PUBLIC_STATUS_ALIASES.get(source_status, source_status)
    projected_steps, source_state = _project_steps(
        value,
        binding=binding,
        source_schema=source_schema,
        projected_status=projected_status,
        allow_missing=initial_ack,
    )
    _assert_projected_step_consistency(
        projected_status=projected_status,
        projected_steps=projected_steps,
    )
    previous_revision = _revision(old)
    revision = _authoritative_revision(
        source_revision,
        previous=old,
        previous_revision=previous_revision,
    )
    checkpoint = _source_checkpoint(
        value,
        binding=binding,
        previous=old,
        source_revision=source_revision,
        runtime_id=runtime_id,
        allow_missing=initial_ack,
    )
    projected_events = _project_events(
        previous=old.get("events"),
        observed=events,
        binding=binding,
    )
    source_observation: dict[str, Any] = {
        "schema": source_schema,
        "status": source_status,
    }
    if "backend" in value:
        source_observation["backend"] = _required_bounded_text(
            value.get("backend"),
            field_name="backend",
            maximum=_MAX_SOURCE_BACKEND_CHARS,
        )
    if source_revision is not None:
        source_observation["revision"] = source_revision

    projected: dict[str, Any] = {
        "schema": WORKFLOW_STATUS_SCHEMA,
        "backend": runtime_id,
        "runtime_id": runtime_id,
        "tenant_id": binding.tenant_id,
        "workflow_id": binding.workflow_id,
        "run_id": binding.run_id,
        "plan_hash": binding.plan_hash,
        "revision": revision,
        "checkpoint_ref": checkpoint,
        "events": projected_events,
        "status": projected_status,
        "steps": projected_steps,
        "updated_at": _observed_timestamp(observed_at, previous=old),
        "source_observation": source_observation,
        **source_state,
    }
    projected.update(
        _project_optional_public_fields(
            value,
            binding=binding,
            source_schema=source_schema,
        )
    )
    if event_cursor:
        projected["event_cursor"] = _cursor(event_cursor)
    _assert_same_revision_state(
        source_revision,
        previous=old,
        current=projected,
    )
    return projected


def _source_schema(raw: Mapping[str, Any], *, runtime_id: str) -> str:
    schema = raw.get("schema")
    if not isinstance(schema, str) or not schema or len(schema) > _MAX_SOURCE_SCHEMA_CHARS:
        raise ValueError("workflow_runtime_source_schema_invalid")
    allowed = _TEMPORAL_SOURCE_SCHEMAS if runtime_id == "temporal" else _DEFAULT_SOURCE_SCHEMAS
    if schema not in allowed:
        raise ValueError("workflow_runtime_source_schema_unsupported")
    return schema


def _assert_source_identity(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    require_bound_fields: bool,
) -> None:
    for field_name, expected in (
        ("workflow_id", binding.workflow_id),
        ("run_id", binding.run_id),
        ("plan_hash", binding.plan_hash),
    ):
        if field_name not in raw:
            if require_bound_fields:
                raise ValueError(f"workflow_runtime_source_{field_name}_required")
            continue
        observed = raw[field_name]
        if not isinstance(observed, str) or observed != expected:
            raise ValueError(f"workflow_runtime_source_{field_name}_mismatch")
    if "tenant_id" in raw and raw.get("tenant_id") != binding.tenant_id:
        raise ValueError("workflow_runtime_source_tenant_id_mismatch")


def _assert_source_backend(raw: Mapping[str, Any], *, runtime_id: str) -> None:
    if "backend" not in raw:
        return
    source_backend = raw["backend"]
    allowed = {runtime_id}
    if runtime_id == "ananta-native":
        allowed.add("local")
    if not isinstance(source_backend, str) or source_backend not in allowed:
        raise ValueError("workflow_runtime_source_backend_mismatch")


def _source_status(raw: Mapping[str, Any], *, source_schema: str) -> str:
    status = _required_bounded_text(
        raw.get("status"),
        field_name="status",
        maximum=_MAX_SOURCE_STATUS_CHARS,
    ).lower()
    allowed = _TEMPORAL_SOURCE_STATUSES if source_schema == TEMPORAL_STATUS_SCHEMA else _PUBLIC_SOURCE_STATUSES
    if status not in allowed:
        raise ValueError("workflow_runtime_source_status_unsupported")
    return status


def _source_revision(raw: Mapping[str, Any], *, allow_missing: bool) -> int | None:
    if "revision" not in raw:
        if allow_missing:
            return None
        raise ValueError("workflow_runtime_source_revision_required")
    value = raw["revision"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("workflow_runtime_source_revision_invalid")
    return value


def _revision(status: Mapping[str, Any]) -> int:
    value = status.get("revision", 0)
    if isinstance(value, bool):
        raise ValueError("workflow_runtime_revision_invalid")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow_runtime_revision_invalid") from exc
    if revision < 0:
        raise ValueError("workflow_runtime_revision_invalid")
    return revision


def _authoritative_revision(
    source_revision: int | None,
    *,
    previous: Mapping[str, Any],
    previous_revision: int,
) -> int:
    if source_revision is None:
        if previous:
            raise ValueError("workflow_runtime_source_revision_required")
        return 0
    if previous and source_revision < previous_revision:
        raise ValueError("workflow_runtime_source_revision_regressed")
    return source_revision


def _source_checkpoint(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    previous: Mapping[str, Any],
    source_revision: int | None,
    runtime_id: str,
    allow_missing: bool,
) -> str:
    raw_checkpoint = raw.get("checkpoint_ref")
    if raw_checkpoint in {None, ""}:
        if not allow_missing:
            raise ValueError("workflow_runtime_source_checkpoint_ref_required")
        checkpoint = binding.checkpoint_id
    else:
        checkpoint = _canonical_checkpoint_ref(
            raw_checkpoint,
            raw=raw,
            binding=binding,
            source_revision=source_revision,
            runtime_id=runtime_id,
        )
    old_checkpoint = str(previous.get("checkpoint_ref") or "")
    old_source_revision = _previous_source_revision(previous)
    if (
        previous
        and source_revision is not None
        and old_source_revision is not None
        and source_revision > old_source_revision
        and checkpoint == old_checkpoint
    ):
        raise ValueError("workflow_runtime_source_checkpoint_ref_stale")
    return checkpoint


def _canonical_checkpoint_ref(
    value: Any,
    *,
    raw: Mapping[str, Any],
    binding: WorkflowControlRunBinding,
    source_revision: int | None,
    runtime_id: str,
) -> str:
    checkpoint = _reference_syntax(value, field_name="checkpoint_ref")
    if checkpoint == binding.checkpoint_id:
        return checkpoint
    if source_revision is None:
        raise ValueError("workflow_runtime_source_checkpoint_ref_unproven")
    expected: set[str] = set()
    if runtime_id == "temporal":
        expected.add(f"temporal:{binding.workflow_id}:{source_revision}")
    elif runtime_id == "langgraph":
        expected.add(f"langgraph:{binding.plan_hash}:{source_revision}")
    elif runtime_id == "ananta-native" and raw.get("backend") == "local":
        local_checkpoint = f"local:{binding.workflow_id}:{source_revision}"
        expected.add(local_checkpoint)
        if re.fullmatch(r"wfc-[0-9a-f]{32}", checkpoint):
            return local_checkpoint
    if checkpoint not in expected:
        raise ValueError("workflow_runtime_source_checkpoint_ref_unproven")
    return checkpoint


def _previous_source_revision(previous: Mapping[str, Any]) -> int | None:
    observation = previous.get("source_observation")
    if not isinstance(observation, Mapping) or "revision" not in observation:
        return None
    value = observation["revision"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("workflow_runtime_previous_source_revision_invalid")
    return value


def _assert_same_revision_state(
    source_revision: int | None,
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    if source_revision is None or not previous:
        return
    previous_source_revision = _previous_source_revision(previous)
    if previous_source_revision is None:
        return
    if source_revision < previous_source_revision:
        raise ValueError("workflow_runtime_source_revision_regressed")
    if source_revision == previous_source_revision and _runtime_state_signature(previous) != _runtime_state_signature(
        current
    ):
        raise ValueError("workflow_runtime_source_revision_conflict")


def _runtime_state_signature(status: Mapping[str, Any]) -> str:
    excluded = {"events", "event_cursor", "updated_at"}
    return canonical_json({key: value for key, value in status.items() if key not in excluded})


__all__ = ["authoritative_runtime_status"]
