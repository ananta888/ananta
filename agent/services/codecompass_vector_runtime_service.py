"""Hub composition root for scoped CodeCompass vector-store runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from agent.services.vector_index_task_service import (
    get_vector_index_task_service,
)
from agent.services.vector_store_rollout_service import (
    VectorStoreRolloutService,
    get_vector_store_rollout_service,
)
from worker.retrieval.vector_store_config import (
    VectorStoreConfig,
    VectorStoreProvider,
)
from worker.retrieval.vector_store_contract import VectorScope
from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    SecretResolver,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory
from worker.retrieval.vector_store_observer import VectorStoreObserver

LOCAL_JSON_READ_EXECUTION = "local_json"
HUB_DIRECT_QDRANT_READ_EXECUTION = "hub_direct_qdrant"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class VectorIndexTaskSubmissionPort(Protocol):
    def submit(self, **kwargs: object) -> dict[str, object]: ...


class VectorIndexInputPublisherPort(Protocol):
    def publish(
        self,
        *,
        scope: VectorScope,
        content: bytes,
        content_sha256: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class CodeCompassVectorRuntimeContext:
    vector_store_config: VectorStoreConfig
    trusted_scope: VectorScope
    index_task_service: VectorIndexTaskSubmissionPort | None = None
    secret_resolver: SecretResolver | None = None
    vector_store_factory: VectorStoreFactory | None = None
    observer: VectorStoreObserver | None = None
    index_input_publisher: VectorIndexInputPublisherPort | None = None
    read_execution: str = LOCAL_JSON_READ_EXECUTION

    def __post_init__(self) -> None:
        if self.trusted_scope.domain != "codecompass":
            raise ValueError("codecompass_vector_scope_domain_invalid")
        if self.vector_store_config.provider == VectorStoreProvider.QDRANT and self.index_task_service is None:
            raise ValueError("vector_index_delegation_required")
        if self.vector_store_config.provider == VectorStoreProvider.QDRANT and self.index_input_publisher is None:
            raise ValueError("vector_index_input_publisher_required")
        if (
            self.vector_store_config.provider == VectorStoreProvider.QDRANT
            and self.read_execution != HUB_DIRECT_QDRANT_READ_EXECUTION
        ):
            raise ValueError("vector_store_qdrant_read_execution_not_configured")
        if self.vector_store_config.provider == VectorStoreProvider.QDRANT and self.secret_resolver is None:
            raise ValueError("vector_store_secret_resolver_required")
        if self.vector_store_config.provider == VectorStoreProvider.QDRANT and self.observer is None:
            raise ValueError("vector_store_observer_required")
        if self.vector_store_config.provider == VectorStoreProvider.JSON:
            object.__setattr__(
                self,
                "read_execution",
                LOCAL_JSON_READ_EXECUTION,
            )


class CodeCompassVectorRuntimeResolver(Protocol):
    def resolve(
        self,
        *,
        repo_root: Path,
    ) -> CodeCompassVectorRuntimeContext: ...


class HubCodeCompassVectorRuntimeResolver:
    """Resolve rollout only after a trusted caller supplies all scope IDs."""

    def __init__(
        self,
        *,
        workspace_id: str,
        repository_id: str,
        profile_name: str = "default",
        rollout_service: VectorStoreRolloutService | None = None,
        index_task_service: VectorIndexTaskSubmissionPort | None = None,
        secret_resolver: SecretResolver | None = None,
        vector_store_factory: VectorStoreFactory | None = None,
        observer: VectorStoreObserver | None = None,
        index_input_publisher: VectorIndexInputPublisherPort | None = None,
        allow_hub_qdrant_reads: bool = False,
    ) -> None:
        self._scope = VectorScope(
            workspace_id=workspace_id,
            repository_id=repository_id,
            profile_name=profile_name,
            domain="codecompass",
        )
        self._rollout_service = rollout_service or get_vector_store_rollout_service()
        self._index_task_service = index_task_service
        self._secret_resolver = secret_resolver
        self._vector_store_factory = vector_store_factory
        self._observer = observer
        self._index_input_publisher = index_input_publisher
        if not isinstance(allow_hub_qdrant_reads, bool):
            raise ValueError("codecompass_vector_hub_qdrant_read_boolean_invalid")
        self._allow_hub_qdrant_reads = allow_hub_qdrant_reads

    def cache_signature(self, *, repo_root: Path) -> tuple[str, ...]:
        if not isinstance(repo_root, Path) or not repo_root.is_absolute():
            raise ValueError("codecompass_repo_root_invalid")
        resolved = self._rollout_service.resolve(
            domain="codecompass",
            workspace_id=self._scope.workspace_id,
            profile_name=self._scope.profile_name,
        )
        return (
            str(resolved.config_hash),
            self._scope.workspace_id,
            self._scope.repository_id,
            self._scope.profile_name,
            "hub_direct" if self._allow_hub_qdrant_reads else "delegated_only",
        )

    def resolve(
        self,
        *,
        repo_root: Path,
    ) -> CodeCompassVectorRuntimeContext:
        if not isinstance(repo_root, Path) or not repo_root.is_absolute():
            raise ValueError("codecompass_repo_root_invalid")
        resolved = self._rollout_service.resolve(
            domain="codecompass",
            workspace_id=self._scope.workspace_id,
            profile_name=self._scope.profile_name,
        )
        config = VectorStoreConfig.from_mapping(resolved.config)
        task_service = self._index_task_service
        if config.provider == VectorStoreProvider.QDRANT and task_service is None:
            task_service = get_vector_index_task_service()
        if config.provider == VectorStoreProvider.QDRANT and not self._allow_hub_qdrant_reads:
            raise ValueError("vector_store_qdrant_read_execution_not_configured")
        secret_resolver = self._secret_resolver
        if config.provider == VectorStoreProvider.QDRANT and secret_resolver is None:
            secret_resolver = EnvFileSecretResolver()
        observer = self._observer
        if config.provider == VectorStoreProvider.QDRANT and observer is None:
            from agent.adapters.vector_store_metrics_adapter import (
                PrometheusVectorStoreObserver,
            )

            observer = PrometheusVectorStoreObserver()
        return CodeCompassVectorRuntimeContext(
            vector_store_config=config,
            trusted_scope=self._scope,
            index_task_service=task_service,
            secret_resolver=secret_resolver,
            vector_store_factory=self._vector_store_factory,
            observer=observer,
            index_input_publisher=self._index_input_publisher,
            read_execution=(
                HUB_DIRECT_QDRANT_READ_EXECUTION
                if config.provider == VectorStoreProvider.QDRANT
                else LOCAL_JSON_READ_EXECUTION
            ),
        )


def build_default_codecompass_vector_runtime_resolver(
    *,
    environ: Mapping[str, str] | None = None,
    rollout_service: VectorStoreRolloutService | None = None,
    index_task_service: VectorIndexTaskSubmissionPort | None = None,
    secret_resolver: SecretResolver | None = None,
    vector_store_factory: VectorStoreFactory | None = None,
    observer: VectorStoreObserver | None = None,
    index_input_publisher: VectorIndexInputPublisherPort | None = None,
) -> HubCodeCompassVectorRuntimeResolver | None:
    """Build the production resolver only from explicit trusted scope config."""

    values = environ if environ is not None else os.environ
    workspace_id = str(values.get("ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID") or "").strip()
    repository_id = str(values.get("ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID") or "").strip()
    if not workspace_id and not repository_id:
        return None
    if not workspace_id or not repository_id:
        raise ValueError("codecompass_vector_runtime_scope_incomplete")
    profile_name = str(values.get("ANANTA_CODECOMPASS_VECTOR_PROFILE_NAME") or "default").strip()
    raw_opt_in = str(values.get("ANANTA_CODECOMPASS_VECTOR_HUB_QDRANT_READ_ENABLED") or "false").strip().lower()
    if raw_opt_in in _TRUE_VALUES:
        allow_hub_qdrant_reads = True
    elif raw_opt_in in _FALSE_VALUES:
        allow_hub_qdrant_reads = False
    else:
        raise ValueError("codecompass_vector_hub_qdrant_read_boolean_invalid")
    if index_input_publisher is None:
        from agent.services.vector_index_input_artifact_service import (
            build_vector_index_input_publisher,
        )

        index_input_publisher = build_vector_index_input_publisher(environ=values)
    return HubCodeCompassVectorRuntimeResolver(
        workspace_id=workspace_id,
        repository_id=repository_id,
        profile_name=profile_name,
        rollout_service=rollout_service,
        index_task_service=index_task_service,
        secret_resolver=secret_resolver,
        vector_store_factory=vector_store_factory,
        observer=observer,
        index_input_publisher=index_input_publisher,
        allow_hub_qdrant_reads=allow_hub_qdrant_reads,
    )


__all__ = [
    "CodeCompassVectorRuntimeContext",
    "CodeCompassVectorRuntimeResolver",
    "HUB_DIRECT_QDRANT_READ_EXECUTION",
    "HubCodeCompassVectorRuntimeResolver",
    "LOCAL_JSON_READ_EXECUTION",
    "build_default_codecompass_vector_runtime_resolver",
]
