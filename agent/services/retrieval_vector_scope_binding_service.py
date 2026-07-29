"""Hub-owned binding between task execution context and vector retrieval scope.

The deployment overlay is a capability grant, not request input.  This module
materializes that grant into the authoritative Hub task and later resolves it
read-only for RAG.  Generic task APIs are not allowed to create or replace the
reserved context block.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agent.services.retrieval_vector_runtime_scope_service import (
    RetrievalVectorRuntimeScope,
)

RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY = "retrieval_vector_scope"
RETRIEVAL_VECTOR_SCOPE_SCHEMA = "ananta.retrieval_vector_scope.v1"
DEPLOYMENT_BINDING_SOURCE = "hub_deployment_policy"

_CONTEXT_FIELDS = frozenset(
    {
        "schema",
        "bound_task_id",
        "workspace_id",
        "codecompass_repository_id",
        "wiki_source_id",
        "profile_name",
        "binding_source",
        "bound_at",
    }
)


class RetrievalVectorScopeTaskRepositoryPort(Protocol):
    """Narrow authoritative task persistence port."""

    def get_by_id(self, task_id: str) -> object | None: ...

    def save(self, task: object) -> object: ...


class RetrievalVectorScopeBindingProviderPort(Protocol):
    """Resolve a Hub-trusted binding independently of retrieval payloads."""

    def binding_for_task(
        self,
        *,
        task_id: str,
        task: object | None,
    ) -> "RetrievalVectorScopeBinding | None": ...


class RetrievalVectorScopeBinderPort(Protocol):
    """Materialize an immutable task-bound scope before Hub retrieval."""

    def bind_task_scope(
        self,
        task_id: str,
    ) -> RetrievalVectorRuntimeScope | None: ...


class RetrievalVectorScopeResolverPort(Protocol):
    """Read a previously Hub-bound scope for one authoritative task."""

    def resolve_task_scope(
        self,
        task_id: str,
    ) -> RetrievalVectorRuntimeScope | None: ...


@dataclass(frozen=True, slots=True)
class RetrievalVectorScopeBinding:
    """Trusted scope selected by a Hub-side policy provider."""

    workspace_id: str
    codecompass_repository_id: str = ""
    wiki_source_id: str = ""
    profile_name: str = "default"
    binding_source: str = DEPLOYMENT_BINDING_SOURCE

    def __post_init__(self) -> None:
        scope = self.as_runtime_scope()
        source = str(self.binding_source or "").strip()
        if not source:
            raise ValueError("retrieval_vector_scope_binding_source_required")
        object.__setattr__(self, "workspace_id", scope.workspace_id)
        object.__setattr__(
            self,
            "codecompass_repository_id",
            scope.codecompass_repository_id,
        )
        object.__setattr__(self, "wiki_source_id", scope.wiki_source_id)
        object.__setattr__(self, "profile_name", scope.profile_name)
        object.__setattr__(self, "binding_source", source)

    def as_runtime_scope(self) -> RetrievalVectorRuntimeScope:
        return RetrievalVectorRuntimeScope(
            workspace_id=self.workspace_id,
            codecompass_repository_id=self.codecompass_repository_id,
            wiki_source_id=self.wiki_source_id,
            profile_name=self.profile_name,
        )

    def as_task_context(
        self,
        *,
        task_id: str,
        bound_at: float | None = None,
    ) -> dict[str, object]:
        timestamp = float(bound_at if bound_at is not None else time.time())
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("retrieval_vector_scope_bound_at_invalid")
        return {
            "schema": RETRIEVAL_VECTOR_SCOPE_SCHEMA,
            "bound_task_id": _required_task_id(task_id),
            "workspace_id": self.workspace_id,
            "codecompass_repository_id": self.codecompass_repository_id,
            "wiki_source_id": self.wiki_source_id,
            "profile_name": self.profile_name,
            "binding_source": self.binding_source,
            "bound_at": timestamp,
        }


class DeploymentRetrievalVectorScopeBindingProvider:
    """Build the single-scope Hub capability declared by deployment config."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = environ

    def binding_for_task(
        self,
        *,
        task_id: str,
        task: object | None,
    ) -> RetrievalVectorScopeBinding | None:
        del task_id, task
        values = self._environ if self._environ is not None else os.environ
        codecompass = _deployment_domain_binding(
            values,
            workspace_key="ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID",
            source_key="ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID",
            profile_key="ANANTA_CODECOMPASS_VECTOR_PROFILE_NAME",
            incomplete_reason="codecompass_vector_runtime_scope_incomplete",
        )
        wiki = _deployment_domain_binding(
            values,
            workspace_key="ANANTA_WIKI_VECTOR_WORKSPACE_ID",
            source_key="ANANTA_WIKI_VECTOR_SOURCE_ID",
            profile_key="ANANTA_WIKI_VECTOR_PROFILE_NAME",
            incomplete_reason="wiki_vector_runtime_scope_incomplete",
        )
        if codecompass is None and wiki is None:
            return None
        configured = [item for item in (codecompass, wiki) if item is not None]
        workspaces = {item[0] for item in configured}
        if len(workspaces) != 1:
            raise ValueError("retrieval_vector_scope_workspace_conflict")
        profiles = {item[2] for item in configured}
        if len(profiles) != 1:
            raise ValueError("retrieval_vector_scope_profile_conflict")
        return RetrievalVectorScopeBinding(
            workspace_id=configured[0][0],
            codecompass_repository_id=(codecompass[1] if codecompass is not None else ""),
            wiki_source_id=wiki[1] if wiki is not None else "",
            profile_name=configured[0][2],
        )


