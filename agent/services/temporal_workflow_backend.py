"""Optional Temporal WorkflowBackend adapter.

Temporal is deliberately imported lazily so the default local runtime has no
hard dependency on the temporalio package or a running Temporal server.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.services.temporal_history_projection import (
    SQLTemporalProjectionRepository,
    TemporalHistoryProjectionService,
    TemporalSDKHistorySource,
)
from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowRequest,
    WorkflowSignal,
    workflow_backend_event,
)
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA
from ananta_contracts.temporal_workflow import (
    AnantaWorkflowInput,
    TemporalContractError,
    WorkflowCommand,
    WorkflowCommandResult,
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
        projection_service: TemporalHistoryProjectionService | None = None,
    ) -> None:
        self.address = address
        self.namespace = namespace
        self.task_queue = task_queue
        self.workflow_type = workflow_type
        self._projection_service_override = projection_service
        self._projection_service_instance: TemporalHistoryProjectionService | None = None

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(request.workflow_id, unavailable, request=request)
        errors = request.validate()
        if errors:
            return self._degraded(
                request.workflow_id,
                "invalid_workflow_request",
                request=request,
                details={"errors": errors},
            )
        try:
            workflow_input = AnantaWorkflowInput.from_mapping(self._workflow_payload(request))
        except (TemporalContractError, TypeError, ValueError) as exc:
            reason = getattr(exc, "reason_code", type(exc).__name__)
            return self._degraded(
                request.workflow_id,
                "invalid_temporal_workflow_input",
                request=request,
                details={"validation_reason": str(reason)},
            )
        try:
            handle = _run(self._start(request, workflow_input))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(request.workflow_id, f"temporal_start_failed:{type(exc).__name__}", request=request)
        temporal_run_id = str(
            getattr(handle, "first_execution_run_id", "")
            or getattr(handle, "run_id", "")
            or getattr(handle, "result_run_id", "")
            or ""
        )
        if not temporal_run_id:
            try:
                description = _run(self._describe(request.workflow_id))
                temporal_run_id = str(getattr(description, "run_id", "") or "")
            except Exception as exc:  # noqa: BLE001
                return self._degraded(
                    request.workflow_id,
                    f"temporal_start_adoption_failed:{type(exc).__name__}",
                    request=request,
                )
        if not temporal_run_id:
            return self._degraded(
                request.workflow_id,
                "temporal_start_run_id_missing",
                request=request,
            )
        try:
            self._projection_service().bind_run(
                tenant_id=workflow_input.tenant_id,
                workflow_id=workflow_input.workflow_id,
                run_id=workflow_input.run_id,
                temporal_run_id=temporal_run_id,
                correlation_id=workflow_input.correlation_id,
            )
        except Exception as exc:  # noqa: BLE001
            return self._degraded(
                request.workflow_id,
                f"temporal_projection_bind_failed:{type(exc).__name__}",
                request=request,
                details={"temporal_run_id": temporal_run_id},
            )
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": self.backend_id,
            "workflow_id": request.workflow_id,
            "status": "running",
            "correlation_id": request.correlation_id,
            "workflow_request_schema": request.to_dict().get("schema"),
            "temporal": self._temporal_metadata(run_id=temporal_run_id),
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
            return self._degraded(
                workflow_id,
                f"temporal_cancel_failed:{type(exc).__name__}",
                details={"reason": reason},
            )
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
        del workflow_id, signal
        # Direct Temporal Signals cannot synchronously prove signature, replay
        # consumption and optimistic revision checks.  All mutations use the
        # Hub-verified ``command`` Update instead.
        raise PermissionError("temporal_direct_signal_forbidden")

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        page = self.list_workflow_event_page(workflow_id)
        events = list(page.get("events") or [])
        if events:
            return events
        return [
            workflow_backend_event(
                workflow_id=str(workflow_id or "").strip(),
                event_type="temporal_history_projection_unavailable",
                status=str(page.get("consistency_state") or "stale"),
                details={
                    "reason": str(page.get("reason_code") or "temporal_history_empty"),
                    "projection_cursor": page.get("projection_cursor"),
                    "mapping_version": page.get("mapping_version"),
                    "lag": page.get("lag"),
                    "consistency_state": page.get("consistency_state"),
                },
            )
        ]

    def list_workflow_event_page(
        self,
        workflow_id: str,
        *,
        expected_tenant_id: str = "",
        page_size: int = 500,
        max_pages: int = 20,
    ) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return {
                "schema": "ananta.temporal-history-projection-page.v1",
                "workflow_id": str(workflow_id or "").strip(),
                "run_id": "",
                "events": [],
                "projection_cursor": 0,
                "mapping_version": "ananta.temporal-history-map.v1",
                "lag": None,
                "consistency_state": "stale",
                "reason_code": unavailable,
                "raw_history_ref": "",
            }
        return _run(
            self._projection_service().synchronize(
                str(workflow_id or "").strip(),
                expected_tenant_id=expected_tenant_id,
                page_size=page_size,
                max_pages=max_pages,
            )
        )

    def query_workflow(self, workflow_id: str, query_name: str = "status") -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(workflow_id, unavailable)
        try:
            result = _run(self._query(workflow_id, query_name))
        except Exception as exc:  # noqa: BLE001
            return self._degraded(workflow_id, f"temporal_query_failed:{type(exc).__name__}")
        return dict(result) if isinstance(result, dict) else {"result": result}

    def update_workflow(
        self,
        workflow_id: str,
        command: dict[str, Any],
        *,
        update_id: str = "",
    ) -> dict[str, Any]:
        unavailable = self._temporal_unavailable()
        if unavailable:
            return self._degraded(workflow_id, unavailable)
        typed: WorkflowCommand | None = None
        try:
            typed = WorkflowCommand.from_mapping(command)
            result = _run(
                self._update(
                    workflow_id,
                    typed.to_dict(),
                    update_id=str(update_id or typed.command_id),
                )
            )
        except Exception as exc:  # the exact SDK type is imported lazily below
            rejected = self._rejected_update_result(
                workflow_id,
                command=typed,
                cause=exc,
            )
            if rejected is not None:
                return rejected
            if isinstance(exc, TemporalContractError):
                return self._degraded(
                    workflow_id,
                    "invalid_temporal_workflow_command",
                    details={"validation_reason": exc.reason_code},
                )
            return self._degraded(
                workflow_id,
                f"temporal_update_failed:{type(exc).__name__}",
            )
        if hasattr(result, "to_dict"):
            return dict(result.to_dict())
        return dict(result) if isinstance(result, dict) else {"result": result}

    def _rejected_update_result(
        self,
        workflow_id: str,
        *,
        command: WorkflowCommand | None,
        cause: Exception,
    ) -> dict[str, Any] | None:
        from temporalio.client import WorkflowUpdateFailedError
        from temporalio.exceptions import ApplicationError

        if command is None or not isinstance(cause, WorkflowUpdateFailedError):
            return None
        application = cause.cause
        if not isinstance(application, ApplicationError):
            return None
        reason_code = str(application.type or "").strip()
        if (
            not reason_code
            or len(reason_code) > 64
            or not reason_code[0].isalpha()
            or any(not character.isalnum() and character != "_" for character in reason_code)
        ):
            return None
        try:
            observed = _run(self._query(workflow_id, "status"))
        except Exception:  # noqa: BLE001 - ambiguous observation remains retryable
            return None
        if not isinstance(observed, dict):
            return None
        revision = observed.get("revision")
        status = observed.get("status")
        if (
            observed.get("schema") != TEMPORAL_STATUS_SCHEMA
            or observed.get("workflow_id") != command.workflow_id
            or observed.get("run_id") != command.run_id
            or observed.get("plan_hash") != command.plan_hash
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < command.expected_revision
            or not isinstance(status, str)
            or status
            not in {
                "created",
                "running",
                "paused",
                "waiting_approval",
                "completed",
                "failed",
                "cancelled",
            }
        ):
            return None
        return WorkflowCommandResult(
            command_id=command.command_id,
            accepted=False,
            revision=revision,
            status=status,
            reason_code=reason_code,
        ).to_dict()

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

        from agent.services.temporal_client_connection import TemporalHubClientSecurity

        security = TemporalHubClientSecurity.from_env()
        return await Client.connect(
            self.address,
            namespace=self.namespace,
            **security.client_kwargs(),
        )

    async def _start(self, request: WorkflowRequest, workflow_input: AnantaWorkflowInput):
        from temporalio.exceptions import WorkflowAlreadyStartedError

        client = await self._client()
        try:
            return await client.start_workflow(
                self.workflow_type,
                workflow_input.to_dict(),
                id=request.workflow_id,
                task_queue=self.task_queue,
            )
        except WorkflowAlreadyStartedError:
            # The stable workflow ID is the start idempotency key.  Re-adopt
            # the durable execution so a failed Hub projection binding can be
            # retried without creating or orphaning another Workflow.
            return client.get_workflow_handle(request.workflow_id)

    async def _describe(self, workflow_id: str):
        client = await self._client()
        return await client.get_workflow_handle(str(workflow_id or "").strip()).describe()

    async def _cancel(self, workflow_id: str) -> None:
        client = await self._client()
        await client.get_workflow_handle(str(workflow_id or "").strip()).cancel()

    async def _query(self, workflow_id: str, query_name: str):
        client = await self._client()
        return await client.get_workflow_handle(str(workflow_id or "").strip()).query(str(query_name or "status"))

    async def _update(
        self,
        workflow_id: str,
        command: dict[str, Any],
        *,
        update_id: str,
    ):
        client = await self._client()
        return await client.get_workflow_handle(str(workflow_id or "").strip()).execute_update(
            "command",
            command,
            id=update_id,
        )

    def _projection_service(self) -> TemporalHistoryProjectionService:
        if self._projection_service_override is not None:
            return self._projection_service_override
        if self._projection_service_instance is None:
            self._projection_service_instance = TemporalHistoryProjectionService(
                namespace=self.namespace,
                source=TemporalSDKHistorySource(
                    address=self.address,
                    namespace=self.namespace,
                    client_factory=self._client,
                ),
                repository=SQLTemporalProjectionRepository(),
            )
        return self._projection_service_instance

    @staticmethod
    def _workflow_payload(request: WorkflowRequest) -> dict[str, Any]:
        metadata = dict(request.metadata or {})
        policy_scope = dict(request.policy_scope or {})
        tenant_id = str(metadata.get("tenant_id") or policy_scope.get("tenant_id") or "").strip()
        run_id = str(metadata.get("run_id") or "").strip()
        plan_hash = str(metadata.get("plan_hash") or "").strip()
        policy_version = str(metadata.get("policy_version") or policy_scope.get("policy_version") or "").strip()
        envelopes = metadata.get("authorization_envelopes")
        envelope_by_step = dict(envelopes) if isinstance(envelopes, dict) else {}
        steps: list[dict[str, Any]] = []
        for step in request.steps:
            step_metadata = dict(step.metadata or {})
            envelope = step_metadata.get("authorization_envelope") or envelope_by_step.get(step.step_id)
            steps.append(
                {
                    **step.to_dict(),
                    "schema": "ananta.temporal-workflow-step.v1",
                    "operation_id": step_metadata.get("operation_id"),
                    "authorization_envelope": envelope,
                    "artifact_refs": [
                        {"artifact_id": artifact_id, "kind": "workflow_input"} for artifact_id in step.input_artifacts
                    ],
                    "activity_class": step_metadata.get("activity_class")
                    or step_metadata.get("side_effect_class")
                    or "long_running",
                    "required_capabilities": list(step_metadata.get("required_capabilities") or []),
                    "node_type": step_metadata.get("node_type") or "task",
                    "parallel_group": step_metadata.get("parallel_group") or "default",
                    "merge_strategy": step_metadata.get("merge_strategy") or "",
                    "partial_failure": step_metadata.get("partial_failure") or "fail",
                }
            )
        retry_budget_remaining = int(metadata.get("retry_budget_remaining") or 0)
        return {
            "schema": "ananta.temporal-workflow-input.v1",
            "tenant_id": tenant_id,
            "workflow_id": request.workflow_id,
            "run_id": run_id,
            "correlation_id": request.correlation_id,
            "plan_hash": plan_hash,
            "policy_version": policy_version,
            "steps": steps,
            "retry_budget_remaining": retry_budget_remaining,
            "retry_budget_maximum": int(metadata.get("retry_budget_maximum", retry_budget_remaining)),
            "mutable_parameters": list(metadata.get("mutable_parameters") or []),
            "parameters": dict(metadata.get("parameters") or {}),
            "max_parallel_steps": int(metadata.get("max_parallel_steps") or 1),
            "tenant_parallel_limit": int(metadata.get("tenant_parallel_limit") or 1),
            "worker_parallel_limit": int(metadata.get("worker_parallel_limit") or 1),
            "max_history_events": int(metadata.get("max_history_events") or 20_000),
            "max_state_bytes": int(metadata.get("max_state_bytes") or 512_000),
        }

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
