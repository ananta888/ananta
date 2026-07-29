from __future__ import annotations

import pytest

from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreGlobalEnvConfigLoader,
    VectorStoreRolloutService,
)
from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.vector_store_config import VectorStoreConfig
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory


def _service(*, global_config=None):
    audits: list[tuple[str, dict]] = []
    service = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config=global_config,
        audit=lambda event, payload: audits.append((event, payload)),
        clock=lambda: 100.0,
    )
    return service, audits


def test_rollout_precedence_is_global_json_then_profile_then_workspace() -> None:
    service, audits = _service(
        global_config={
            "qdrant": {
                "endpoint": {
                    "rest_url": "https://qdrant:6333",
                    "api_key_ref": "secretfile:///run/secrets/qdrant-api-key",
                    "allowed_origins": ["https://qdrant:6333"],
                    "external_calls_allowed": True,
                }
            }
        }
    )
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


@pytest.mark.parametrize(
    "profile_first",
    [True, False],
)
def test_rollout_rejects_cross_layer_invalid_state_before_persistence(
    profile_first: bool,
) -> None:
    service, audits = _service()
    profile_override = {
        "availability": {
            "fallback_provider": "qdrant",
        }
    }
    workspace_override = {
        "availability": {
            "on_unavailable": "explicit_json_fallback",
        }
    }

    if profile_first:
        service.set_profile_override(
            domain="codecompass",
            profile_name="semantic",
            override=profile_override,
            expected_revision=0,
            actor="admin-a",
        )
        with pytest.raises(
            ValueError,
            match="vector_store_invalid_availability_policy",
        ):
            service.set_workspace_override(
                domain="codecompass",
                workspace_id="workspace-a",
                override=workspace_override,
                expected_revision=0,
                actor="admin-b",
            )
        assert service.get_override(
            layer="workspace",
            domain="codecompass",
            scope_name="workspace-a",
        ) is None
    else:
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override=workspace_override,
            expected_revision=0,
            actor="admin-a",
        )
        with pytest.raises(
            ValueError,
            match="vector_store_invalid_availability_policy",
        ):
            service.set_profile_override(
                domain="codecompass",
                profile_name="semantic",
                override=profile_override,
                expected_revision=0,
                actor="admin-b",
            )
        assert service.get_override(
            layer="profile",
            domain="codecompass",
            scope_name="semantic",
        ) is None

    assert len(audits) == 1
    assert audits[0][0] == "vector_store_override_updated"


def test_workspace_audit_reports_bounded_mixed_provider_set_across_profiles() -> None:
    service, audits = _service()
    service.set_profile_override(
        domain="codecompass",
        profile_name="semantic",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    service.set_profile_override(
        domain="codecompass",
        profile_name="lexical",
        override={"provider": "json"},
        expected_revision=0,
        actor="admin-a",
    )

    service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={
            "availability": {
                "on_unavailable": "fail_fast",
            }
        },
        expected_revision=0,
        actor="admin-b",
    )

    workspace_audit = [
        payload
        for event, payload in audits
        if event == "vector_store_override_updated"
        and payload["layer"] == "workspace"
    ][-1]
    assert workspace_audit["previous_provider"] == "mixed"
    assert workspace_audit["new_provider"] == "mixed"
    assert service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
        profile_name="semantic",
    ).provider == "qdrant"
    assert service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
        profile_name="lexical",
    ).provider == "json"


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
    wiki_resolved = service.resolve(
        domain="wiki",
        workspace_id="workspace-a",
    )
    assert wiki_resolved.provider == "qdrant"
    assert (
        wiki_resolved.config["qdrant"]["collection_prefix"]
        == "ananta-wiki"
    )

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


def test_wiki_rollout_rejects_codecompass_collection_prefix() -> None:
    service, _audits = _service()

    with pytest.raises(
        ValueError,
        match="wiki_qdrant_collection_prefix_must_be_separate",
    ):
        service.set_workspace_override(
            domain="wiki",
            workspace_id="workspace-a",
            override={
                "provider": "qdrant",
                "qdrant": {
                    "collection_prefix": "ananta-codecompass"
                },
            },
            expected_revision=0,
            actor="admin-a",
        )


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