class HubTaskRetrievalVectorScopeBinder:
    """Persist a policy-selected scope under the reserved task context key."""

    def __init__(
        self,
        *,
        task_repository: RetrievalVectorScopeTaskRepositoryPort | None = None,
        binding_provider: RetrievalVectorScopeBindingProviderPort | None = None,
        mutation_lock_port: object | None = None,
    ) -> None:
        self._task_repository = task_repository
        self._binding_provider = binding_provider or DeploymentRetrievalVectorScopeBindingProvider()
        self._mutation_lock_port = mutation_lock_port

    def _repository(self) -> RetrievalVectorScopeTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def _locks(self):
        if self._mutation_lock_port is not None:
            return self._mutation_lock_port
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port()

    def bind_task_scope(
        self,
        task_id: str,
    ) -> RetrievalVectorRuntimeScope | None:
        normalized_task_id = _required_task_id(task_id)
        repository = self._repository()
        with self._locks().mutation_locks({normalized_task_id}) as acquired:
            if not acquired:
                raise RuntimeError("retrieval_vector_scope_binding_lock_unavailable")
            task = repository.get_by_id(normalized_task_id)
            binding = self._binding_provider.binding_for_task(
                task_id=normalized_task_id,
                task=task,
            )
            if binding is None:
                return None
            if task is None:
                raise ValueError("retrieval_vector_scope_task_not_found")
            existing = _scope_from_task(
                task,
                expected_task_id=normalized_task_id,
            )
            expected = binding.as_runtime_scope()
            if existing is not None:
                if existing != expected:
                    raise ValueError("retrieval_vector_scope_binding_conflict")
                return existing
            worker_context = dict(_task_value(task, "worker_execution_context") or {})
            worker_context[RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY] = binding.as_task_context(task_id=normalized_task_id)
            _set_task_value(
                task,
                "worker_execution_context",
                worker_context,
            )
            if _has_task_field(task, "updated_at"):
                _set_task_value(task, "updated_at", time.time())
            persisted = repository.save(task)
            resolved = _scope_from_task(
                persisted,
                expected_task_id=normalized_task_id,
            )
            if resolved != expected:
                raise RuntimeError("retrieval_vector_scope_binding_persist_failed")
            return resolved


class HubTaskRetrievalVectorScopeResolver:
    """Resolve only Hub-persisted task context; never inspect request data."""

    def __init__(
        self,
        *,
        task_repository: RetrievalVectorScopeTaskRepositoryPort | None = None,
    ) -> None:
        self._task_repository = task_repository

    def _repository(self) -> RetrievalVectorScopeTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def resolve_task_scope(
        self,
        task_id: str,
    ) -> RetrievalVectorRuntimeScope | None:
        normalized_task_id = _required_task_id(task_id)
        task = self._repository().get_by_id(normalized_task_id)
        if task is None:
            return None
        return _scope_from_task(
            task,
            expected_task_id=normalized_task_id,
        )


