"""Pure canonical public projection for transition receipt finalization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA, WorkflowRequest
from agent.services.workflow_control_bindings import (
    WorkflowControlRunBinding,
    assert_public_status_progression,
)
from agent.services.workflow_runtime_selection_composition import configured_runtime_id
from agent.services.workflow_runtime_status_projection import authoritative_runtime_status
from agent.services.workflow_transition_outbox import (
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    WorkflowTransition,
)

_SOURCE_OBSERVATION_KEYS = frozenset({"schema", "status", "backend", "revision"})
_SOURCE_OBSERVATION_REQUIRED_KEYS = frozenset({"schema", "status"})


def canonical_workflow_public_status(
    binding: WorkflowControlRunBinding,
    status: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Derive or exactly revalidate a binding-aware public status."""

    runtime_id = configured_runtime_id(binding.runtime_id)
    if runtime_id not in {TRANSITION_RUNTIME_NATIVE, TRANSITION_RUNTIME_LANGGRAPH}:
        raise ValueError("workflow_transition_public_projection_runtime_unsupported")
    raw_observed_at = status.get("updated_at")
    if not isinstance(raw_observed_at, (int, float)) or isinstance(raw_observed_at, bool) or float(raw_observed_at) < 0:
        raise ValueError("workflow_runtime_source_updated_at_invalid")
    safe_status = dict(status)
    source_observation = _validated_source_observation(
        safe_status,
        binding=binding,
        runtime_id=runtime_id,
    )
    if source_observation is None:
        return authoritative_runtime_status(
            safe_status,
            binding=binding,
            previous=(dict(previous) if previous else None),
            runtime_id=runtime_id,
            observed_at=float(raw_observed_at),
        )

    raw_status = dict(safe_status)
    raw_status.pop("source_observation", None)
    for field_name in _SOURCE_OBSERVATION_KEYS:
        if field_name in source_observation:
            raw_status[field_name] = source_observation[field_name]
        else:
            raw_status.pop(field_name, None)
    projected = authoritative_runtime_status(
        raw_status,
        binding=binding,
        previous=None,
        runtime_id=runtime_id,
        events=tuple(safe_status.get("events") or ()),
        event_cursor=safe_status.get("event_cursor") or "",
        observed_at=float(raw_observed_at),
        allow_initial_ack="revision" not in source_observation,
    )
    if projected != safe_status:
        raise ValueError("workflow_control_public_status_reprojection_mismatch")
    return projected


class WorkflowTransitionPublicStatusProjector:
    """Stateless adapter for the transition receipt projection port."""

    def project(
        self,
        *,
        transition: WorkflowTransition,
        binding: Mapping[str, Any],
        binding_status: Mapping[str, Any],
        previous_public_status: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        authoritative = _projection_binding(binding)
        runtime_id = configured_runtime_id(authoritative.runtime_id)
        if (
            transition.tenant_id != authoritative.tenant_id
            or transition.workflow_id != authoritative.workflow_id
            or transition.run_id != authoritative.run_id
            or transition.runtime_id != runtime_id
        ):
            raise ValueError("workflow_control_public_status_binding_mismatch")
        projected = canonical_workflow_public_status(
            authoritative,
            binding_status,
            previous=previous_public_status,
        )
        assert_public_status_progression(
            dict(previous_public_status) if previous_public_status else None,
            projected,
        )
        return projected


def _projection_binding(binding: Mapping[str, Any]) -> WorkflowControlRunBinding:
    required = (
        "tenant_id",
        "subject_id",
        "workflow_id",
        "run_id",
        "runtime_id",
        "plan_hash",
        "policy_version",
        "checkpoint_id",
    )
    values: dict[str, str] = {}
    for field_name in required:
        value = binding.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError("workflow_control_public_status_binding_mismatch")
        values[field_name] = value
    workflow_request = binding.get("workflow_request")
    execution_plan = binding.get("execution_plan")
    if not isinstance(workflow_request, Mapping) or not isinstance(execution_plan, Mapping):
        raise ValueError("workflow_control_public_status_binding_mismatch")
    return WorkflowControlRunBinding(
        tenant_id=values["tenant_id"],
        subject_id=values["subject_id"],
        workflow_id=values["workflow_id"],
        run_id=values["run_id"],
        runtime_id=values["runtime_id"],
        plan_hash=values["plan_hash"],
        policy_version=values["policy_version"],
        checkpoint_id=values["checkpoint_id"],
        request=WorkflowRequest.from_mapping(dict(workflow_request)),
        execution_plan=dict(execution_plan),
    )


def _validated_source_observation(
    status: dict[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    runtime_id: str,
) -> dict[str, Any] | None:
    if "source_observation" not in status:
        return None
    source = status["source_observation"]
    if (
        not isinstance(source, dict)
        or not _SOURCE_OBSERVATION_REQUIRED_KEYS.issubset(source)
        or not frozenset(source).issubset(_SOURCE_OBSERVATION_KEYS)
        or not isinstance(source.get("schema"), str)
        or not isinstance(source.get("status"), str)
        or ("backend" in source and not isinstance(source["backend"], str))
    ):
        raise ValueError("workflow_control_public_status_source_observation_invalid")
    if (
        status.get("schema") != WORKFLOW_STATUS_SCHEMA
        or status.get("backend") != runtime_id
        or status.get("runtime_id") != runtime_id
        or status.get("tenant_id") != binding.tenant_id
        or status.get("workflow_id") != binding.workflow_id
        or status.get("run_id") != binding.run_id
        or status.get("plan_hash") != binding.plan_hash
    ):
        raise ValueError("workflow_control_public_status_binding_mismatch")
    revision = status.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("workflow_control_public_status_revision_invalid")
    if "revision" in source:
        source_revision = source["revision"]
        if (
            isinstance(source_revision, bool)
            or not isinstance(source_revision, int)
            or source_revision < 0
            or source_revision != revision
        ):
            raise ValueError("workflow_control_public_status_source_observation_mismatch")
    elif revision != 0:
        raise ValueError("workflow_control_public_status_source_observation_mismatch")
    return dict(source)


__all__ = [
    "WorkflowTransitionPublicStatusProjector",
    "canonical_workflow_public_status",
]
