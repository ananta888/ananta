"""Optional Temporal WorkflowBackend adapter.

Temporal is deliberately imported lazily so the default local runtime has no
hard dependency on the temporalio package or a running Temporal server.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowRequest,
    WorkflowSignal,
    workflow_backend_event,
)


class TemporalWorkflowBackend:
    backend_id = "temporal"

    def __init__(
        self,
        *,
        address: str = "localhost:7233",
        namespace: str = "default",
        task_queue: str = "ananta-workflows",
        workflow_type: str = "AnantaWorkflow",
    ) -> None:
        self.address = address
        self.namespace = namespace
        self.task_queue = task_queue
        self.workflow_type = workflow_type

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(request.workflow_id, unavailable, request=request)
        errors = request.validate()
        if errors:
            return self._degraded(request.workflow_id, "invalid_workflow_request", request=request, details={"errors": errors})
        try:
            handle = _run(self._start(request))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(request.workflow_id, f"temporal_start_failed:{type(exc).__name__}", request=request)
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": request.workflow_id,
            "status": "running",
            "correlation_id": request.correlation_id,
            "workflow_request_schema": request.to_dict().get("schema"),
            "temporal": self._temporal_metadata(run_id=getattr(handle, "run_id", "")),
            "events": [
                workflow_backend_event(
                    workflow_id=request.workflow_id,
                    event_type="temporal_workflow_started",
                    status="running",
                    details={"workflow_type": self.workflow_type},
                )
            ],
        }

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(workflow_id, unavailable)
        try:
            description = _run(self._describe(workflow_id))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(workflow_id, f"temporal_status_failed:{type(exc).__name__}")
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": str(workflow_id or "").strip(),
            "status": _temporal_status_name(description),
            "temporal": self._temporal_metadata(),
            "events": [],
        }

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(workflow_id, unavailable, details={"reason": reason})
        try:
            _run(self._cancel(workflow_id))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(workflow_id, f"temporal_cancel_failed:{type(exc).__name__}", details={"reason": reason})
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": str(workflow_id or "").strip(),
            "status": "cancel_requested",
            "temporal": self._temporal_metadata(),
            "events": [
                workflow_backend_event(
                    workflow_id=str(workflow_id or "").strip(),
                    event_type="temporal_cancel_requested",
                    status="cancel_requested",
                    details={"reason": reason},
                )
            ],
        }

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(workflow_id, unavailable, details={"signal": signal.name})
        try:
            _run(self._signal(workflow_id, signal))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(workflow_id, f"temporal_signal_failed:{type(exc).__name__}", details={"signal": signal.name})
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": str(workflow_id or "").strip(),
            "status": "signal_sent",
            "temporal": self._temporal_metadata(),
            "events": [
                workflow_backend_event(
                    workflow_id=str(workflow_id or "").strip(),
                    event_type=f"temporal_signal:{signal.name}",
                    status="signal_sent",
                    actor=signal.actor,
                    details=signal.payload,
                )
            ],
        }

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        return [
            workflow_backend_event(
                workflow_id=str(workflow_id or "").strip(),
                event_type="temporal_backend_degraded",
                status="degraded",
                details={"reason": "temporal_events_not_configured"},
            )
        ]

    @staticmethod
    def _temporal_unavailable() -> str:
        try:
            import temporalio.client  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return f"temporalio_unavailable:{type(exc).__name__}"
        return ""

    def _degraded(
        self,
        workflow_id: str,
        reason: str,
        *,
        request: WorkflowRequest | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": str(workflow_id or "").strip(),
            "status": "degraded",
            "reason": reason,
            "temporal": {
                **self._temporal_metadata(),
            },
            "events": [
                workflow_backend_event(
                    workflow_id=str(workflow_id or "").strip(),
                    event_type="temporal_backend_degraded",
                    status="degraded",
                    details={"reason": reason, **dict(details or {})},
                )
            ],
        }
        if request is not None:
            payload["correlation_id"] = request.correlation_id
            payload["workflow_request_schema"] = request.to_dict().get("schema")
        return payload

    async def _client(self):
        from temporalio.client import Client

        return await Client.connect(self.address, namespace=self.namespace)

    async def _start(self, request: WorkflowRequest):
        client = await self._client()
        return await client.start_workflow(
            self.workflow_type,
            request.to_dict(),
            id=request.workflow_id,
            task_queue=self.task_queue,
        )

    async def _describe(self, workflow_id: str):
        client = await self._client()
        return await client.get_workflow_handle(str(workflow_id or "").strip()).describe()

    async def _cancel(self, workflow_id: str) -> None:
        client = await self._client()
        await client.get_workflow_handle(str(workflow_id or "").strip()).cancel()

    async def _signal(self, workflow_id: str, signal: WorkflowSignal) -> None:
        client = await self._client()
        await client.get_workflow_handle(str(workflow_id or "").strip()).signal(signal.name, signal.payload)

    def _temporal_metadata(self, *, run_id: str = "") -> dict[str, str]:
        payload = {
            "address": self.address,
            "namespace": self.namespace,
            "task_queue": self.task_queue,
            "workflow_type": self.workflow_type,
        }
        if run_id:
            payload["run_id"] = run_id
        return payload


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _temporal_status_name(description: Any) -> str:
    status = getattr(description, "status", None)
    name = getattr(status, "name", None)
    return str(name or status or "unknown").lower()
