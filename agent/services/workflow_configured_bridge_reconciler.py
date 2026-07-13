"""Lease/CAS reconciler for durable infrastructure-backed runtime bridges."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Callable

from agent.services.workflow_control_bindings import (
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_runtime.ports import DurableRunInfrastructurePort


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
    ) -> None:
        self._runtime_id = str(runtime_id)
        self._bindings = bindings
        self._durable_runs = durable_runs
        self._project = project
        self._owner_id = owner_id or f"{runtime_id}-reconciler-{uuid.uuid4().hex}"

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        processed = 0
        failures: list[dict[str, str]] = []
        for binding in self._bindings.claim_reconcilable(
            runtime_id=self._runtime_id,
            owner_id=self._owner_id,
            lease_seconds=30.0,
            limit=limit,
        ):
            try:
                previous = self._bindings.last_status(binding.workflow_id) or {}
                expected_revision = _revision(previous)
                expected_checkpoint = str(
                    previous.get("checkpoint_ref") or binding.checkpoint_id
                )
                observed = self._durable_runs.describe(
                    tenant_id=binding.tenant_id,
                    run_id=binding.workflow_id,
                )
                page = self._durable_runs.history(
                    tenant_id=binding.tenant_id,
                    run_id=binding.workflow_id,
                    after_cursor=str(previous.get("event_cursor") or 0),
                )
                events = tuple(
                    dict(value)
                    for value in page.get("events") or ()
                    if isinstance(value, dict)
                )
                status = authoritative_runtime_status(
                    observed,
                    binding=binding,
                    previous=previous,
                    runtime_id=self._runtime_id,
                    events=events,
                    event_cursor=str(
                        page.get("next_cursor") or previous.get("event_cursor") or "0"
                    ),
                )
                self._bindings.finish_reconciliation(
                    binding.workflow_id,
                    owner_id=self._owner_id,
                    expected_revision=expected_revision,
                    expected_checkpoint_ref=expected_checkpoint,
                    status=status,
                )
                self._project(binding, status, events=events)
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
        return {
            "runtime_id": self._runtime_id,
            "processed": processed,
            "failed": failures,
        }


def authoritative_runtime_status(
    raw: dict[str, Any],
    *,
    binding: WorkflowControlRunBinding,
    previous: dict[str, Any] | None,
    runtime_id: str,
    events: tuple[dict[str, Any], ...] = (),
    event_cursor: str = "",
) -> dict[str, Any]:
    """Bind an infrastructure observation to a monotonic Hub revision."""

    value = deepcopy(dict(raw))
    old = deepcopy(previous or {})
    old_revision = _revision(old)
    raw_revision = _revision(value)
    revision = max(raw_revision, old_revision + (1 if old else 0))
    checkpoint = str(value.get("checkpoint_ref") or "")
    if not checkpoint or checkpoint == str(old.get("checkpoint_ref") or ""):
        checkpoint = (
            binding.checkpoint_id
            if not old and revision == 0
            else f"{runtime_id}:{binding.plan_hash}:{revision}"
        )
    combined_events = [
        *(
            dict(item)
            for item in old.get("events") or ()
            if isinstance(item, dict)
        ),
        *(dict(item) for item in events),
    ]
    value.update(
        schema=str(value.get("schema") or "ananta.workflow_backend_status.v1"),
        backend=runtime_id,
        runtime_id=runtime_id,
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        plan_hash=str(value.get("plan_hash") or binding.plan_hash),
        revision=revision,
        checkpoint_ref=checkpoint,
        events=combined_events[-256:],
    )
    if event_cursor:
        value["event_cursor"] = event_cursor
    return value


def _revision(status: dict[str, Any]) -> int:
    try:
        value = int(status.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow_runtime_revision_invalid") from exc
    if value < 0:
        raise ValueError("workflow_runtime_revision_invalid")
    return value


__all__ = ["ConfiguredBridgeReconciler", "authoritative_runtime_status"]