def _deployment_domain_binding(
    values: Mapping[str, str],
    *,
    workspace_key: str,
    source_key: str,
    profile_key: str,
    incomplete_reason: str,
) -> tuple[str, str, str] | None:
    workspace = str(values.get(workspace_key) or "").strip()
    source = str(values.get(source_key) or "").strip()
    if not workspace and not source:
        return None
    if not workspace or not source:
        raise ValueError(incomplete_reason)
    profile = str(values.get(profile_key) or "default").strip()
    if not profile:
        raise ValueError("vector_runtime_profile_scope_required")
    return workspace, source, profile


def _required_task_id(value: object) -> str:
    task_id = str(value or "").strip()
    if not task_id or len(task_id) > 256 or any(ord(character) < 32 for character in task_id):
        raise ValueError("retrieval_vector_scope_task_id_required")
    return task_id


def _task_value(task: object, name: str) -> object:
    if isinstance(task, Mapping):
        return task.get(name)
    return getattr(task, name, None)


def _has_task_field(task: object, name: str) -> bool:
    return name in task if isinstance(task, Mapping) else hasattr(task, name)


def _set_task_value(task: object, name: str, value: object) -> None:
    if isinstance(task, dict):
        task[name] = value
        return
    setattr(task, name, value)


def _scope_from_task(
    task: object,
    *,
    expected_task_id: str,
) -> RetrievalVectorRuntimeScope | None:
    worker_context = _task_value(task, "worker_execution_context")
    if not isinstance(worker_context, Mapping):
        return None
    raw = worker_context.get(RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("retrieval_vector_scope_context_invalid")
    unknown = sorted(set(raw).difference(_CONTEXT_FIELDS))
    if unknown:
        raise ValueError("retrieval_vector_scope_context_fields_invalid")
    if str(raw.get("schema") or "") != RETRIEVAL_VECTOR_SCOPE_SCHEMA:
        raise ValueError("retrieval_vector_scope_schema_invalid")
    task_id = _required_task_id(raw.get("bound_task_id"))
    if task_id != expected_task_id:
        raise ValueError("retrieval_vector_scope_task_binding_mismatch")
    binding_source = str(raw.get("binding_source") or "").strip()
    if not binding_source or len(binding_source) > 256 or any(ord(character) < 32 for character in binding_source):
        raise ValueError("retrieval_vector_scope_binding_source_required")
    try:
        bound_at = float(raw.get("bound_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("retrieval_vector_scope_bound_at_invalid") from exc
    if not math.isfinite(bound_at) or bound_at < 0:
        raise ValueError("retrieval_vector_scope_bound_at_invalid")
    scope = RetrievalVectorRuntimeScope(
        workspace_id=str(raw.get("workspace_id") or ""),
        codecompass_repository_id=str(raw.get("codecompass_repository_id") or ""),
        wiki_source_id=str(raw.get("wiki_source_id") or ""),
        profile_name=str(raw.get("profile_name") or ""),
    )
    return scope


_default_binder: HubTaskRetrievalVectorScopeBinder | None = None
_default_resolver: HubTaskRetrievalVectorScopeResolver | None = None


def get_retrieval_vector_scope_binder() -> HubTaskRetrievalVectorScopeBinder:
    global _default_binder
    if _default_binder is None:
        _default_binder = HubTaskRetrievalVectorScopeBinder()
    return _default_binder


def get_retrieval_vector_scope_resolver() -> HubTaskRetrievalVectorScopeResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = HubTaskRetrievalVectorScopeResolver()
    return _default_resolver


__all__ = [
    "DEPLOYMENT_BINDING_SOURCE",
    "DeploymentRetrievalVectorScopeBindingProvider",
    "HubTaskRetrievalVectorScopeBinder",
    "HubTaskRetrievalVectorScopeResolver",
    "RETRIEVAL_VECTOR_SCOPE_CONTEXT_KEY",
    "RETRIEVAL_VECTOR_SCOPE_SCHEMA",
    "RetrievalVectorScopeBinderPort",
    "RetrievalVectorScopeBinding",
    "RetrievalVectorScopeBindingProviderPort",
    "RetrievalVectorScopeResolverPort",
    "get_retrieval_vector_scope_binder",
    "get_retrieval_vector_scope_resolver",
]
