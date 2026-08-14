"""Lease/CAS reconciler for durable infrastructure-backed runtime bridges."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from agent.services.workflow_control_bindings import (
    WorkflowControlBindingStore,
)
from agent.services.workflow_runtime.ports import DurableRunInfrastructurePort
from agent.services.workflow_runtime_status_projection import (
    authoritative_runtime_status,
)


class ConfiguredBridgeReconciler:
    """Project Temporal observations into the Hub-owned authoritative binding."""

    def __init__(
        self,
        *,
        runtime_id: str,
        bindings: WorkflowControlBindingStore,
        durable_runs: DurableRunInfrastructurePort,
        project: Callable[..., None],
        owner_id: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runtime_id = str(runtime_id)
        self._bindings = bindings
        self._durable_runs = durable_runs
        self._project = project
        self._owner_id = owner_id or f"{runtime_id}-reconciler-{uuid.uuid4().hex}"
        self._clock = clock

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        processed = 0
        failures: list[dict[str, str]] = []
        trace_failures: list[dict[str, str]] = []
        for binding in self._bindings.claim_reconcilable(
            runtime_id=self._runtime_id,
            owner_id=self._owner_id,
            lease_seconds=30.0,
            limit=limit,
        ):
            try:
                previous = self._bindings.last_status(binding.workflow_id) or {}
                expected_revision = _revision(previous)
                expected_checkpoint = str(previous.get("checkpoint_ref") or binding.checkpoint_id)
                observed = self._durable_runs.describe(
                    tenant_id=binding.tenant_id,
                    run_id=binding.workflow_id,
                )
                previous_cursor = str(previous.get("event_cursor") or "0")
                try:
                    page = self._durable_runs.history(
                        tenant_id=binding.tenant_id,
                        run_id=binding.workflow_id,
                        after_cursor=previous_cursor,
                    )
                    events, event_cursor = _history_page(
                        page,
                        previous_cursor=previous_cursor,
                    )
                except Exception as exc:  # trace enrichment is independent of runtime truth
                    events = ()
                    event_cursor = previous_cursor
                    trace_failures.append(
                        {
                            "workflow_id": binding.workflow_id,
                            "error_type": type(exc).__name__,
                        }
                    )
                status = authoritative_runtime_status(
                    observed,
                    binding=binding,
                    previous=previous,
                    runtime_id=self._runtime_id,
                    events=events,
                    event_cursor=event_cursor,
                    observed_at=self._clock(),
                )
                self._bindings.finish_reconciliation(
                    binding.workflow_id,
                    owner_id=self._owner_id,
                    expected_revision=expected_revision,
                    expected_checkpoint_ref=expected_checkpoint,
                    status=status,
                )
                # The status already contains the bounded, identity-grounded
                # event projection. Never pass the raw infrastructure page a
                # second time into the persistent read-model projector.
                self._project(binding, status, events=())
                processed += 1
            except Exception as exc:  # one durable run cannot halt the Hub tick
                self._bindings.release_reconciliation(
                    binding.workflow_id,
                    owner_id=self._owner_id,
                )
                failures.append(
                    {
                        "workflow_id": binding.workflow_id,
                        "error_type": type(exc).__name__,
                    }
                )
        result: dict[str, Any] = {
            "runtime_id": self._runtime_id,
            "processed": processed,
            "failed": failures,
        }
        if trace_failures:
            result["trace_failed"] = trace_failures
        return result


def _revision(status: dict[str, Any]) -> int:
    if isinstance(status.get("revision"), bool):
        raise ValueError("workflow_runtime_revision_invalid")
    try:
        value = int(status.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow_runtime_revision_invalid") from exc
    if value < 0:
        raise ValueError("workflow_runtime_revision_invalid")
    return value


def _history_page(
    raw: Any,
    *,
    previous_cursor: str,
) -> tuple[tuple[dict[str, Any], ...], str]:
    if not isinstance(raw, Mapping):
        raise TypeError("workflow_runtime_history_page_invalid")
    raw_events = raw.get("events")
    if not isinstance(raw_events, list) or any(not isinstance(value, dict) for value in raw_events):
        raise ValueError("workflow_runtime_history_events_invalid")
    if len(raw_events) > 256:
        raise ValueError("workflow_runtime_history_events_too_many")
    previous = _history_cursor(previous_cursor, field_name="previous_cursor")
    current = _history_cursor(raw.get("next_cursor"), field_name="next_cursor")
    if current < previous or current - previous != len(raw_events):
        raise ValueError("workflow_runtime_history_cursor_inconsistent")
    return tuple(dict(value) for value in raw_events), str(current)


def _history_cursor(raw: Any, *, field_name: str) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"workflow_runtime_history_{field_name}_invalid")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isdigit() and len(raw) <= 20:
        value = int(raw)
    else:
        raise ValueError(f"workflow_runtime_history_{field_name}_invalid")
    if value < 0 or value > 9_223_372_036_854_775_807:
        raise ValueError(f"workflow_runtime_history_{field_name}_invalid")
    return value


__all__ = ["ConfiguredBridgeReconciler", "authoritative_runtime_status"]
