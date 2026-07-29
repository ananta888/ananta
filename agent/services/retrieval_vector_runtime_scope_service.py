"""Trusted request scope and resolver factory for vector retrieval runtimes.

The scope object is deliberately not constructed from arbitrary mappings.
The Hub must create it after resolving the authenticated workspace and
repository/source boundary for a request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from agent.services.codecompass_vector_runtime_service import (
    HubCodeCompassVectorRuntimeResolver,
)
from agent.services.vector_store_rollout_service import (
    VectorStoreRolloutService,
)
from agent.services.wiki_vector_runtime_service import (
    HubWikiVectorRuntimeResolver,
)
from worker.retrieval.vector_store_endpoint_policy import (
    SecretResolver,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory
from worker.retrieval.vector_store_observer import VectorStoreObserver

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _scope_identifier(value: object, *, reason: str, required: bool) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        if required:
            raise ValueError(reason)
        return ""
    if len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ValueError(reason)
    return normalized


@runtime_checkable
class RetrievalLifecycle(Protocol):
    """Small lifecycle capability used by retrieval caches."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RetrievalVectorRuntimeScope:
    """Hub-authorized vector scope for one retrieval request."""

    workspace_id: str
    codecompass_repository_id: str = ""
    wiki_source_id: str = ""
    profile_name: str = "default"

    def __post_init__(self) -> None:
        workspace_id = _scope_identifier(
            self.workspace_id,
            reason="vector_runtime_workspace_scope_required",
            required=True,
        )
        repository_id = _scope_identifier(
            self.codecompass_repository_id,
            reason="codecompass_vector_request_scope_incomplete",
            required=False,
        )
        wiki_source_id = _scope_identifier(
            self.wiki_source_id,
            reason="wiki_vector_request_scope_incomplete",
            required=False,
        )
        profile_name = _scope_identifier(
            self.profile_name,
            reason="vector_runtime_profile_scope_required",
            required=True,
        )
        if not repository_id and not wiki_source_id:
            raise ValueError("vector_runtime_source_scope_required")
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(
            self,
            "codecompass_repository_id",
            repository_id,
        )
        object.__setattr__(self, "wiki_source_id", wiki_source_id)
        object.__setattr__(self, "profile_name", profile_name)

    def cache_key(self, domain: str) -> tuple[str, ...]:
        normalized_domain = str(domain or "").strip().lower()
        if normalized_domain == "codecompass":
            source_id = self.codecompass_repository_id
        elif normalized_domain == "wiki":
            source_id = self.wiki_source_id
        else:
            raise ValueError("vector_runtime_domain_invalid")
        if not source_id:
            raise ValueError(f"{normalized_domain}_vector_request_scope_incomplete")
        return (
            normalized_domain,
            self.workspace_id,
            source_id,
            self.profile_name,
        )


class RetrievalVectorRuntimeResolverFactory(Protocol):
    """Create cheap resolvers bound to one trusted request scope."""

    def codecompass_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ) -> object | None: ...

    def wiki_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ) -> object | None: ...


class HubRetrievalVectorRuntimeResolverFactory:
    """Hub composition root for request-bound rollout resolution."""

    def __init__(
        self,
        *,
        codecompass_enabled: bool = True,
        wiki_enabled: bool = True,
        rollout_service: VectorStoreRolloutService | None = None,
        codecompass_index_task_service: object | None = None,
        wiki_index_task_service: object | None = None,
        codecompass_index_input_publisher: object | None = None,
        wiki_index_input_publisher: object | None = None,
        secret_resolver: SecretResolver | None = None,
        vector_store_factory: VectorStoreFactory | None = None,
        observer: VectorStoreObserver | None = None,
        allow_codecompass_hub_qdrant_reads: bool = False,
        allow_wiki_hub_qdrant_reads: bool = False,
        wiki_retrieval_cache_state: str = "",
        wiki_manifest_hash: str = "",
    ) -> None:
        self._codecompass_enabled = bool(codecompass_enabled)
        self._wiki_enabled = bool(wiki_enabled)
        self._rollout_service = rollout_service
        self._codecompass_index_task_service = codecompass_index_task_service
        self._wiki_index_task_service = wiki_index_task_service
        self._codecompass_index_input_publisher = codecompass_index_input_publisher
        self._wiki_index_input_publisher = wiki_index_input_publisher
        self._secret_resolver = secret_resolver
        self._vector_store_factory = vector_store_factory
        self._observer = observer
        self._allow_codecompass_hub_qdrant_reads = bool(allow_codecompass_hub_qdrant_reads)
        self._allow_wiki_hub_qdrant_reads = bool(allow_wiki_hub_qdrant_reads)
        self._wiki_retrieval_cache_state = str(wiki_retrieval_cache_state or "")
        self._wiki_manifest_hash = str(wiki_manifest_hash or "")

    def codecompass_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ) -> HubCodeCompassVectorRuntimeResolver | None:
        if not self._codecompass_enabled:
            return None
        scope.cache_key("codecompass")
        return HubCodeCompassVectorRuntimeResolver(
            workspace_id=scope.workspace_id,
            repository_id=scope.codecompass_repository_id,
            profile_name=scope.profile_name,
            rollout_service=self._rollout_service,
            index_task_service=self._codecompass_index_task_service,
            secret_resolver=self._secret_resolver,
            vector_store_factory=self._vector_store_factory,
            observer=self._observer,
            index_input_publisher=(self._codecompass_index_input_publisher),
            allow_hub_qdrant_reads=(self._allow_codecompass_hub_qdrant_reads),
        )

    def wiki_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ) -> HubWikiVectorRuntimeResolver | None:
        if not self._wiki_enabled:
            return None
        scope.cache_key("wiki")
        return HubWikiVectorRuntimeResolver(
            workspace_id=scope.workspace_id,
            source_id=scope.wiki_source_id,
            profile_name=scope.profile_name,
            retrieval_cache_state=self._wiki_retrieval_cache_state,
            manifest_hash=self._wiki_manifest_hash,
            rollout_service=self._rollout_service,
            index_task_service=self._wiki_index_task_service,
            index_input_publisher=self._wiki_index_input_publisher,
            secret_resolver=self._secret_resolver,
            observer=self._observer,
            allow_hub_qdrant_reads=(self._allow_wiki_hub_qdrant_reads),
        )


