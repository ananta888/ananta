"""Public status, event, reason-code, and structured-field allowlists plus size bounds for runtime status projection."""

from __future__ import annotations

import re

from agent.services.workflow_backend import WORKFLOW_EVENT_SCHEMA, WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_runtime.events import CANONICAL_WORKFLOW_EVENT_SCHEMA
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
