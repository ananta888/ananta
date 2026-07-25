from __future__ import annotations

import pytest

from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)


def _service():
    audits: list[tuple[str, dict]] = []
    service = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        audit=lambda event, payload: audits.append((event, payload)),
        clock=lambda: 100.0,
    )
    return service, audits


def test_rollout_precedence_is_global_json_then_profile_then_workspace() -> None:
    service, audits = _service()
    baseline = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
        profile_name="semantic",
    )
    assert baseline.provider == "json"
    assert baseline.source_layers == ("global_json_default",)

    service.set_profile_override(
        domain="codecompass",
        profile_name="semantic",
        override={
            "provider": "qdrant",
            "qdrant": {
                "endpoint": {
                    "rest_url": "http://qdrant:6333",
                    "api_key_ref": "file:///run/secrets/qdrant-api-key",
                }
            },
        },
        expected_revision=0,
        actor="admin-a",
    )
    service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"availability": {"on_unavailable": "fail_fast"}},
        expected_revision=0,
        actor="admin-a",
    )

    resolved = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
        profile_name="semantic",
    )
    assert resolved.provider == "qdrant"
    assert resolved.config["availability"]["on_unavailable"] == "fail_fast"
    assert resolved.source_layers == (
        "global_json_default",
        "profile_override",
        "workspace_override",
    )
    assert all("qdrant-api-key" not in str(payload) for _, payload in audits)


def test_workspace_and_wiki_rollout_are_isolated_and_rollback_to_json() -> None:
    service, _audits = _service()
    record = service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    service.set_workspace_override(
        domain="wiki",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )

    assert service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    ).provider == "qdrant"
    assert service.resolve(
        domain="codecompass",
        workspace_id="workspace-b",
    ).provider == "json"
    assert service.resolve(
        domain="wiki",
        workspace_id="workspace-a",
    ).provider == "qdrant"

    service.rollback(
        layer="workspace",
        domain="codecompass",
        scope_name="workspace-a",
        expected_revision=record.revision,
        actor="admin-a",
    )
    assert service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    ).provider == "json"
    assert service.resolve(
        domain="wiki",
        workspace_id="workspace-a",
    ).provider == "qdrant"


def test_rollout_rejects_plaintext_secrets_and_stale_revision() -> None:
    service, _audits = _service()
    with pytest.raises(ValueError, match="plaintext_secret"):
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override={"qdrant": {"api_key": "do-not-store"}},
            expected_revision=0,
            actor="admin-a",
        )

    service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    with pytest.raises(RuntimeError, match="revision_conflict"):
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override={"provider": "json"},
            expected_revision=0,
            actor="admin-a",
        )
