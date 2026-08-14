"""Fail-closed infrastructure-to-Hub workflow status projection policy."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from agent.services.workflow_backend import (
    WORKFLOW_EVENT_SCHEMA,
    WORKFLOW_STATUS_SCHEMA,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.events import CANONICAL_WORKFLOW_EVENT_SCHEMA
from agent.visual_process.definition_snapshot_contract import definition_snapshot_hash
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA

_PUBLIC_STATUS_ALIASES = {
    "waiting_approval": "waiting_for_approval",
    # Acknowledging cancellation is nonterminal; clients must keep polling.
    "cancel_requested": "running",
}
_PUBLIC_SOURCE_STATUSES = frozenset(
    {
        "created",
        "queued",
        "pending",
        "waiting",
        "running",
        "in_progress",
        "paused",
        "waiting_for_approval",
        "waiting_for_review",
        "done",
        "success",
        "completed",
        "succeeded",
        "error",
        "failed",
        "degraded",
        "unavailable",
        "interrupted",
        "rejected",
        "rejected_by_policy",
        "cancelled",
        "canceled",
        "skipped",
        "unknown",
        "not_found",
        "cancel_requested",
    }
)
_TEMPORAL_SOURCE_STATUSES = frozenset(
    {
        "created",
        "running",
        "paused",
        "waiting_approval",
        "completed",
        "failed",
        "cancelled",
    }
)
_TERMINAL_PUBLIC_STATUSES = frozenset(
    {
        "done",
        "success",
        "completed",
        "succeeded",
        "error",
        "failed",
        "degraded",
        "unavailable",
        "interrupted",
        "rejected",
        "rejected_by_policy",
        "cancelled",
        "canceled",
        "skipped",
    }
)
_SUCCESS_PUBLIC_STATUSES = frozenset({"done", "success", "completed", "succeeded"})
_LIVE_PUBLIC_STEP_STATUSES = frozenset(
    {
        "running",
        "in_progress",
        "paused",
        "waiting_for_approval",
        "waiting_for_review",
    }
)
_INCOMPLETE_PUBLIC_STEP_STATUSES = frozenset(
    {
        "created",
        "queued",
        "pending",
        "waiting",
        "unknown",
        "not_found",
    }
)
_TEMPORAL_SOURCE_SCHEMAS = frozenset({WORKFLOW_STATUS_SCHEMA, TEMPORAL_STATUS_SCHEMA})
_DEFAULT_SOURCE_SCHEMAS = frozenset({WORKFLOW_STATUS_SCHEMA})
_TEMPORAL_STEP_STATE_FIELDS = (
    "active_step_ids",
    "completed_step_ids",
    "failed_step_ids",
    "open_gates",
)
_PUBLIC_EVENT_SCHEMAS = frozenset({WORKFLOW_EVENT_SCHEMA, CANONICAL_WORKFLOW_EVENT_SCHEMA})
_REFERENCE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
_MAX_SOURCE_SCHEMA_CHARS = 160
_MAX_SOURCE_STATUS_CHARS = 64
_MAX_SOURCE_BACKEND_CHARS = 80
_MAX_EVENTS = 256
_MAX_EVENT_BYTES = 16_384
_MAX_STRUCTURED_BYTES = 16_384
_MAX_STRUCTURED_DEPTH = 6
_MAX_STRUCTURED_ITEMS = 128
_SENSITIVE_EVENT_KEY_PARTS = (
    "private_key",
    "raw_content",
    "authorization",
    "credential",
    "password",
    "api_key",
    "cookie",
    "prompt",
    "secret",
    "token",
)
_SAFE_TOKEN_KEYS = frozenset(
    {
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "completion_tokens",
        "input_tokens",
        "max_tokens",
        "output_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "token_count",
        "token_usage",
        "total_tokens",
    }
)
_REDACTED_PUBLIC_TEXT = "[REDACTED]"
_REDACTED_REASON_CODE = "runtime_reason_redacted"
_PUBLIC_RUNTIME_REASON_CODES = frozenset(
    {
        _REDACTED_REASON_CODE,
        "activity_failed",
        "approval_required",
        "caseflow_edge_trace_query_transport_forbidden",
        "condition_evaluation_failed",
        "configured_backend_selected",
        "configured_backend_selection_mismatch",
        "direct_signal_forbidden",
        "gate_failed",
        "gate_failed_but_policy_skip",
        "gate_failed_pending_human_approval",
        "gate_pending",
        "gate_timeout",
        "hub_control_started",
        "hub_execution_authorized",
        "langgraph_node_failed",
        "native_node_result_contract_missing",
        "native_operator_cancelled",
        "native_route_not_selected",
        "plan_reauthorization_required",
        "policy_rejected",
        "provider_transport_not_required",
        "runtime_capabilities_missing",
        "runtime_capabilities_satisfied",
        "temporal_workflow_id_mismatch",
        "upstream_unavailable",
        "worker_execution_failed",
        "workflow_adapter_cancelled",
        "workflow_adapter_contract_creation_failed",
        "workflow_adapter_queue_persistence_failed",
        "workflow_adapter_result_contract_missing",
        "workflow_backend_selection_unreachable",
        "workflow_backend_unknown",
        "workflow_cancelled",
        "workflow_changes_requested",
        "workflow_command_id_invalid",
        "workflow_id_unavailable",
        "workflow_paused",
        "workflow_principal_required",
        "workflow_signal_name_invalid",
        "workflow_signal_name_required",
        "workflow_signal_payload_invalid",
        "workflow_task_ledger_completion_mismatch",
    }
)
_PUBLIC_EVENT_TYPES = frozenset(
    {
        "step_started",
        "temporal_backend_degraded",
        "temporal_cancel_requested",
        "temporal_history_projection_unavailable",
        "temporal_workflow_started",
        "workflow.approval.granted",
        "workflow.approval.rejected",
        "workflow.approval.requested",
        "workflow.checkpoint.created",
        "workflow.control.approve",
        "workflow.control.cancel",
        "workflow.control.pause",
        "workflow.control.reject",
        "workflow.control.request_changes",
        "workflow.control.resume",
        "workflow.control.retry",
        "workflow.node.delegated",
        "workflow.plan.edited",
        "workflow.run.cancelled",
        "workflow.run.completed",
        "workflow.run.failed",
        "workflow.run.paused",
        "workflow.run.resumed",
        "workflow.run.retry_requested",
        "workflow.run.started",
        "workflow.runtime.activity_event",
        "workflow.runtime.history_event",
        "workflow.runtime.observed",
        "workflow.step.authorization_checked",
        "workflow.step.completed",
        "workflow.step.delegated",
        "workflow.step.failed",
        "workflow.step.retry_scheduled",
        "workflow.step.skipped",
        "workflow.tool.authorization_checked",
        "workflow_adapter_task_cancelled",
        "workflow_adapter_task_created",
        "workflow_cancelled",
        "workflow_node_task_cancelled",
        "workflow_node_task_created",
        "workflow_observed",
        "workflow_rejected",
        "workflow_started",
        "workflow_step_delegated",
    }
    | {f"workflow.step.{status}" for status in _PUBLIC_SOURCE_STATUSES}
)
_PUBLIC_STRUCTURED_CONTAINER_KEYS = frozenset(
    {
        "dataset",
        "datasetBuild",
        "dataset_build",
        "dataset_build_result",
        "diagnostics",
        "fallback_attempts",
        "gate",
        "job",
        "job_result",
        "links",
        "llm_call_profile",
        "message",
        "messages",
        "metrics",
        "outputs",
        "result",
        "terminal_result",
        "token_usage",
        "training",
        "usage",
    }
)
_PUBLIC_STRUCTURED_IDENTITY_KEYS = frozenset(
    {
        "adapter_id",
        "agent_run_id",
        "artifact_id",
        "attempt_id",
        "command_id",
        "dataset_id",
        "edge_id",
        "gate_id",
        "id",
        "job_id",
        "model",
        "model_id",
        "node_id",
        "profile_id",
        "provider",
        "provider_id",
        "selected_model",
        "selected_model_profile_id",
        "selected_provider_id",
        "source_step_id",
        "step_id",
        "target_step_id",
        "task_id",
        "tool",
        "tool_name",
        "training_profile_id",
    }
)
_PUBLIC_STRUCTURED_REFERENCE_KEYS = frozenset(
    {
        "causation_id",
        "correlation_id",
        "event_id",
        "trace_bundle_ref",
        "trace_id",
        "trace_ref",
    }
)
_PUBLIC_STRUCTURED_CODE_KEYS = frozenset(
    {
        "backend",
        "dataset_status",
        "decision_reason",
        "error_type",
        "event_type",
        "job_type",
        "kind",
        "legacy_status",
        "phase",
        "reason_code",
        "redaction_policy",
        "role",
        "schema",
        "source",
        "source_mode",
        "status",
        "training_phase",
        "training_status",
        "type",
        "validation_status",
    }
)
_PUBLIC_STRUCTURED_FREE_TEXT_KEYS = frozenset(
    {
        "body",
        "content",
        "description",
        "error",
        "last_error",
        "message",
        "reason",
        "summary",
        "text",
        "value",
    }
)
_PUBLIC_STRUCTURED_BOOLEAN_KEYS = frozenset(
    {
        "approved",
        "cached",
        "estimated",
        "idempotent_replay",
        "open",
        "required",
        "success",
        "terminal",
        "trainable",
        "truncated",
    }
)
_PUBLIC_STRUCTURED_NUMBER_KEYS = frozenset(
    {
        "attempt",
        "cached_tokens",
        "completion_tokens",
        "cost_micros",
        "current_step",
        "duration_ms",
        "epoch",
        "eval_loss",
        "gpu_utilization_percent",
        "input_tokens",
        "latency_ms",
        "learning_rate",
        "max_steps",
        "max_tokens",
        "occurred_at",
        "output_tokens",
        "progress_percent",
        "prompt_tokens",
        "reasoning_tokens",
        "record_count",
        "revision",
        "sequence",
        "token_count",
        "tokens_per_second",
        "total_tokens",
        "train_loss",
        "train_record_count",
        "validation_record_count",
        "vram_used_bytes",
    }
)
_PUBLIC_STRUCTURED_LOCAL_ROUTE_KEYS = frozenset(
    {
        "api_events",
        "api_job",
        "dataset_url",
        "dataset",
        "job",
        "job_url",
        "model_training",
        "model_training_url",
    }
)
_PUBLIC_STRUCTURED_STATUS_VALUES = frozenset(
    _PUBLIC_SOURCE_STATUSES
    | {
        "accepted",
        "approved",
        "blocked",
        "closed",
        "denied",
        "disabled",
        "enabled",
        "invalid",
        "not_configured",
        "open",
        "ready",
        "valid",
    }
)
_PUBLIC_STRUCTURED_PHASE_VALUES = frozenset(
    _PUBLIC_STRUCTURED_STATUS_VALUES
    | {
        "evaluating",
        "finalizing",
        "preparing",
        "training",
        "uploading",
        "validating",
    }
)
_PUBLIC_STRUCTURED_ENUM_VALUES = {
    "dataset_status": _PUBLIC_STRUCTURED_STATUS_VALUES,
    "legacy_status": _PUBLIC_STRUCTURED_STATUS_VALUES,
    "phase": _PUBLIC_STRUCTURED_PHASE_VALUES,
    "status": _PUBLIC_STRUCTURED_STATUS_VALUES,
    "training_phase": _PUBLIC_STRUCTURED_PHASE_VALUES,
    "training_status": _PUBLIC_STRUCTURED_STATUS_VALUES,
    "validation_status": _PUBLIC_STRUCTURED_STATUS_VALUES,
}
_PUBLIC_STRUCTURED_ALLOWED_KEYS = frozenset().union(
    _PUBLIC_STRUCTURED_CONTAINER_KEYS,
    _PUBLIC_STRUCTURED_IDENTITY_KEYS,
    _PUBLIC_STRUCTURED_REFERENCE_KEYS,
    _PUBLIC_STRUCTURED_CODE_KEYS,
    _PUBLIC_STRUCTURED_FREE_TEXT_KEYS,
    _PUBLIC_STRUCTURED_BOOLEAN_KEYS,
    _PUBLIC_STRUCTURED_NUMBER_KEYS,
    _PUBLIC_STRUCTURED_LOCAL_ROUTE_KEYS,
)


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


def _project_steps(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    source_schema: str,
    projected_status: str,
    allow_missing: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if source_schema == TEMPORAL_STATUS_SCHEMA:
        return _project_temporal_steps(
            raw,
            binding=binding,
            projected_status=projected_status,
        )
    return (
        _project_public_steps(raw, binding=binding, allow_missing=allow_missing),
        {},
    )


def _project_public_steps(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    allow_missing: bool,
) -> list[dict[str, Any]]:
    requested_ids = tuple(step.step_id for step in binding.request.steps)
    known_ids = frozenset(requested_ids)
    if "steps" not in raw:
        if not allow_missing:
            raise ValueError("workflow_runtime_source_steps_required")
        return [{"step_id": step_id, "status": "pending"} for step_id in requested_ids]
    raw_steps = raw["steps"]
    if not isinstance(raw_steps, list):
        raise ValueError("workflow_runtime_source_steps_invalid")
    if len(raw_steps) > len(requested_ids):
        raise ValueError("workflow_runtime_source_steps_too_many")
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_steps:
        if not isinstance(item, dict):
            raise ValueError("workflow_runtime_source_step_invalid")
        step_id = _source_step_identity(item)
        if step_id not in known_ids:
            raise ValueError("workflow_runtime_source_step_unknown")
        if step_id in by_id:
            raise ValueError("workflow_runtime_source_step_duplicate")
        by_id[step_id] = _project_public_step(item, step_id=step_id)
    return [by_id.get(step_id, {"step_id": step_id, "status": "pending"}) for step_id in requested_ids]


def _assert_projected_step_consistency(
    *,
    projected_status: str,
    projected_steps: Sequence[Mapping[str, Any]],
) -> None:
    """Keep every public backend projection inside the strict UI contract.

    Infrastructure adapters may omit known steps while a run is live, but a
    terminal overall status must never be paired with live step evidence.  A
    successful terminal status additionally proves that every requested step
    reached an explicit terminal outcome; failed steps remain valid evidence
    for partial-failure workflows.
    """

    step_statuses = tuple(str(step.get("status") or "") for step in projected_steps)
    if projected_status in _TERMINAL_PUBLIC_STATUSES and any(
        status in _LIVE_PUBLIC_STEP_STATUSES for status in step_statuses
    ):
        raise ValueError("workflow_runtime_source_terminal_step_state_conflict")
    if projected_status in _SUCCESS_PUBLIC_STATUSES and any(
        status in _INCOMPLETE_PUBLIC_STEP_STATUSES for status in step_statuses
    ):
        raise ValueError("workflow_runtime_source_completed_step_state_conflict")


def _project_public_step(raw: Mapping[str, Any], *, step_id: str) -> dict[str, Any]:
    raw_status = raw.get("status")
    raw_run_state = raw.get("run_state")
    if raw_status is None and raw_run_state is None:
        status = "pending"
    else:
        status = _normalized_public_step_status(
            raw_run_state if raw_run_state is not None else raw_status,
            field_name="step_status",
        )
        if raw_status is not None and raw_run_state is not None:
            alternate = _normalized_public_step_status(raw_status, field_name="step_status")
            if alternate != status:
                raise ValueError("workflow_runtime_source_step_status_conflict")
    projected: dict[str, Any] = {"step_id": step_id, "status": status}
    if "error" in raw and raw["error"] is not None:
        projected["error"] = _redacted_public_text(
            raw["error"],
            field_name="error",
            maximum=2048,
        )
    for key in (
        "selected_model_profile_id",
        "selected_provider_id",
        "selected_model",
        "job_id",
        "dataset_id",
        "training_profile_id",
    ):
        if key in raw and raw[key] is not None:
            _identity_syntax(raw[key], field_name=key)
            projected[key] = _REDACTED_PUBLIC_TEXT
    for key in ("training_status", "training_phase", "dataset_status"):
        if key in raw and raw[key] is not None:
            projected[key] = _public_structured_enum(
                raw[key],
                structured_key=key,
                field_name=key,
            )
    for key in ("model_training_url", "dataset_url"):
        if key in raw and raw[key] is not None:
            _local_public_route(raw[key], field_name=key)
            projected[key] = _REDACTED_PUBLIC_TEXT
    for key in ("started_at", "finished_at", "duration_ms"):
        if key in raw and raw[key] is not None:
            projected[key] = _nonnegative_number(raw[key], field_name=key)
    if "terminal" in raw:
        if not isinstance(raw["terminal"], bool):
            raise ValueError("workflow_runtime_source_step_terminal_invalid")
        projected["terminal"] = raw["terminal"]
    if "gate" in raw and raw["gate"] is not None:
        gate = raw["gate"]
        if isinstance(gate, Mapping):
            projected["gate"] = _bounded_redacted_json(
                gate,
                field_name="step_gate",
            )
        else:
            raise ValueError("workflow_runtime_source_step_gate_invalid")
    for key, expected in (
        ("dataset_build_result", Mapping),
        ("diagnostics", Mapping),
        ("links", Mapping),
        ("training", Mapping),
        ("datasetBuild", Mapping),
        ("dataset_build", Mapping),
        ("fallback_attempts", Sequence),
        ("llm_call_profile", Sequence),
    ):
        if key not in raw or raw[key] is None:
            continue
        if expected is Mapping and not isinstance(raw[key], Mapping):
            raise ValueError(f"workflow_runtime_source_step_{key}_invalid")
        if expected is Sequence and (isinstance(raw[key], (str, bytes)) or not isinstance(raw[key], Sequence)):
            raise ValueError(f"workflow_runtime_source_step_{key}_invalid")
        projected[key] = _bounded_redacted_json(
            raw[key],
            field_name=f"step_{key}",
        )
    return projected


def _source_step_identity(raw: Mapping[str, Any]) -> str:
    step_id = raw.get("step_id")
    legacy_id = raw.get("id")
    if step_id is not None and legacy_id is not None and step_id != legacy_id:
        raise ValueError("workflow_runtime_source_step_identity_mismatch")
    value = step_id if step_id is not None else legacy_id
    return _identity(value, field_name="step_identity")


def _project_temporal_steps(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    projected_status: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_ids = tuple(step.step_id for step in binding.request.steps)
    known_ids = frozenset(requested_ids)
    states = {
        field_name: _temporal_source_ids(raw, field_name=field_name, known_ids=known_ids)
        for field_name in _TEMPORAL_STEP_STATE_FIELDS
    }
    current_step_id = raw.get("current_step_id")
    if not isinstance(current_step_id, str):
        raise ValueError("workflow_runtime_source_current_step_invalid")
    if current_step_id and current_step_id not in known_ids:
        raise ValueError("workflow_runtime_source_step_unknown")

    completed = states["completed_step_ids"]
    failed = states["failed_step_ids"]
    active = states["active_step_ids"]
    open_gates = states["open_gates"]
    classified = (completed, failed, active, open_gates)
    if any(left.intersection(right) for index, left in enumerate(classified) for right in classified[index + 1 :]):
        raise ValueError("workflow_runtime_source_step_state_overlap")
    if current_step_id and current_step_id in completed.union(failed):
        raise ValueError("workflow_runtime_source_current_step_conflict")

    gate_ids = frozenset(step.step_id for step in binding.request.steps if step.gate)
    if not open_gates.issubset(gate_ids):
        raise ValueError("workflow_runtime_source_open_gate_mismatch")
    if projected_status == "waiting_for_approval" and (len(open_gates) != 1 or current_step_id not in open_gates):
        raise ValueError("workflow_runtime_source_open_gate_required")
    if projected_status in _TERMINAL_PUBLIC_STATUSES:
        if active or open_gates or current_step_id:
            raise ValueError("workflow_runtime_source_terminal_step_state_conflict")
        if projected_status == "completed" and completed.union(failed) != known_ids:
            raise ValueError("workflow_runtime_source_completed_step_state_conflict")

    projected: list[dict[str, Any]] = []
    for step_id in requested_ids:
        if step_id in completed:
            status = "completed"
        elif step_id in failed:
            status = "failed"
        elif step_id in open_gates:
            status = "waiting_for_approval"
        elif step_id in active or step_id == current_step_id:
            status = "running"
        elif projected_status in {"cancelled", "canceled"}:
            status = "cancelled"
        elif projected_status in {"failed", "error"}:
            status = "unknown"
        else:
            status = "pending"
        projected.append({"step_id": step_id, "status": status})

    retry_budget = _nonnegative_integer(
        raw.get("retry_budget_remaining"),
        field_name="retry_budget_remaining",
    )
    plan_revision = _positive_integer(
        raw.get("plan_revision"),
        field_name="plan_revision",
    )
    reason_code = _public_reason_code(
        raw.get("reason_code"),
        field_name="reason_code",
        allow_empty=True,
    )
    state = {
        "current_step_id": current_step_id,
        "completed_step_ids": [step_id for step_id in requested_ids if step_id in completed],
        "retry_budget_remaining": retry_budget,
        "open_gates": [step_id for step_id in requested_ids if step_id in open_gates],
        "reason_code": reason_code,
        "plan_revision": plan_revision,
        "active_step_ids": [step_id for step_id in requested_ids if step_id in active],
        "failed_step_ids": [step_id for step_id in requested_ids if step_id in failed],
    }
    return projected, state


def _temporal_source_ids(
    raw: Mapping[str, Any],
    *,
    field_name: str,
    known_ids: frozenset[str],
) -> frozenset[str]:
    value = raw.get(field_name)
    if not isinstance(value, list):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if len(value) > len(known_ids):
        raise ValueError(f"workflow_runtime_source_{field_name}_too_many")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if len(value) != len(set(value)):
        raise ValueError(f"workflow_runtime_source_{field_name}_duplicate")
    result = frozenset(value)
    if not result.issubset(known_ids):
        raise ValueError("workflow_runtime_source_step_unknown")
    return result


def _project_optional_public_fields(
    raw: Mapping[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    source_schema: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    expected_snapshot_hash = definition_snapshot_hash(dict(binding.request.metadata))
    if "snapshot_hash" in raw and raw["snapshot_hash"] is not None:
        source_snapshot_hash = (
            _bounded_text(
                raw["snapshot_hash"],
                field_name="snapshot_hash",
                maximum=256,
            )
            .removeprefix("sha256:")
            .lower()
        )
        if not expected_snapshot_hash or source_snapshot_hash != expected_snapshot_hash:
            raise ValueError("workflow_runtime_source_snapshot_hash_mismatch")
    if expected_snapshot_hash:
        result["snapshot_hash"] = expected_snapshot_hash
    if source_schema == TEMPORAL_STATUS_SCHEMA:
        # Temporal diagnostics above are projected from validated primitives;
        # parameters and plan_ref deliberately never cross this boundary.
        return result
    if "process_id" in raw and raw["process_id"] is not None:
        process_id = _identity(raw["process_id"], field_name="process_id")
        if process_id != binding.workflow_id:
            raise ValueError("workflow_runtime_source_process_id_mismatch")
        result["process_id"] = process_id
    expected_correlation_id = str(binding.request.correlation_id or "")
    if expected_correlation_id:
        expected_correlation_id = _identity(
            expected_correlation_id,
            field_name="binding_correlation_id",
        )
    if "correlation_id" in raw and raw["correlation_id"] is not None:
        source_correlation_id = _identity(raw["correlation_id"], field_name="correlation_id")
        if not expected_correlation_id or source_correlation_id != expected_correlation_id:
            raise ValueError("workflow_runtime_source_correlation_id_mismatch")
    if expected_correlation_id:
        result["correlation_id"] = expected_correlation_id
    if "process_version" in raw and raw["process_version"] is not None:
        _identity_syntax(raw["process_version"], field_name="process_version")
        result["process_version"] = _REDACTED_PUBLIC_TEXT
    for key, maximum in (("error", 2048), ("reason", 512)):
        if key in raw and raw[key] is not None:
            result[key] = _redacted_public_text(
                raw[key],
                field_name=key,
                maximum=maximum,
                allow_empty=True,
            )
    if "reason_code" in raw and raw["reason_code"] is not None:
        result["reason_code"] = _public_reason_code(
            raw["reason_code"],
            field_name="reason_code",
            allow_empty=True,
        )
    for key in ("created_at", "started_at", "finished_at"):
        if key in raw and raw[key] is not None:
            result[key] = _nonnegative_number(raw[key], field_name=key)
    if "gate" in raw and raw["gate"] is not None:
        if not isinstance(raw["gate"], Mapping):
            raise ValueError("workflow_runtime_source_gate_invalid")
        result["gate"] = _bounded_redacted_json(raw["gate"], field_name="gate")
    temporal = raw.get("temporal")
    if temporal is not None:
        if not isinstance(temporal, Mapping):
            raise ValueError("workflow_runtime_source_temporal_invalid")
        source_run_id = temporal.get("run_id")
        if source_run_id in {None, ""}:
            result["temporal"] = {}
        else:
            source_run_id = _identity_syntax(source_run_id, field_name="temporal_run_id")
            result["temporal"] = {
                "run_id": binding.run_id if source_run_id == binding.run_id else _REDACTED_PUBLIC_TEXT
            }
    return result


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
    known_step_ids = frozenset(step.step_id for step in binding.request.steps)
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


def _normalized_public_status(raw: Any, *, field_name: str) -> str:
    value = _required_bounded_text(
        raw,
        field_name=field_name,
        maximum=_MAX_SOURCE_STATUS_CHARS,
    ).lower()
    if value not in _PUBLIC_SOURCE_STATUSES:
        raise ValueError(f"workflow_runtime_source_{field_name}_unsupported")
    return _PUBLIC_STATUS_ALIASES.get(value, value)


def _normalized_public_step_status(raw: Any, *, field_name: str) -> str:
    status = _normalized_public_status(raw, field_name=field_name)
    if status == "not_found":
        raise ValueError(f"workflow_runtime_source_{field_name}_unsupported")
    return status


def _observed_timestamp(observed_at: float | None, *, previous: Mapping[str, Any]) -> float:
    candidate = time.time() if observed_at is None else observed_at
    timestamp = _nonnegative_number(candidate, field_name="observed_at")
    previous_raw = previous.get("updated_at")
    if previous_raw is None:
        return timestamp
    previous_timestamp = _nonnegative_number(
        previous_raw,
        field_name="previous_updated_at",
    )
    return max(timestamp, previous_timestamp)


def _required_bounded_text(value: Any, *, field_name: str, maximum: int) -> str:
    return _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=False,
    )


def _bounded_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if (
        normalized != value
        or len(value) > maximum
        or any(not character.isprintable() or character in {"\x7f", "\x00"} for character in value)
    ):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _redacted_public_text(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=allow_empty,
    )
    return "" if not bounded else _REDACTED_PUBLIC_TEXT


def _contains_sensitive_public_scalar(value: str) -> bool:
    """Reject secret/PII-shaped values before typed public projection.

    Identity, reference, and code grammars intentionally permit opaque text.
    Syntax validation alone therefore cannot be the public-data boundary.
    This conservative predicate is shared by every typed scalar projector and
    by nested structured evidence so an attacker cannot move the same value
    between fields to bypass redaction.
    """

    snake_value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", snake_value).strip("_")
    parts = frozenset(part for part in normalized.split("_") if part)
    sensitive_parts = {
        "authorization",
        "cookie",
        "credential",
        "password",
        "prompt",
    }
    secret_payload_parts = {"content", "data", "key", "payload", "value"}
    sensitive_compounds = {
        "api_key",
        "private_key",
        "raw_content",
    }
    if parts.intersection(sensitive_parts) or any(compound in normalized for compound in sensitive_compounds):
        return True
    if parts.intersection({"secret", "token"}) and parts.intersection(secret_payload_parts):
        return True
    if re.search(r"(?i)(?:^|[^a-z0-9])(?:bearer\s+|github_pat_|gh[pousr]_|sk-|xox[baprs]-)", value):
        return True
    if re.search(r"(?:^|[^0-9])\d{3}[-_]\d{2}[-_]\d{4}(?:$|[^0-9])", value):
        return True
    return False


def _public_reason_code(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=512,
        allow_empty=allow_empty,
    )
    if not bounded:
        return ""
    normalized = bounded.lower()
    if normalized not in _PUBLIC_RUNTIME_REASON_CODES:
        return _REDACTED_REASON_CODE
    return normalized


def _public_event_type(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(value, field_name=field_name, maximum=160)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return "workflow.runtime.observed"
    normalized = bounded.lower()
    return normalized if normalized in _PUBLIC_EVENT_TYPES else "workflow.runtime.observed"


def _public_event_status(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=64,
        allow_empty=True,
    )
    if not bounded:
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return "unknown"
    normalized = bounded.lower()
    return _PUBLIC_STATUS_ALIASES.get(normalized, normalized) if normalized in _PUBLIC_SOURCE_STATUSES else "unknown"


def _public_code(
    value: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=maximum,
        allow_empty=allow_empty,
    )
    if not bounded:
        return ""
    if _contains_sensitive_public_scalar(bounded):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return bounded


def _public_structured_enum(
    value: Any,
    *,
    structured_key: str,
    field_name: str,
) -> str:
    """Project only explicitly catalogued structured enum values.

    Worker-owned identifiers and open-ended codes are not evidence of public
    provenance merely because they satisfy a lexical grammar. Unknown fields
    therefore retain presence only; adding a new clear-text value requires an
    explicit contract entry here.
    """

    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=160,
        allow_empty=True,
    )
    if not bounded:
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", bounded):
        return _REDACTED_PUBLIC_TEXT
    allowed = _PUBLIC_STRUCTURED_ENUM_VALUES.get(structured_key)
    normalized = bounded.lower()
    return normalized if allowed is not None and normalized in allowed else _REDACTED_PUBLIC_TEXT


def _local_public_route(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=2048,
    )
    if _contains_sensitive_public_scalar(bounded):
        return _REDACTED_PUBLIC_TEXT
    parsed = urlsplit(bounded)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
        return _REDACTED_PUBLIC_TEXT
    if parsed.path == "/model-training":
        allowed_query_keys = {"dataset_id", "job_id", "tab"}
        try:
            query = parse_qsl(parsed.query, keep_blank_values=False, strict_parsing=True) if parsed.query else []
        except ValueError:
            return _REDACTED_PUBLIC_TEXT
        if len(query) > 3 or any(key not in allowed_query_keys for key, _ in query):
            return _REDACTED_PUBLIC_TEXT
        for key, item in query:
            if key == "tab" and item not in {"datasets", "jobs"}:
                return _REDACTED_PUBLIC_TEXT
            if key != "tab":
                try:
                    _identity(item, field_name=field_name)
                except ValueError:
                    return _REDACTED_PUBLIC_TEXT
        return bounded
    if (
        re.fullmatch(
            r"/api/ml-intern-training/jobs/[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}(?:/events)?",
            parsed.path,
        )
        and not parsed.query
    ):
        return bounded
    return _REDACTED_PUBLIC_TEXT


def _identity(value: Any, *, field_name: str) -> str:
    result = _identity_syntax(value, field_name=field_name)
    if _contains_sensitive_public_scalar(result):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    return result


def _identity_syntax(value: Any, *, field_name: str) -> str:
    result = _bounded_text(value, field_name=field_name, maximum=256)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", result):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return result


def _optional_identity(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=256,
        allow_empty=True,
    )
    return "" if not bounded else _identity(bounded, field_name=field_name)


def _reference(value: Any, *, field_name: str) -> str:
    result = _reference_syntax(value, field_name=field_name)
    if _contains_sensitive_public_scalar(result):
        raise ValueError(f"workflow_runtime_source_{field_name}_sensitive")
    return result


def _reference_syntax(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not _REFERENCE_RE.fullmatch(value):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _optional_reference(value: Any, *, field_name: str) -> str:
    bounded = _bounded_text(
        value,
        field_name=field_name,
        maximum=512,
        allow_empty=True,
    )
    return "" if not bounded else _reference(bounded, field_name=field_name)


def _nonnegative_integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _positive_integer(value: Any, *, field_name: str) -> int:
    result = _nonnegative_integer(value, field_name=field_name)
    if result < 1:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return result


def _nonnegative_number(value: Any, *, field_name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"workflow_runtime_source_{field_name}_invalid")
    return value


def _cursor(value: Any) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 20:
        raise ValueError("workflow_runtime_event_cursor_invalid")
    return str(int(value))


__all__ = ["authoritative_runtime_status"]