def test_rollout_uses_production_parser_and_immutable_hub_security_policy() -> None:
    service, _audits = _service(
        global_config={
            "qdrant": {
                "endpoint": {
                    "rest_url": "https://qdrant:6333",
                    "allowed_origins": [
                        "https://qdrant:6333",
                        "https://qdrant-backup:6333",
                    ],
                    "external_calls_allowed": True,
                    "api_key_ref": "env://QDRANT_API_KEY",
                }
            }
        }
    )
    with pytest.raises(
        ValueError,
        match="security_policy_override_forbidden",
    ):
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override={
                "provider": "qdrant",
                "qdrant": {
                    "endpoint": {
                        "allowed_origins": ["https://attacker.invalid:443"],
                        "external_calls_allowed": True,
                    }
                },
            },
            expected_revision=0,
            actor="admin-a",
        )
    with pytest.raises(
        ValueError,
        match="security_policy_override_forbidden",
    ):
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override={
                "provider": "qdrant",
                "qdrant": {
                    "endpoint": {
                        "trusted_private_origins": [
                            "http://attacker.invalid:6333"
                        ],
                    }
                },
            },
            expected_revision=0,
            actor="admin-a",
        )
    with pytest.raises(ValueError, match="vector_store_invalid_collection"):
        service.set_workspace_override(
            domain="codecompass",
            workspace_id="workspace-a",
            override={
                "provider": "qdrant",
                "qdrant": {"collection_prefix": "../invalid"},
            },
            expected_revision=0,
            actor="admin-a",
        )

    service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={
            "provider": "qdrant",
            "qdrant": {
                "endpoint": {
                    "rest_url": "https://qdrant-backup:6333",
                }
            },
        },
        expected_revision=0,
        actor="admin-a",
    )
    resolved = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    )

    assert resolved.provider == "qdrant"
    assert resolved.config["qdrant"]["endpoint"]["allowed_origins"] == [
        "https://qdrant-backup:6333",
        "https://qdrant:6333",
    ]
    assert (
        resolved.config["qdrant"]["endpoint"]["api_key_ref"]
        == "env://QDRANT_API_KEY"
    )


@pytest.mark.parametrize("layer", ["profile", "workspace"])
def test_rollout_forbids_json_index_path_override_but_preserves_global_path(
    layer: str,
) -> None:
    service, _audits = _service(
        global_config={"json": {"index_path": "/hub-owned/vector-index.json"}}
    )
    setter = (
        service.set_profile_override
        if layer == "profile"
        else service.set_workspace_override
    )
    scope = (
        {"profile_name": "default"}
        if layer == "profile"
        else {"workspace_id": "workspace-a"}
    )

    with pytest.raises(ValueError, match="json_index_path_override_forbidden"):
        setter(
            domain="codecompass",
            override={"json": {"index_path": "/etc/passwd"}},
            expected_revision=0,
            actor="admin-a",
            **scope,
        )

    resolved = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    )
    assert (
        resolved.config["json"]["index_path"]
        == "/hub-owned/vector-index.json"
    )


def test_resolved_hash_excludes_all_hub_secret_reference_locators() -> None:
    def config(api_key_ref: str, tls_ca_cert_ref: str):
        return {
            "qdrant": {
                "endpoint": {
                    "rest_url": "https://localhost:6333",
                    "allowed_origins": ["https://localhost:6333"],
                    "api_key_ref": api_key_ref,
                    "tls_ca_cert_ref": tls_ca_cert_ref,
                }
            }
        }

    first, _ = _service(
        global_config=config(
            "env://QDRANT_KEY_A",
            "secretfile:///run/secrets/qdrant-ca-a.pem",
        )
    )
    second, _ = _service(
        global_config=config(
            "env://QDRANT_KEY_B",
            "secretfile:///run/secrets/qdrant-ca-b.pem",
        )
    )

    assert first.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    ).config_hash == second.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    ).config_hash


