from __future__ import annotations

from agent.config import settings
from agent.visual_process.node_definitions import NODE_REGISTRY_VERSION


def test_node_definition_registry_requires_authentication(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "visual_process_registry_inspector_enabled", True)

    response = client.get("/api/visual-process/v1/node-definitions")

    assert response.status_code == 401


def test_node_definition_registry_flag_is_fail_closed(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "visual_process_registry_inspector_enabled", False)

    response = client.get(
        "/api/visual-process/v1/node-definitions",
        headers=admin_auth_header,
    )

    assert response.status_code == 404
    assert response.get_json()["error_code"] == "visual_process_registry_inspector_disabled"


def test_node_definition_registry_is_versioned_and_etag_revalidated(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "visual_process_registry_inspector_enabled", True)

    first = client.get(
        "/api/visual-process/v1/node-definitions",
        headers=admin_auth_header,
    )
    payload = first.get_json()
    etag = first.headers["ETag"]
    second = client.get(
        "/api/visual-process/v1/node-definitions",
        headers={**admin_auth_header, "If-None-Match": etag},
    )

    assert first.status_code == 200
    assert payload["schema"] == "ananta.visual_process.node_definition_registry.v1"
    assert payload["registry_version"] == NODE_REGISTRY_VERSION
    assert len(payload["registry_hash"]) == 64
    assert payload["definitions"]
    assert second.status_code == 304
    assert second.headers["ETag"] == etag
    assert "private" in first.headers["Cache-Control"]
