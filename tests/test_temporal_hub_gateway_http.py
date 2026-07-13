from __future__ import annotations

import asyncio

from ananta_contracts.hub_task_gateway import (
    HUB_TASK_RECEIPT_SCHEMA,
    RETRY_BUDGET_RECEIPT_SCHEMA,
)
from ananta_contracts.temporal_workflow import (
    ActivityClass,
    AuthorizationEnvelopeRef,
    StepActivityInput,
)
from worker.temporal.hub_gateway import HttpHubTaskGateway


class RecordingGateway(HttpHubTaskGateway):
    def __init__(self) -> None:
        super().__init__(hub_url="https://hub.internal", bearer_token="service-token")
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request_json(self, method: str, path: str, payload):
        body = dict(payload) if payload is not None else None
        self.calls.append((method, path, body))
        if path.endswith("/retries"):
            return {
                "schema": RETRY_BUDGET_RECEIPT_SCHEMA,
                "retry_id": body["retry_id"],
                "category": body["retry_category"],
                "used": 1,
                "maximum": 2,
                "remaining": 1,
            }
        return {
            "schema": HUB_TASK_RECEIPT_SCHEMA,
            "hub_task_id": "hub-task-1",
            "operation_id": body["operation_id"],
            "status": "created",
            "authorization_state": "valid",
            "ledger_state": "authorized",
            "artifact_refs": [],
            "canonical_event_refs": [],
        }


def _request() -> StepActivityInput:
    return StepActivityInput(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        correlation_id="correlation-1",
        step_id="step-1",
        operation_id="operation-1",
        plan_hash="a" * 64,
        task_kind="coding",
        authorization_envelope=AuthorizationEnvelopeRef(
            envelope_id="envelope-1",
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            allowed_tools=(),
            allowed_artifacts=(),
            budgets={"retries": 2},
            issued_at=1_700_000_000,
            expires_at=1_800_000_000,
            nonce="nonce-1",
            key_id="key-1",
            signature="signed",
        ),
        artifact_refs=(),
        required_capabilities=("coding",),
        activity_class=ActivityClass.IDEMPOTENT,
        retry_budget_remaining=2,
    )


def test_worker_gateway_transports_reads_and_retry_bindings_only_in_post_bodies() -> None:
    async def scenario() -> None:
        gateway = RecordingGateway()
        request = _request()

        await gateway.submit_authorized_task(request)
        await gateway.get_task(hub_task_id="hub-task-1", operation_id=request.operation_id)
        receipt = await gateway.consume_retry(
            request,
            retry_id="temporal-attempt-2",
            category="temporal_activity",
        )

        assert receipt.remaining == 1
        assert all(method == "POST" for method, _path, _body in gateway.calls)
        assert all("?" not in path for _method, path, _body in gateway.calls)
        assert all(body and body["operation_id"] == request.operation_id for _, _, body in gateway.calls)

    asyncio.run(scenario())