def test_hub_env_loader_supports_exact_internal_qdrant_policy() -> None:
    global_config = VectorStoreGlobalEnvConfigLoader(
        {
            "ANANTA_QDRANT_REST_URL": "https://qdrant:6333",
            "ANANTA_QDRANT_ALLOWED_ORIGINS": "https://qdrant:6333",
            "ANANTA_QDRANT_TRUSTED_PRIVATE_ORIGINS": "https://qdrant:6333",
            "ANANTA_QDRANT_API_KEY_REF": (
                "secretfile:///run/secrets/qdrant-api-key"
            ),
            "ANANTA_QDRANT_TLS_CA_CERT_REF": (
                "secretfile:///run/secrets/qdrant-tls-ca.pem"
            ),
            "ANANTA_QDRANT_EXTERNAL_CALLS_ALLOWED": "false",
            "ANANTA_QDRANT_CONNECT_TIMEOUT_SECONDS": "2",
            "ANANTA_QDRANT_REQUEST_TIMEOUT_SECONDS": "8",
        }
    ).load()
    service, _audits = _service(global_config=global_config)
    service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )

    endpoint = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    ).config["qdrant"]["endpoint"]
    assert endpoint["rest_url"] == "https://qdrant:6333"
    assert endpoint["trusted_private_origins"] == ["https://qdrant:6333"]
    assert endpoint["tls_ca_cert_ref"].endswith("/qdrant-tls-ca.pem")
    assert endpoint["external_calls_allowed"] is False
    assert endpoint["connect_timeout_seconds"] == 2.0
    assert endpoint["request_timeout_seconds"] == 8.0


def test_hub_env_loader_rejects_ambiguous_boolean() -> None:
    with pytest.raises(
        ValueError,
        match="vector_store_environment_boolean_invalid",
    ):
        VectorStoreGlobalEnvConfigLoader(
            {"ANANTA_QDRANT_EXTERNAL_CALLS_ALLOWED": "sometimes"}
        ).load()


def test_override_audit_records_effective_provider_policy_and_time() -> None:
    service, audits = _service()
    profile = service.set_profile_override(
        domain="codecompass",
        profile_name="default",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )
    workspace = service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"availability": {"on_unavailable": "fail_fast"}},
        expected_revision=0,
        actor="admin-a",
    )
    service.rollback(
        layer="workspace",
        domain="codecompass",
        scope_name="workspace-a",
        expected_revision=workspace.revision,
        actor="admin-a",
    )
    service.rollback(
        layer="profile",
        domain="codecompass",
        scope_name="default",
        expected_revision=profile.revision,
        actor="admin-a",
    )

    updated = [
        payload
        for event, payload in audits
        if event == "vector_store_override_updated"
    ]
    rolled_back = [
        payload
        for event, payload in audits
        if event == "vector_store_override_rolled_back"
    ]
    assert (updated[0]["previous_provider"], updated[0]["new_provider"]) == (
        "json",
        "qdrant",
    )
    assert (updated[1]["previous_provider"], updated[1]["new_provider"]) == (
        "mixed",
        "mixed",
    )
    assert updated[0]["policy_decision"] == "override_allowed"
    assert rolled_back[-1]["previous_provider"] == "qdrant"
    assert rolled_back[-1]["new_provider"] == "json"
    assert rolled_back[-1]["policy_decision"] == "rollback_allowed"
    assert all(payload["occurred_at"] == 100.0 for payload in updated + rolled_back)
    assert all("scope_name" not in payload for payload in updated + rolled_back)


