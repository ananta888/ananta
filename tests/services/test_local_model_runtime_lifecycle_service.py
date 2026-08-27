import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from agent.repositories.local_model_runtime_decision import SqliteLocalRuntimeDecisionRepository
from agent.services.local_model_runtime_lifecycle_service import (
    HttpLocalRuntimeControl,
    LocalRuntimeLifecycleService,
)
from agent.services.local_multi_model_runtime import GiB, ResourceSnapshot, rtx3080_local_model_capabilities
from ananta_contracts.local_model_runtime import LocalRuntimeControlReceipt


class Resources:
    def __init__(self, free_vram=10 * GiB):
        self.free_vram = free_vram
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return ResourceSnapshot(10 * GiB, self.free_vram, 64 * GiB)


class Control:
    def __init__(self):
        self.calls = []

    def apply(self, decision, *, action):
        self.calls.append((decision.decision_id, action))
        return LocalRuntimeControlReceipt(
            decision_id=decision.decision_id,
            decision_digest=decision.decision_digest,
            action=action,
            status="completed",
            reason_code="runtime_control_completed",
            completed_at="2026-08-27T00:00:00Z",
        )


def test_admission_is_persisted_idempotent_and_content_free(tmp_path):
    resources = Resources()
    repository_path = tmp_path / "runtime.sqlite3"
    repository = SqliteLocalRuntimeDecisionRepository(repository_path)
    audit = []
    service = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=repository,
        capabilities=rtx3080_local_model_capabilities(),
        clock=lambda: datetime(2026, 8, 27, tzinfo=UTC),
        audit_sink=lambda action, facts: audit.append((action, facts)),
    )

    first = service.evaluate(request_id="request-1", capabilities=rtx3080_local_model_capabilities())
    second = service.evaluate(request_id="request-1", capabilities=rtx3080_local_model_capabilities())

    assert first == second
    assert first.admitted is True
    assert first.start_order == ("lfm", "kat", "needle")
    assert resources.calls == 1
    assert stat.S_IMODE(repository_path.stat().st_mode) == 0o600
    assert "prompt" not in str(first.to_wire()).lower()
    assert audit[0][1]["reason_code"] == "placement_admitted"


def test_denied_decision_cannot_activate(tmp_path):
    service = LocalRuntimeLifecycleService(
        resources=Resources(free_vram=2 * GiB),
        decisions=SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3"),
        capabilities=rtx3080_local_model_capabilities(),
        control=Control(),
    )
    decision = service.evaluate(request_id="request-2", capabilities=rtx3080_local_model_capabilities())

    assert decision.admitted is False
    with pytest.raises(ValueError, match="not_admitted"):
        service.apply(decision_id=decision.decision_id)


def test_control_receipt_is_bound_to_exact_persisted_decision(tmp_path):
    control = Control()
    service = LocalRuntimeLifecycleService(
        resources=Resources(),
        decisions=SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3"),
        capabilities=rtx3080_local_model_capabilities(),
        control=control,
    )
    decision = service.evaluate(request_id="request-3", capabilities=rtx3080_local_model_capabilities())

    receipt = service.apply(decision_id=decision.decision_id, action="activate")
    replay = service.apply(decision_id=decision.decision_id, action="activate")

    assert receipt.decision_digest == decision.decision_digest
    assert replay == receipt
    assert control.calls == [(decision.decision_id, "activate")]


def test_control_action_is_serialized_across_hub_service_instances(tmp_path):
    control = Control()
    resources = Resources()
    repository_path = tmp_path / "runtime.sqlite3"
    first = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=SqliteLocalRuntimeDecisionRepository(repository_path),
        capabilities=rtx3080_local_model_capabilities(),
        control=control,
    )
    second = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=SqliteLocalRuntimeDecisionRepository(repository_path),
        capabilities=rtx3080_local_model_capabilities(),
        control=control,
    )
    decision = first.evaluate(
        request_id="request-concurrent-control",
        capabilities=rtx3080_local_model_capabilities(),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(
                lambda service: service.apply(decision_id=decision.decision_id, action="restart"),
                (first, second),
            )
        )

    assert receipts[0] == receipts[1]
    assert control.calls == [(decision.decision_id, "restart")]


def test_activation_rechecks_resources_and_rejects_stale_context_decision(tmp_path):
    resources = Resources()
    audit = []
    service = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3"),
        capabilities=rtx3080_local_model_capabilities(),
        control=Control(),
        audit_sink=lambda action, facts: audit.append((action, facts)),
    )
    decision = service.evaluate(
        request_id="request-resource-race",
        capabilities=rtx3080_local_model_capabilities(),
    )
    resources.free_vram = 8 * GiB

    with pytest.raises(ValueError, match="decision_stale"):
        service.apply(decision_id=decision.decision_id, action="activate")

    assert audit[-1][0] == "local_runtime_activation_revalidation_denied"
    assert audit[-1][1]["reason_code"] == "local_runtime_decision_stale"


def test_http_control_sends_only_digest_bound_closed_action(tmp_path):
    resources = Resources()
    repository = SqliteLocalRuntimeDecisionRepository(tmp_path / "runtime.sqlite3")
    decision = LocalRuntimeLifecycleService(
        resources=resources,
        decisions=repository,
        capabilities=rtx3080_local_model_capabilities(),
    ).evaluate(request_id="request-http", capabilities=rtx3080_local_model_capabilities())

    class Response:
        status = 200

        @staticmethod
        def read():
            return (
                LocalRuntimeControlReceipt(
                    decision_id=decision.decision_id,
                    decision_digest=decision.decision_digest,
                    action="activate",
                    status="completed",
                    reason_code="runtime_control_completed",
                    completed_at="2026-08-27T00:00:00Z",
                )
                .model_dump_json(by_alias=True)
                .encode()
            )

    captured = {}

    def open_request(request, *, timeout):
        captured.update(url=request.full_url, body=request.data, timeout=timeout)
        return Response()

    receipt = HttpLocalRuntimeControl(
        "http://host.docker.internal:8093",
        token="x" * 24,
        opener=open_request,
    ).apply(decision, action="activate")

    assert receipt.decision_digest == decision.decision_digest
    assert captured["url"].endswith("/v1/control")
    assert b'"action":"activate"' in captured["body"]
    assert b"prompt" not in captured["body"]
