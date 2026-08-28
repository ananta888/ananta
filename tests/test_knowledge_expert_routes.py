from __future__ import annotations


def test_knowledge_expert_snapshot_is_default_off(client, admin_auth_header) -> None:
    response = client.get("/api/knowledge-experts", headers=admin_auth_header)

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["schema"] == "ananta.knowledge-expert-control-snapshot.v1"
    assert data["enabled"] is False
    assert data["rollout_state"] == "off"
    assert data["fallback_mode"] == "rag_only"


def test_knowledge_expert_decision_ignores_client_authority(client, admin_auth_header) -> None:
    response = client.post(
        "/api/knowledge-experts/decisions",
        headers=admin_auth_header,
        json={
            "schema": "ananta.knowledge-augmentation-request.v1",
            "profile_id": "rag-default",
            "expert_id": "client-selected-expert",
        },
    )

    assert response.status_code == 422
    assert response.get_json()["data"]["reason_code"] == (
        "knowledge_augmentation_client_authority_denied"
    )
