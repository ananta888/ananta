"""Projection of runtime and Temporal step state and optional public fields into the public status view."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.bpmn_runtime_provenance import bpmn_step_provenance
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime_status_redaction import _bounded_redacted_json
from agent.services.workflow_runtime_status_scalars import (
    _bounded_text,
    _canonical_step_ids,
    _identity,
    _identity_syntax,
    _local_public_route,
    _nonnegative_integer,
    _nonnegative_number,
    _normalized_public_step_status,
    _positive_integer,
    _public_reason_code,
    _public_structured_enum,
    _redacted_public_text,
)
from agent.services.workflow_runtime_status_vocabulary import (
    _INCOMPLETE_PUBLIC_STEP_STATUSES,
    _LIVE_PUBLIC_STEP_STATUSES,
    _REDACTED_PUBLIC_TEXT,
    _SUCCESS_PUBLIC_STATUSES,
    _TEMPORAL_STEP_STATE_FIELDS,
    _TERMINAL_PUBLIC_STATUSES,
)
from agent.visual_process.definition_snapshot_contract import definition_snapshot_hash
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA
from ananta_contracts.temporal_workflow import WorkflowCommandType


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
    requested_ids = _canonical_step_ids(binding)
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
    # Source/iteration identity comes exclusively from the admitted plan. Never
    # copy an infrastructure adapter's or Worker's claimed activation lineage.
    provenance = bpmn_step_provenance(binding.execution_plan or {})
    return [
        {**by_id.get(step_id, {"step_id": step_id, "status": "pending"}), **provenance.get(step_id, {})}
        for step_id in requested_ids
    ]


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
    definition_hash = ((binding.execution_plan or {}).get("metadata") or {}).get("bpmn_definition_hash")
    if definition_hash:
        if not isinstance(definition_hash, str) or re.fullmatch(r"[a-f0-9]{64}", definition_hash) is None:
            raise ValueError("workflow_runtime_definition_hash_invalid")
        result["definition_hash"] = definition_hash
    if raw.get("definition_hash") is not None and raw["definition_hash"] != definition_hash:
        raise ValueError("workflow_runtime_source_definition_hash_mismatch")
    if definition_hash or "allowed_commands" in raw:
        commands = raw.get("allowed_commands", [])
        known_commands = {command.value for command in WorkflowCommandType}
        if not isinstance(commands, list) or any(
            not isinstance(value, str) or value not in known_commands for value in commands
        ):
            raise ValueError("workflow_runtime_source_allowed_commands_invalid")
        # Only the authoritative runtime policy/checkpoint may supply hints.
        # Missing hints never authorize a control; ingress still revalidates.
        result["allowed_commands"] = list(dict.fromkeys(commands))
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
