from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.vector_store_control import vector_store_control_bp
from agent.services.vector_index_preparation_policy import (
    DeploymentVectorIndexPreparationPolicy,
    VectorIndexPreparationPolicyConfigurationError,
)
from agent.services.vector_index_task_service import (
    VectorIndexTaskService,
    VectorIndexTrustedScope,
)
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_embedding_egress_policy import (
    VectorIndexEmbeddingEgressPolicyError,
    WorkerEmbeddingEgressPolicy,
)
from worker.retrieval.vector_index_execution import (
    ConfiguredVectorIndexExecution,
)
from worker.retrieval.vector_index_preparation import (
    CODECOMPASS_DOCUMENTS,
    VECTOR_INDEX_PREPARATION_SCHEMA,
    TaskEmbeddingProviderFactory,
    VectorIndexPreparationService,
    VectorIndexPreparationSpec,
)

_BASE_URL = "https://embeddings.example.test/v1"


def _embedding() -> dict:
    return {
        "provider": "openai_compatible",
        "provider_id": "openai_compatible",
        "policy_profile": "approved-external",
        "model": "embedding-a",
        "model_version": "embedding-a",
        "dimensions": 2,
        "base_url": _BASE_URL,
        "api_key_ref": "env://ANANTA_EMBEDDING_API_KEY",
        "timeout_seconds": 20,
        "external_calls_allowed": True,
        "allowed_base_urls": [_BASE_URL],
    }


def _preparation(embedding: dict | None = None) -> dict:
    return {
        "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
        "kind": CODECOMPASS_DOCUMENTS,
        "embedding": embedding or _embedding(),
        "embedding_text_profile": ("codecompass-symbol-path-summary-v1"),
    }


def _policy() -> DeploymentVectorIndexPreparationPolicy:
    embedding = _embedding()
    embedding.pop("policy_profile")
    return DeploymentVectorIndexPreparationPolicy(
        {
            "approved-external": {
                "domains": ["codecompass"],
                "embedding": embedding,
            }
        }
    )


def _payload(embedding: dict | None = None) -> dict:
    scope = VectorIndexTrustedScope(
        workspace_id="workspace-a",
        repository_id="repo-a",
    )
    return {
        "input_ref": VectorIndexArtifactLocator.locate(
            scope=scope,
            content_sha256="a" * 64,
        ).to_reference(),
        "preparation": _preparation(embedding),
        "compatibility": {
            "dimensions": 2,
            "distance": "cosine",
            "provider": "openai_compatible",
            "model": "embedding-a",
            "profile": "codecompass-symbol-path-summary-v1",
            "encoding": "float32",
            "config_hash": "config-a",
            "schema_version": "vector_store.v1",
            "manifest_hash": "manifest-a",
        },
    }


def test_local_hash_remains_offline_without_deployment_profile() -> None:
    preparation = _preparation(
        {
            "provider": "local_hash",
            "provider_id": "local_hash",
            "model_version": "hash-v1",
            "dimensions": 2,
            "timeout_seconds": 20,
            "external_calls_allowed": False,
            "allowed_base_urls": [],
        }
    )

    result = DeploymentVectorIndexPreparationPolicy().authorize(
        preparation=preparation,
        trusted_domain="codecompass",
    )

    assert result is not None
    assert result["embedding"]["provider"] == "local_hash"
    assert result["embedding"]["external_calls_allowed"] is False


def test_hub_policy_allows_exact_deployment_profile() -> None:
    result = _policy().authorize(
        preparation=_preparation(),
        trusted_domain="codecompass",
    )

    assert result is not None
    assert result["embedding"] == _embedding()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(external_calls_allowed=False),
        lambda value: value.update(
            base_url="https://exfil.example.test/v1",
            allowed_base_urls=["https://exfil.example.test/v1"],
        ),
        lambda value: value.update(
            allowed_base_urls=[
                _BASE_URL,
                "https://exfil.example.test/v1",
            ]
        ),
        lambda value: value.update(api_key_ref="env://ANANTA_OTHER_SECRET"),
    ],
)
def test_hub_policy_rejects_caller_expansion_with_stable_code(
    mutate,
) -> None:
    embedding = _embedding()
    mutate(embedding)

    with pytest.raises(
        ValueError,
        match="^vector_index_embedding_policy_forbidden$",
    ):
        _policy().authorize(
            preparation=_preparation(embedding),
            trusted_domain="codecompass",
        )


def test_hub_policy_requires_exact_normalized_deployment_profile() -> None:
    profile = _embedding()
    profile.pop("policy_profile")
    profile["provider"] = "openai"

    with pytest.raises(
        VectorIndexPreparationPolicyConfigurationError,
        match="profile_not_normalized",
    ):
        DeploymentVectorIndexPreparationPolicy(
            {
                "approved-external": {
                    "domains": ["codecompass"],
                    "embedding": profile,
                }
            }
        )


class _Repository:
    def get_by_id(self, _task_id):
        return None

    def get_all(self):
        return []


class _RejectingQueue:
    def __init__(self) -> None:
        self.calls = 0

    def ingest_task(self, **_kwargs):
        self.calls += 1
        raise AssertionError("embedding policy denial must precede persistence")


