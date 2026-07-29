"""Hub composition root for independently rolled out Wiki vector storage."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from agent.services.vector_index_task_service import (
    VectorIndexTrustedScope,
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
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    VectorScope,
)
from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    SecretResolver,
)
from worker.retrieval.vector_store_observer import VectorStoreObserver
from worker.retrieval.wiki_vector_store import (
    WIKI_EMBEDDING_PROFILE,
    WIKI_VECTOR_PAYLOAD_SCHEMA,
    WikiVectorStoreConfig,
)

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


class VectorIndexCompatibilityStatePort(Protocol):
    def get_latest_completed_compatibility_state(
        self,
        *,
        trusted_scope: VectorIndexTrustedScope,
    ) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class WikiVectorRuntimeContext:
    vector_store_config: WikiVectorStoreConfig
    index_task_service: VectorIndexTaskSubmissionPort | None = None
    index_input_publisher: VectorIndexInputPublisherPort | None = None
    secret_resolver: SecretResolver | None = None
    observer: VectorStoreObserver | None = None
    allow_hub_qdrant_reads: bool = False

    def __post_init__(self) -> None:
        if self.vector_store_config.provider != "qdrant":
            return
        if not self.allow_hub_qdrant_reads:
            raise ValueError("wiki_vector_hub_qdrant_read_not_enabled")
        if self.index_task_service is None:
            raise ValueError("vector_index_delegation_required")
        if self.index_input_publisher is None:
            raise ValueError("vector_index_input_publisher_required")
        if self.secret_resolver is None:
            raise ValueError("vector_store_secret_resolver_required")
        if self.observer is None:
            raise ValueError("vector_store_observer_required")


class HubWikiVectorRuntimeResolver:
    """Resolve Wiki independently while retaining Hub policy ownership."""

    def __init__(
        self,
        *,
        workspace_id: str,
        source_id: str,
        profile_name: str = "default",
        retrieval_cache_state: str = "",
        manifest_hash: str = "",
        rollout_service: VectorStoreRolloutService | None = None,
        index_task_service: VectorIndexTaskSubmissionPort | None = None,
        index_input_publisher: VectorIndexInputPublisherPort | None = None,
        compatibility_state_reader: (VectorIndexCompatibilityStatePort | None) = None,
        secret_resolver: SecretResolver | None = None,
        observer: VectorStoreObserver | None = None,
        allow_hub_qdrant_reads: bool = False,
    ) -> None:
        self._scope = VectorScope(
            workspace_id=workspace_id,
            repository_id=source_id,
            profile_name=profile_name,
            domain="wiki",
        )
        self._retrieval_cache_state = str(retrieval_cache_state or "")
        self._manifest_hash = str(manifest_hash or "")
        self._rollout = rollout_service or get_vector_store_rollout_service()
        self._task_service = index_task_service
        self._publisher = index_input_publisher
        self._compatibility_state_reader = compatibility_state_reader
        self._secret_resolver = secret_resolver
        self._observer = observer
        if not isinstance(allow_hub_qdrant_reads, bool):
            raise ValueError("wiki_vector_hub_qdrant_read_boolean_invalid")
        self._allow_hub_qdrant_reads = allow_hub_qdrant_reads

    def cache_signature(self) -> tuple[str, ...]:
        resolved = self._rollout.resolve(
            domain="wiki",
            workspace_id=self._scope.workspace_id,
            profile_name=self._scope.profile_name,
        )
        common = VectorStoreConfig.from_mapping(resolved.config)
        (
            retrieval_cache_state,
            manifest_hash,
            _expected_compatibility,
            state_signature,
        ) = self._compatibility_state(include_completed=(common.provider == VectorStoreProvider.QDRANT))
        return (
            resolved.config_hash,
            self._scope.workspace_id,
            self._scope.repository_id,
            self._scope.profile_name,
            retrieval_cache_state,
            manifest_hash,
            *state_signature,
            ("hub_direct" if self._allow_hub_qdrant_reads else "delegated_only"),
        )

    def resolve(self) -> WikiVectorRuntimeContext:
        resolved = self._rollout.resolve(
            domain="wiki",
            workspace_id=self._scope.workspace_id,
            profile_name=self._scope.profile_name,
        )
        common = VectorStoreConfig.from_mapping(resolved.config)
        (
            retrieval_cache_state,
            manifest_hash,
            expected_compatibility,
            _state_signature,
        ) = self._compatibility_state(include_completed=(common.provider == VectorStoreProvider.QDRANT))
        if common.provider == VectorStoreProvider.JSON:
            return WikiVectorRuntimeContext(
                vector_store_config=WikiVectorStoreConfig(
                    provider="json",
                    workspace_id=self._scope.workspace_id,
                    source_id=self._scope.repository_id,
                    profile_name=self._scope.profile_name,
                    retrieval_cache_state=retrieval_cache_state,
                    manifest_hash=manifest_hash,
                    expected_compatibility=expected_compatibility,
                    availability=common.availability,
                    json=common.json,
                )
            )
        if common.qdrant is None:
            raise ValueError("wiki_qdrant_config_required")
        if not common.qdrant.collection_prefix.startswith("ananta-wiki"):
            raise ValueError("wiki_qdrant_collection_prefix_must_be_separate")
        task_service = self._task_service or get_vector_index_task_service()
        secret_resolver = self._secret_resolver or EnvFileSecretResolver()
        observer = self._observer
        if observer is None:
            from agent.adapters.vector_store_metrics_adapter import (
                PrometheusVectorStoreObserver,
            )

            observer = PrometheusVectorStoreObserver()
        return WikiVectorRuntimeContext(
            vector_store_config=WikiVectorStoreConfig(
                provider="qdrant",
                qdrant_enabled=True,
                collection_prefix=common.qdrant.collection_prefix,
                workspace_id=self._scope.workspace_id,
                source_id=self._scope.repository_id,
                profile_name=self._scope.profile_name,
                retrieval_cache_state=retrieval_cache_state,
                manifest_hash=manifest_hash,
                expected_compatibility=expected_compatibility,
                qdrant=common.qdrant,
                availability=common.availability,
                json=common.json,
            ),
            index_task_service=task_service,
            index_input_publisher=self._publisher,
            secret_resolver=secret_resolver,
            observer=observer,
            allow_hub_qdrant_reads=self._allow_hub_qdrant_reads,
        )

    def _compatibility_state(
        self,
        *,
        include_completed: bool,
    ) -> tuple[
        str,
        str,
        CompatibilitySpec | None,
        tuple[str, ...],
    ]:
        if not include_completed:
            return (
                self._retrieval_cache_state,
                self._manifest_hash,
                None,
                ("static",),
            )
        reader = self._compatibility_state_reader
        if reader is None:
            candidate = self._task_service
            if candidate is None:
                candidate = get_vector_index_task_service()
            if callable(
                getattr(
                    candidate,
                    "get_latest_completed_compatibility_state",
                    None,
                )
            ):
                reader = candidate  # type: ignore[assignment]
        if reader is None:
            return (
                self._retrieval_cache_state,
                self._manifest_hash,
                None,
                ("static",),
            )
        state = reader.get_latest_completed_compatibility_state(
            trusted_scope=VectorIndexTrustedScope(**self._scope.as_dict())
        )
        if state is None:
            return (
                self._retrieval_cache_state,
                self._manifest_hash,
                None,
                ("static",),
            )
        if str(state.get("scope_fingerprint") or "") != VectorIndexTrustedScope(**self._scope.as_dict()).fingerprint():
            raise RuntimeError("wiki_vector_compatibility_scope_mismatch")
        raw_compatibility = state.get("compatibility")
        if not isinstance(raw_compatibility, Mapping):
            raise RuntimeError("wiki_vector_compatibility_state_invalid")
        try:
            compatibility = CompatibilitySpec(**dict(raw_compatibility))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("wiki_vector_compatibility_state_invalid") from exc
        if (
            compatibility.profile != WIKI_EMBEDDING_PROFILE
            or compatibility.schema_version != WIKI_VECTOR_PAYLOAD_SCHEMA
            or compatibility.encoding != "float32"
        ):
            raise RuntimeError("wiki_vector_compatibility_state_invalid")
        retrieval_cache_state = str(state.get("retrieval_cache_state") or "").strip()
        return (
            retrieval_cache_state,
            compatibility.manifest_hash,
            compatibility,
            (
                "completed",
                str(state.get("state_version") or ""),
                str(state.get("job_id") or ""),
            ),
        )


def build_default_wiki_vector_runtime_resolver(
    *,
    environ: Mapping[str, str] | None = None,
    rollout_service: VectorStoreRolloutService | None = None,
    index_task_service: VectorIndexTaskSubmissionPort | None = None,
    index_input_publisher: VectorIndexInputPublisherPort | None = None,
    compatibility_state_reader: (VectorIndexCompatibilityStatePort | None) = None,
    secret_resolver: SecretResolver | None = None,
    observer: VectorStoreObserver | None = None,
) -> HubWikiVectorRuntimeResolver | None:
    values = environ if environ is not None else os.environ
    workspace_id = str(values.get("ANANTA_WIKI_VECTOR_WORKSPACE_ID") or "").strip()
    source_id = str(values.get("ANANTA_WIKI_VECTOR_SOURCE_ID") or "").strip()
    if not workspace_id and not source_id:
        return None
    if not workspace_id or not source_id:
        raise ValueError("wiki_vector_runtime_scope_incomplete")
    raw_opt_in = str(values.get("ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED") or "false").strip().lower()
    if raw_opt_in in _TRUE_VALUES:
        allow_hub_qdrant_reads = True
    elif raw_opt_in in _FALSE_VALUES:
        allow_hub_qdrant_reads = False
    else:
        raise ValueError("wiki_vector_hub_qdrant_read_boolean_invalid")
    if index_input_publisher is None:
        from agent.services.vector_index_input_artifact_service import (
            build_vector_index_input_publisher,
        )

        index_input_publisher = build_vector_index_input_publisher(environ=values)
    return HubWikiVectorRuntimeResolver(
        workspace_id=workspace_id,
        source_id=source_id,
        profile_name=str(values.get("ANANTA_WIKI_VECTOR_PROFILE_NAME") or "default").strip(),
        retrieval_cache_state=str(values.get("ANANTA_WIKI_VECTOR_RETRIEVAL_CACHE_STATE") or ""),
        manifest_hash=str(values.get("ANANTA_WIKI_VECTOR_MANIFEST_HASH") or ""),
        rollout_service=rollout_service,
        index_task_service=index_task_service,
        index_input_publisher=index_input_publisher,
        compatibility_state_reader=compatibility_state_reader,
        secret_resolver=secret_resolver,
        observer=observer,
        allow_hub_qdrant_reads=allow_hub_qdrant_reads,
    )


def build_wiki_retrieval_index_service(
    *,
    fts_db_path: str | Path,
    vector_index_path: str | Path,
    runtime_resolver: HubWikiVectorRuntimeResolver,
    embedding_provider: object | None = None,
    embedding_provider_config: Mapping[str, object] | None = None,
):
    """Productive Wiki composition without embedding or queue logic here."""

    from agent.services.wiki_retrieval_index_service import (
        WikiRetrievalIndexService,
    )

    runtime = runtime_resolver.resolve()
    return WikiRetrievalIndexService(
        fts_db_path=Path(fts_db_path),
        vector_index_path=Path(vector_index_path),
        embedding_provider=embedding_provider,
        vector_config=runtime.vector_store_config,
        vector_secret_resolver=runtime.secret_resolver,
        vector_observer=runtime.observer,
        index_task_service=runtime.index_task_service,
        index_input_publisher=runtime.index_input_publisher,
        embedding_provider_config=embedding_provider_config,
        allow_hub_qdrant_reads=runtime.allow_hub_qdrant_reads,
    )


__all__ = [
    "HubWikiVectorRuntimeResolver",
    "VectorIndexCompatibilityStatePort",
    "WikiVectorRuntimeContext",
    "build_default_wiki_vector_runtime_resolver",
    "build_wiki_retrieval_index_service",
]
