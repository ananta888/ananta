"""Command-field parsers and the gateway event recorder shared by the workflow worker gateway collaborators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.native_graph_event_appender import append_gateway_observation
from agent.services.workflow_runtime import CanonicalWorkflowEvent, EventStore, side_effect_event
from agent.services.workflow_worker_gateway_ports import WorkflowWorkerGatewayError
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


def bounded_identifier(value: object, reason_code: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 256 or "\x00" in text:
        raise WorkflowWorkerGatewayError(reason_code, status_code=422)
    return text


def optional_bounded_identifier(value: object, reason_code: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > 256 or "\x00" in text:
        raise WorkflowWorkerGatewayError(reason_code, status_code=422)
    return text


def optional_bounded_text(value: object, reason_code: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum or "\x00" in text:
        raise WorkflowWorkerGatewayError(reason_code, status_code=422)
    return text


def command_attempt_id(raw: Mapping[str, Any]) -> str:
    return bounded_identifier(raw.get("attempt_id"), "workflow_worker_attempt_id_invalid")


def command_expected_revision(raw: Mapping[str, Any]) -> int:
    try:
        expected_revision = int(raw.get("expected_revision"))
    except (TypeError, ValueError) as exc:
        raise WorkflowWorkerGatewayError("side_effect_revision_invalid", status_code=422) from exc
    if expected_revision < 1:
        raise WorkflowWorkerGatewayError("side_effect_revision_invalid", status_code=422)
    return expected_revision


class WorkflowWorkerEventRecorder:
    """Append Hub-attributed gateway observations; owns only the event store."""

    def __init__(self, events: EventStore) -> None:
        self._events = events

    def append(
        self,
        binding: WorkflowWorkerBinding,
        *,
        event_type: str,
        dedupe_key: str,
        causation_id: str,
        payload: dict[str, Any],
    ) -> None:
        event = CanonicalWorkflowEvent.build(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=binding.step_id,
            event_type=event_type,
            correlation_id=binding.correlation_id or binding.run_id,
            causation_id=causation_id,
            dedupe_key=dedupe_key,
            actor="hub",
            payload=payload,
        )
        append_gateway_observation(self._events, event)

    def append_side_effect(
        self,
        binding: WorkflowWorkerBinding,
        record: Any,
        *,
        causation_id: str,
    ) -> None:
        event = side_effect_event(
            record,
            correlation_id=binding.correlation_id or binding.run_id,
            causation_id=causation_id,
        )
        append_gateway_observation(self._events, event)