def _task_service(
    preparation_policy=None,
) -> tuple[VectorIndexTaskService, _RejectingQueue]:
    queue = _RejectingQueue()
    return (
        VectorIndexTaskService(
            task_queue=queue,
            task_repository=_Repository(),
            preparation_policy=(
                preparation_policy if preparation_policy is not None else DeploymentVectorIndexPreparationPolicy()
            ),
            audit=lambda *_args: None,
        ),
        queue,
    )


def test_direct_task_attack_is_denied_before_persistence() -> None:
    service, queue = _task_service()

    with pytest.raises(
        ValueError,
        match="^vector_index_embedding_policy_forbidden$",
    ):
        service.submit(
            operation="refresh",
            trusted_scope=VectorIndexTrustedScope(
                workspace_id="workspace-a",
                repository_id="repo-a",
            ),
            idempotency_key="external-attack-request",
            payload=_payload(),
            actor="admin-a",
        )

    assert queue.calls == 0


def test_direct_task_cannot_expand_configured_profile() -> None:
    service, queue = _task_service(_policy())
    embedding = _embedding()
    embedding.update(
        base_url="https://exfil.example.test/v1",
        allowed_base_urls=["https://exfil.example.test/v1"],
    )

    with pytest.raises(
        ValueError,
        match="^vector_index_embedding_policy_forbidden$",
    ):
        service.submit(
            operation="refresh",
            trusted_scope=VectorIndexTrustedScope(
                workspace_id="workspace-a",
                repository_id="repo-a",
            ),
            idempotency_key="external-expand-request",
            payload=_payload(embedding),
            actor="admin-a",
        )

    assert queue.calls == 0


def test_route_attack_returns_stable_policy_denial(monkeypatch) -> None:
    service, queue = _task_service()
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vector_store_control_bp)
    token = generate_token(
        {"sub": "operator-a", "role": "system_admin"},
        settings.secret_key,
    )

    response = app.test_client().post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "refresh",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "external-route-attack",
            "payload": _payload(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    assert response.get_json()["reason_code"] == ("vector_index_embedding_policy_forbidden")
    assert queue.calls == 0


class _SecretResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, _reference):
        self.calls += 1
        return "resolved-secret"


def test_worker_denial_precedes_secret_and_provider_build(
    monkeypatch,
) -> None:
    resolver = _SecretResolver()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        "worker.retrieval.vector_index_preparation.build_embedding_provider",
        lambda config: provider_calls.append(config),
    )
    factory = TaskEmbeddingProviderFactory(
        secret_resolver=resolver,
        egress_policy=WorkerEmbeddingEgressPolicy(),
    )
    spec = VectorIndexPreparationSpec.from_mapping(_preparation())

    with pytest.raises(VectorIndexEmbeddingEgressPolicyError):
        factory.create(spec)

    assert resolver.calls == 0
    assert provider_calls == []


def test_worker_allows_explicit_deployment_egress_profile(
    monkeypatch,
) -> None:
    resolver = _SecretResolver()
    provider = SimpleNamespace(
        provider_id="openai_compatible",
        model_version="embedding-a",
        dimensions=2,
    )
    provider_configs: list[dict] = []

    def build(config):
        provider_configs.append(config)
        return provider

    monkeypatch.setattr(
        "worker.retrieval.vector_index_preparation.build_embedding_provider",
        build,
    )
    factory = TaskEmbeddingProviderFactory(
        secret_resolver=resolver,
        egress_policy=WorkerEmbeddingEgressPolicy((_BASE_URL,)),
    )

    assert factory.create(VectorIndexPreparationSpec.from_mapping(_preparation())) is provider
    assert resolver.calls == 1
    assert provider_configs[0]["api_key"] == "resolved-secret"
    assert provider_configs[0]["follow_redirects"] is False
    assert "api_key_ref" not in provider_configs[0]
    assert "policy_profile" not in provider_configs[0]


class _InputLoader:
    def __init__(self) -> None:
        self.validate_calls = 0
        self.load_calls = 0

    def validate_reference(self, *_args, **_kwargs):
        self.validate_calls += 1

    def load_document_input(self, *_args, **_kwargs):
        self.load_calls += 1
        raise AssertionError("denied preparation must not load input")


class _StoreFactory:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("denied preparation must not create a store")


def test_worker_execution_denial_precedes_store_and_network(
    monkeypatch,
) -> None:
    resolver = _SecretResolver()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        "worker.retrieval.vector_index_preparation.build_embedding_provider",
        lambda config: provider_calls.append(config),
    )
    input_loader = _InputLoader()
    store_factory = _StoreFactory()
    preparation_service = VectorIndexPreparationService(
        provider_factory=TaskEmbeddingProviderFactory(
            secret_resolver=resolver,
            egress_policy=WorkerEmbeddingEgressPolicy(),
        )
    )
    execution = ConfiguredVectorIndexExecution(
        factory=store_factory,
        input_loader=input_loader,
        preparation_service=preparation_service,
    )

    result = execution.execute(
        operation="refresh",
        scope={
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "profile_name": "default",
            "domain": "codecompass",
        },
        resolved_config={},
        payload=_payload(),
        idempotency_key="external-worker-attack",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == ("vector_index_embedding_policy_forbidden")
    assert input_loader.validate_calls == 1
    assert input_loader.load_calls == 0
    assert store_factory.calls == 0
    assert resolver.calls == 0
    assert provider_calls == []
