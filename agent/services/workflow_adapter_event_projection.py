"""Canonical Hub projection for completed workflow-adapter results.

Workers may return runtime-specific diagnostics, but only this Hub-side adapter
turns the validated LangGraph execution trace into canonical workflow events.
The projection is deterministic and idempotent so status polling cannot create
different history or duplicate events.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore

LANGGRAPH_EXECUTION_PLAN_RESULT_SCHEMA = (
    "ananta.langgraph_execution_plan_result.v1"
)
LANGGRAPH_HUB_NODE_RESULT_SCHEMA = "ananta.langgraph_hub_node_result.v1"
_TRACE_EVENT = re.compile(
    r"^workflow\.(?:run|step|approval|batch)\.[a-z][a-z0-9_.-]{0,79}$"
)


class WorkflowAdapterResultProjectionError(ValueError):
    """A worker result cannot be safely represented as canonical events."""


class WorkflowAdapterCanonicalEventProjector:
    """Project a validated adapter result into the Hub-owned event store."""

    def __init__(self, events: EventStore) -> None:
        self._events = events

    def project(
        self,
        *,
        context: Mapping[str, Any],
        result: Mapping[str, Any],
        hub_task_id: str,
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        if str(context.get("adapter_kind") or "") != "langgraph":
            return ()
        artifact = _execution_plan_artifact(result)
        if artifact is None:
            node_artifact = _hub_node_artifact(result)
            if node_artifact is None:
                return ()
            event = self._node_event(
                node_artifact,
                context=context,
                hub_task_id=hub_task_id,
            )
            current = self._events.list_events(
                tenant_id=event.tenant_id,
                run_id=event.run_id,
            )
            duplicate = next(
                (value for value in current if value.dedupe_key == event.dedupe_key),
                None,
            )
            if duplicate is not None:
                if duplicate.content_hash != event.content_hash:
                    raise WorkflowAdapterResultProjectionError(
                        "workflow_adapter_langgraph_node_dedupe_conflict"
                    )
                return ()
            return (self._events.append(event, expected_sequence=len(current)),)
        trace = artifact.get("trace")
        records = artifact.get("records")
        if not isinstance(trace, list) or not isinstance(records, list):
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_invalid"
            )
        record_by_node = _validated_records(records)
        binding = _binding(context, hub_task_id=hub_task_id)
        current = list(
            self._events.list_events(
                tenant_id=binding["tenant_id"],
                run_id=binding["run_id"],
            )
        )
        known_dedupe = {event.dedupe_key for event in current}
        projected: list[CanonicalWorkflowEvent] = []
        for index, raw in enumerate(trace):
            event = self._event(
                raw,
                index=index,
                binding=binding,
                record_by_node=record_by_node,
            )
            if event.dedupe_key in known_dedupe:
                continue
            stored = self._events.append(event, expected_sequence=len(current))
            current.append(stored)
            known_dedupe.add(stored.dedupe_key)
            projected.append(stored)
        return tuple(projected)

    @staticmethod
    def _node_event(
        artifact: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
        hub_task_id: str,
    ) -> CanonicalWorkflowEvent:
        node_id = str(artifact.get("node_id") or "").strip()
        status = str(artifact.get("status") or "").strip()
        if not node_id or status not in {"completed", "failed"}:
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_node_result_invalid"
            )
        binding = _binding(context, hub_task_id=hub_task_id)
        if node_id != str(context.get("step_id") or ""):
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_node_binding_mismatch"
            )
        record = {
            str(key): value
            for key, value in artifact.items()
            if key not in {"schema", "plan_hash"}
        }
        digest = sha256_json(
            {
                "hub_task_id": hub_task_id,
                "node_id": node_id,
                "record": record,
            }
        )
        return CanonicalWorkflowEvent.build(
            tenant_id=str(binding["tenant_id"]),
            workflow_id=str(binding["workflow_id"]),
            run_id=str(binding["run_id"]),
            step_id=node_id,
            attempt=int(binding["attempt"]),
            event_type=f"workflow.step.{status}",
            correlation_id=str(binding["correlation_id"]),
            causation_id=f"hub-task:{hub_task_id}",
            dedupe_key=f"langgraph-node:{hub_task_id}:{digest}",
            actor="worker:langgraph",
            occurred_at=float(binding["occurred_at"]),
            payload={
                "runtime_id": "langgraph",
                "hub_task_id": hub_task_id,
                "node_result": record,
            },
            event_id=f"wfe-lg-node-{digest}",
        )

    @staticmethod
    def _event(
        raw: object,
        *,
        index: int,
        binding: Mapping[str, Any],
        record_by_node: Mapping[str, dict[str, Any]],
    ) -> CanonicalWorkflowEvent:
        if not isinstance(raw, Mapping):
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_event_invalid"
            )
        trace = {str(key): value for key, value in raw.items()}
        event_type = str(trace.pop("event", "")).strip()
        if _TRACE_EVENT.fullmatch(event_type) is None:
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_event_type_invalid"
            )
        step_id = str(trace.pop("node_id", "")).strip()
        if step_id and step_id not in record_by_node:
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_step_unknown"
            )
        if step_id:
            # The complete worker outcome is retained, not merely the status
            # label from the trace. Canonical redaction happens in build().
            trace["node_result"] = dict(record_by_node[step_id])
        identity = {
            "hub_task_id": binding["hub_task_id"],
            "trace_index": index,
            "event_type": event_type,
            "step_id": step_id,
            "trace": trace,
        }
        digest = sha256_json(identity)
        return CanonicalWorkflowEvent.build(
            tenant_id=str(binding["tenant_id"]),
            workflow_id=str(binding["workflow_id"]),
            run_id=str(binding["run_id"]),
            step_id=step_id,
            attempt=int(binding["attempt"]),
            event_type=event_type,
            correlation_id=str(binding["correlation_id"]),
            causation_id=f"hub-task:{binding['hub_task_id']}",
            dedupe_key=f"langgraph:{binding['hub_task_id']}:{index}:{digest}",
            actor="worker:langgraph",
            occurred_at=float(binding["occurred_at"]) + (index / 1_000_000),
            payload={
                "runtime_id": "langgraph",
                "hub_task_id": binding["hub_task_id"],
                "trace_index": index,
                **trace,
            },
            event_id=f"wfe-lg-{digest}",
        )


def _execution_plan_artifact(
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    adapter_result = result.get("adapter_result")
    if not isinstance(adapter_result, Mapping):
        return None
    artifacts = adapter_result.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    matches = [
        dict(value)
        for value in artifacts
        if isinstance(value, Mapping)
        and value.get("schema") == LANGGRAPH_EXECUTION_PLAN_RESULT_SCHEMA
    ]
    if len(matches) > 1:
        raise WorkflowAdapterResultProjectionError(
            "workflow_adapter_langgraph_trace_ambiguous"
        )
    return matches[0] if matches else None


def _hub_node_artifact(result: Mapping[str, Any]) -> dict[str, Any] | None:
    adapter_result = result.get("adapter_result")
    if not isinstance(adapter_result, Mapping):
        return None
    artifacts = adapter_result.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    matches = [
        dict(value)
        for value in artifacts
        if isinstance(value, Mapping)
        and value.get("schema") == LANGGRAPH_HUB_NODE_RESULT_SCHEMA
    ]
    if len(matches) > 1:
        raise WorkflowAdapterResultProjectionError(
            "workflow_adapter_langgraph_node_result_ambiguous"
        )
    return matches[0] if matches else None


def _validated_records(values: list[object]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, Mapping):
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_record_invalid"
            )
        record = {str(key): value for key, value in raw.items()}
        node_id = str(record.get("node_id") or "").strip()
        status = str(record.get("status") or "").strip()
        if (
            not node_id
            or node_id in records
            or status
            not in {"blocked", "cancelled", "completed", "failed", "skipped"}
        ):
            raise WorkflowAdapterResultProjectionError(
                "workflow_adapter_langgraph_trace_record_invalid"
            )
        records[node_id] = record
    return records


def _binding(
    context: Mapping[str, Any], *, hub_task_id: str
) -> dict[str, Any]:
    required = {
        name: str(context.get(name) or "").strip()
        for name in ("tenant_id", "workflow_id", "run_id")
    }
    if not all(required.values()):
        raise WorkflowAdapterResultProjectionError(
            "workflow_adapter_langgraph_trace_binding_invalid"
        )
    authorization = context.get("authorization_envelope")
    issued_at = (
        float(authorization.get("issued_at") or 1.0)
        if isinstance(authorization, Mapping)
        else 1.0
    )
    return {
        **required,
        "hub_task_id": str(hub_task_id),
        "correlation_id": str(context.get("correlation_id") or required["run_id"]),
        "attempt": int(context.get("fencing_token") or 0),
        "occurred_at": max(issued_at, 1.0),
    }


__all__ = [
    "LANGGRAPH_EXECUTION_PLAN_RESULT_SCHEMA",
    "LANGGRAPH_HUB_NODE_RESULT_SCHEMA",
    "WorkflowAdapterCanonicalEventProjector",
    "WorkflowAdapterResultProjectionError",
]
