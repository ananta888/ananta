from __future__ import annotations

import pytest

from agent.services.dendritic_memory_worker_port import normalize_dendritic_worker_endpoint
from tests.dendritic_memory.helpers import assignment
from worker.runtime.dendritic_memory_app import create_app


class _Runner:
    def run(self, *, job, records=(), packs=(), cancelled=lambda: False):
        del records, packs, cancelled
        return {
            "run_id": job["run_id"],
            "attempt_id": job["attempt_id"],
            "fencing_token": job["fencing_token"],
            "state": "failed",
            "reason_code": "dendritic_worker_test_failure",
            "event_count": 0,
            "artifact": None,
            "manifest": None,
            "output": None,
            "checkpoint": None,
            "schema": "ananta.dendritic-memory-worker-result.v1",
        }


def test_worker_http_surface_requires_auth_and_closed_envelope() -> None:
    token = "worker-token-that-is-long-enough"
    app = create_app(runner=_Runner(), bearer_token=token, capability={"available": True})
    client = app.test_client()
    assert client.post("/internal/v1/dendritic-memory/jobs", json={}).status_code == 401
    response = client.post(
        "/internal/v1/dendritic-memory/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json={"assignment": assignment(), "records": [], "packs": []},
    )
    assert response.status_code == 200
    assert response.get_json()["reason_code"] == "dendritic_worker_test_failure"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://worker:8095/internal/v1/dendritic-memory",
        "http://127.0.0.1:8095/internal/v1/dendritic-memory",
        "http://worker:8095/internal/v1/dendritic-memory/jobs",
        "http://user:secret@worker:8095/internal/v1/dendritic-memory",
        "http://worker:8095/internal/v1/dendritic-memory?proxy=http://evil.test",
    ],
)
def test_hub_worker_endpoint_rejects_non_exact_or_unsafe_urls(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint_invalid"):
        normalize_dendritic_worker_endpoint(endpoint)


def test_hub_worker_endpoint_accepts_exact_internal_container_url() -> None:
    assert normalize_dendritic_worker_endpoint(
        "http://dendritic-worker:8095/internal/v1/dendritic-memory/"
    ) == "http://dendritic-worker:8095/internal/v1/dendritic-memory"
