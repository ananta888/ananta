from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path

import pytest
import yaml

from ananta_contracts.spreadsheet_studio import canonical_digest
from tests.spreadsheet_studio.helpers import snapshot
from worker.runtime.spreadsheet_queue_worker import SpreadsheetQueueWorker, SpreadsheetQueueWorkerError


class Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status = status
        self.headers = Message()
        body = b"" if payload is None else json.dumps(payload).encode()
        if payload is not None:
            self.headers["Content-Type"] = "application/json"
        self.headers["Content-Length"] = str(len(body))
        self._body = io.BytesIO(body)

    def read(self, limit: int = -1) -> bytes:
        return self._body.read(limit)


class Opener:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.responses.pop(0)


class Executor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def dry_run(self, **_values):
        if self.fail:
            raise RuntimeError("untrusted-detail-must-not-cross-boundary")
        return {"schema": "ananta.spreadsheet-execution-result.v1", "candidate": "safe"}


def _assignment() -> dict:
    return {
        "schema": "ananta.spreadsheet-worker-assignment.v1",
        "job_id": "spreadsheet-job-one",
        "worker_job_id": "worker-job-one",
        "slot_lease_id": "slot-one",
        "assignment_digest": "a" * 64,
        "snapshot": snapshot(),
        "actions": [],
        "callback_token": "callback-capability",
        "human_intervention_required": False,
    }


@pytest.mark.parametrize("fail", [False, True])
def test_queue_worker_polls_fixed_hub_routes_and_reports_automatic_result(fail: bool) -> None:
    opener = Opener(
        [
            Response(200, {"data": _assignment()}),
            Response(200, {"data": {"status": "failed" if fail else "completed"}}),
        ]
    )
    worker = SpreadsheetQueueWorker(
        hub_endpoint="http://ai-agent-hub:5000/api/spreadsheet-studio/internal",
        worker_id="spreadsheet-worker",
        worker_token="worker-static-token-at-least-24",
        executor=Executor(fail=fail),
        opener=opener,
    )

    assert worker.run_once() is True

    assert opener.requests[0].full_url.endswith("/internal/jobs/claim")
    assert opener.requests[0].get_header("Authorization") == "Bearer worker-static-token-at-least-24"
    callback = json.loads(opener.requests[1].data)
    assert opener.requests[1].full_url.endswith("/internal/jobs/spreadsheet-job-one/result")
    assert opener.requests[1].get_header("Authorization") == "Bearer callback-capability"
    assert callback["status"] == ("failed" if fail else "completed")
    assert callback["reason_code"] == ("spreadsheet_worker_execution_failed" if fail else None)
    assert "untrusted-detail" not in json.dumps(callback)
    if not fail:
        assert callback["result_digest"] == canonical_digest(callback["result"])
        assert 0 <= callback["result"]["operation_durations_ms"]["render_recalc"] <= 300_000


def test_queue_worker_rejects_ambiguous_hub_endpoint() -> None:
    with pytest.raises(SpreadsheetQueueWorkerError, match="endpoint_invalid"):
        SpreadsheetQueueWorker(
            hub_endpoint="http://ai-agent-hub:5000/api/spreadsheet-studio/internal?target=evil",
            worker_id="spreadsheet-worker",
            worker_token="worker-static-token-at-least-24",
            executor=Executor(),
        )


def test_queue_worker_retries_pending_callback_without_reclaiming() -> None:
    opener = Opener(
        [
            Response(200, {"data": _assignment()}),
            Response(503, {"error": {"code": "temporary"}}),
            Response(200, {"data": {"status": "completed", "replayed": False}}),
        ]
    )
    worker = SpreadsheetQueueWorker(
        hub_endpoint="http://ai-agent-hub:5000/api/spreadsheet-studio/internal",
        worker_id="spreadsheet-worker",
        worker_token="worker-static-token-at-least-24",
        executor=Executor(),
        opener=opener,
    )
    with pytest.raises(SpreadsheetQueueWorkerError, match="callback_rejected"):
        worker.run_once()
    assert worker.run_once() is True
    assert sum(request.full_url.endswith("/jobs/claim") for request in opener.requests) == 1
    assert sum(request.full_url.endswith("/result") for request in opener.requests) == 2


def test_compose_worker_has_no_inbound_port_or_external_network() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker/compose-next/compose.spreadsheet-studio.yml").read_text())
    worker = compose["services"]["spreadsheet-worker"]
    hub = compose["services"]["ai-agent-hub"]

    assert worker["read_only"] is True
    assert worker["user"] == "10007:10007"
    assert worker["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in worker["security_opt"]
    assert "apparmor=docker-default" in worker["security_opt"]
    assert "seccomp=builtin" in worker["security_opt"]
    assert set(worker["networks"]) == {"spreadsheet-control"}
    assert compose["networks"]["spreadsheet-control"]["internal"] is True
    assert "ports" not in worker and "expose" not in worker
    assert "ANANTA_SPREADSHEET_WORKER_URL" not in hub["environment"]
    assert "ANANTA_SPREADSHEET_ALLOWED_ENDPOINTS" not in hub["environment"]
    assert worker["environment"]["ANANTA_SPREADSHEET_NETWORK_ISOLATED"] == "true"
    assert str(worker["environment"]["ANANTA_SPREADSHEET_HUB_ENDPOINT"]).endswith(
        "/api/spreadsheet-studio/internal"
    )

    dockerfile = (root / "docker/compose-next/Dockerfile.spreadsheet-worker").read_text()
    assert 'CMD ["python", "-m", "worker.runtime.spreadsheet_queue_worker"]' in dockerfile
    assert "EXPOSE 8097" not in dockerfile