def test_workspace_rollback_builds_compatible_json_with_trace_and_scope(
    tmp_path,
) -> None:
    index_path = tmp_path / "rollback-index.json"
    compatibility = CompatibilitySpec(
        dimensions=2,
        provider="local-hash",
        model="rollback-v1",
        profile="default",
        encoding="float32",
        config_hash="config-v1",
        manifest_hash="manifest-v1",
    )
    scope_a = VectorScope("workspace-a", "repository-a")
    scope_b = VectorScope("workspace-b", "repository-b")
    seed = JsonVectorStore(index_path=index_path)
    seed.rebuild(
        [
            PreparedVectorPoint(
                record_id="workspace-a-result",
                vector=(1.0, 0.0),
                scope=scope_a,
                payload={"kind": "code"},
                source_hash="source-a",
            ),
            PreparedVectorPoint(
                record_id="workspace-b-result",
                vector=(1.0, 0.0),
                scope=scope_b,
                payload={"kind": "code"},
                source_hash="source-b",
            ),
        ],
        compatibility=compatibility,
    )
    service, audits = _service(
        global_config={"json": {"index_path": str(index_path)}}
    )
    override = service.set_workspace_override(
        domain="codecompass",
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

    service.rollback(
        layer="workspace",
        domain="codecompass",
        scope_name="workspace-a",
        expected_revision=override.revision,
        actor="admin-a",
    )
    resolved = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    )
    store = VectorStoreFactory().create(
        VectorStoreConfig.from_mapping(resolved.config)
    )
    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0),
            top_k=10,
            scope=scope_a,
            compatibility=compatibility,
        )
    )

    assert resolved.provider == "json"
    assert resolved.source_layers == ("global_json_default",)
    assert [hit.record_id for hit in result.hits] == [
        "workspace-a-result"
    ]
    assert all(
        {
            "workspace_id": hit.payload["workspace_id"],
            "repository_id": hit.payload["repository_id"],
            "profile_name": hit.payload["profile_name"],
            "domain": hit.payload["domain"],
        }
        == scope_a.as_dict()
        for hit in result.hits
    )
    assert result.requested_provider == "json"
    assert result.effective_provider == "json"
    assert result.provider_fallback is False
    assert result.reason == "ok"
    rollback_audit = [
        payload
        for event, payload in audits
        if event == "vector_store_override_rolled_back"
    ][-1]
    assert (
        rollback_audit["previous_provider"],
        rollback_audit["new_provider"],
    ) == ("qdrant", "json")


def test_workspace_rollback_to_incompatible_json_state_fails_closed(
    tmp_path,
) -> None:
    index_path = tmp_path / "incompatible-rollback-index.json"
    persisted_compatibility = CompatibilitySpec(
        dimensions=2,
        provider="local-hash",
        model="rollback-v1",
        profile="default",
        encoding="float32",
        config_hash="config-v1",
        manifest_hash="manifest-v1",
    )
    expected_compatibility = CompatibilitySpec(
        dimensions=2,
        provider="local-hash",
        model="rollback-v2",
        profile="default",
        encoding="float32",
        config_hash="config-v2",
        manifest_hash="manifest-v2",
    )
    scope = VectorScope("workspace-a", "repository-a")
    seed = JsonVectorStore(index_path=index_path)
    seed.rebuild(
        [
            PreparedVectorPoint(
                record_id="stale-result",
                vector=(1.0, 0.0),
                scope=scope,
                payload={"kind": "code"},
                source_hash="source-a",
            )
        ],
        compatibility=persisted_compatibility,
    )
    service, _audits = _service(
        global_config={"json": {"index_path": str(index_path)}}
    )
    override = service.set_workspace_override(
        domain="codecompass",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )

    service.rollback(
        layer="workspace",
        domain="codecompass",
        scope_name="workspace-a",
        expected_revision=override.revision,
        actor="admin-a",
    )
    resolved = service.resolve(
        domain="codecompass",
        workspace_id="workspace-a",
    )
    store = VectorStoreFactory().create(
        VectorStoreConfig.from_mapping(resolved.config)
    )
    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0),
            top_k=10,
            scope=scope,
            compatibility=expected_compatibility,
        )
    )

    assert resolved.provider == "json"
    assert result.hits == ()
    assert result.reason == "fallback_state_incompatible"
    assert result.diagnostics["compatibility_reason"] == "provider_changed"
    assert result.requested_provider == "json"
    assert result.effective_provider == "json"
    assert result.provider_fallback is False