def _read_boolean(
    values: Mapping[str, str],
    name: str,
    *,
    reason: str,
) -> bool:
    raw = str(values.get(name) or "false").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(reason)


def _configured_pair(
    values: Mapping[str, str],
    first: str,
    second: str,
    *,
    reason: str,
) -> bool:
    first_value = str(values.get(first) or "").strip()
    second_value = str(values.get(second) or "").strip()
    if bool(first_value) != bool(second_value):
        raise ValueError(reason)
    return bool(first_value)


def build_default_retrieval_vector_runtime_resolver_factory(
    *,
    environ: Mapping[str, str] | None = None,
    rollout_service: VectorStoreRolloutService | None = None,
    codecompass_index_task_service: object | None = None,
    wiki_index_task_service: object | None = None,
    secret_resolver: SecretResolver | None = None,
    vector_store_factory: VectorStoreFactory | None = None,
    observer: VectorStoreObserver | None = None,
) -> HubRetrievalVectorRuntimeResolverFactory | None:
    """Build a request-bound factory when a Hub-read domain is configured.

    The legacy scope variables remain deployment capability switches. Their
    values are never reused as a request scope; the authenticated Hub request
    must supply :class:`RetrievalVectorRuntimeScope`.
    """

    values = environ if environ is not None else os.environ
    codecompass_enabled = _configured_pair(
        values,
        "ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID",
        "ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID",
        reason="codecompass_vector_runtime_scope_incomplete",
    )
    wiki_enabled = _configured_pair(
        values,
        "ANANTA_WIKI_VECTOR_WORKSPACE_ID",
        "ANANTA_WIKI_VECTOR_SOURCE_ID",
        reason="wiki_vector_runtime_scope_incomplete",
    )
    if not codecompass_enabled and not wiki_enabled:
        return None

    from agent.services.vector_index_input_artifact_service import (
        build_vector_index_input_publisher,
    )

    publisher = build_vector_index_input_publisher(environ=values)
    return HubRetrievalVectorRuntimeResolverFactory(
        codecompass_enabled=codecompass_enabled,
        wiki_enabled=wiki_enabled,
        rollout_service=rollout_service,
        codecompass_index_task_service=(codecompass_index_task_service),
        wiki_index_task_service=wiki_index_task_service,
        codecompass_index_input_publisher=(publisher if codecompass_enabled else None),
        wiki_index_input_publisher=(publisher if wiki_enabled else None),
        secret_resolver=secret_resolver,
        vector_store_factory=vector_store_factory,
        observer=observer,
        allow_codecompass_hub_qdrant_reads=_read_boolean(
            values,
            "ANANTA_CODECOMPASS_VECTOR_HUB_QDRANT_READ_ENABLED",
            reason=("codecompass_vector_hub_qdrant_read_boolean_invalid"),
        ),
        allow_wiki_hub_qdrant_reads=_read_boolean(
            values,
            "ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED",
            reason="wiki_vector_hub_qdrant_read_boolean_invalid",
        ),
        wiki_retrieval_cache_state=str(values.get("ANANTA_WIKI_VECTOR_RETRIEVAL_CACHE_STATE") or ""),
        wiki_manifest_hash=str(values.get("ANANTA_WIKI_VECTOR_MANIFEST_HASH") or ""),
    )


__all__ = [
    "HubRetrievalVectorRuntimeResolverFactory",
    "RetrievalLifecycle",
    "RetrievalVectorRuntimeResolverFactory",
    "RetrievalVectorRuntimeScope",
    "build_default_retrieval_vector_runtime_resolver_factory",
]
