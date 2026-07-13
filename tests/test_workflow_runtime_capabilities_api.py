from __future__ import annotations

from agent.auth import generate_token
from agent.config import settings


def _headers() -> dict[str, str]:
    token = generate_token(
        {"sub": "runtime-operator", "role": "user", "tenant_id": "tenant-a"},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_capability_projection_requires_auth_and_contains_all_runtimes(client):
    assert client.get("/api/workflow-runtime/capabilities").status_code == 401

    response = client.get("/api/workflow-runtime/capabilities", headers=_headers())

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.workflow_runtime_capability_matrix.v1"
    assert {item["runtime_id"] for item in payload["runtimes"]} == {
        "ananta-native",
        "langgraph",
        "temporal",
    }


def test_capability_projection_evaluates_repeated_requirements(client):
    response = client.get(
        "/api/workflow-runtime/capabilities"
        "?required_capability=resume&required_capability=durability",
        headers=_headers(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["required_capabilities"] == ["durability", "resume"]
    by_id = {item["runtime_id"]: item for item in payload["runtimes"]}
    assert by_id["temporal"]["selection"] == {
        "state": "blocked",
        "reason_code": "runtime_health_not_observed",
        "missing_capabilities": [],
    }
    assert by_id["ananta-native"]["selection"] == {
        "state": "incompatible",
        "reason_code": "runtime_capabilities_missing",
        "missing_capabilities": ["durability"],
    }


def test_capability_projection_rejects_unknown_or_oversized_queries(client):
    unknown = client.get(
        "/api/workflow-runtime/capabilities?runtime=temporal",
        headers=_headers(),
    )
    oversized = client.get(
        "/api/workflow-runtime/capabilities?required_capability=" + ("x" * 129),
        headers=_headers(),
    )

    assert unknown.status_code == 400
    assert unknown.get_json()["reason_code"] == "runtime_capability_query_forbidden"
    assert oversized.status_code == 400
    assert oversized.get_json()["reason_code"] == "runtime_capability_query_invalid"
