"""Production adapters for source operations, destinations and artifact purge."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import AgentInfoDB, KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceAccessGrantDB,
    SourceConnectionDB,
    SourceControlArtifactDeletionDB,
    SourceControlPurgeApprovalDB,
    SourceRevisionDB,
)
from agent.repositories.source_control_repository import (
    SQLSourceControlRepository,
)
from agent.services.codecompass_domain_supplement import (
    CodeCompassDomainSupplementPort,
    get_codecompass_domain_supplement_reader,
)
from agent.services.codecompass_domain_supplement_graph_read import (
    CodeCompassDomainSupplementGraphReadCoordinator,
)
from agent.services.codecompass_graph_artifact_resolver import (
    GRAPH_VISUAL_METRICS_FILENAME,
    CodeCompassGraphArtifactResolver,
    ResolvedCodeCompassDomainSupplement,
)
from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainCatalogPort,
    get_codecompass_graph_domain_catalog_service,
)
from agent.services.codecompass_graph_projection_service import (
    CodeCompassGraphProjectionService,
)
from agent.services.codecompass_graph_read_service import (
    CodeCompassGraphReadError,
    CodeCompassGraphReadPort,
    CodeCompassGraphReadService,
)
from agent.services.codecompass_graph_store_cache import (
    CodeCompassGraphStoreCache,
    get_codecompass_graph_store_cache,
)
from agent.services.codecompass_graph_window_service import (
    CodeCompassGraphWindowSelector,
)
from agent.services.effective_source_access_service import (
    EffectivePolicyEvaluation,
    EffectiveSourceAccessService,
    EffectiveSourceRevision,
)
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from agent.services.source_destination_resolution import (
    DestinationCatalogRecord,
)
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelHealth,
)
from ananta_contracts.source_control import (
    ConnectionState,
    DestinationDescriptor,
    ProviderLocation,
)

_LOG = logging.getLogger(__name__)
_TERMINAL_RUN_STATES = frozenset(
    {"completed", "failed", "cancelled", "purged", "tombstoned"}
)


class SourceControlProductionAdapterError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def _public(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_public(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _public(to_dict())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _public(model_dump(mode="json"))
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        return {
            str(key): _public(item)
            for key, item in values.items()
            if not str(key).startswith("_")
        }
    raise SourceControlProductionAdapterError(
        "source_operation_result_invalid", status_code=502
    )


class _SingleIndexRepository:
    def __init__(self, index: KnowledgeIndexDB) -> None:
        self._index = index

    def list_completed(self):
        return [self._index] if self._index.status == "completed" else []


class _EmptyKnowledgeLinks:
    def get_by_artifact(self, artifact_id: str):
        del artifact_id
        return []


_BOUND_INDEX_PLAN_FIELDS = frozenset(
    {
        "hub_task_id",
        "source_revision_id",
        "source_revision_digest",
        "admission_digest",
        "policy_snapshot_id",
        "policy_snapshot_digest",
        "destination_id",
        "destination_digest",
        "source_access_grant_id",
        "source_access_grant_digest",
        "files",
        "resource_budget",
        "assignment",
        "destination_selection",
        "source_scope",
        "source_id",
        "records",
    }
)
_BOUND_INDEX_REMOTE_PLAN_FIELDS = frozenset(
    {"source_payload_digest", "source_payload_connection_id"}
)

_REPOSITORY_CONNECTOR_SCOPES = frozenset(
    {
        "registered_workspace",
        "local_directory",
        "git",
        "github",
        "generic_git",
        "github_repository",
    }
)


def _runtime_index_source_scope(connector_type: object) -> str:
    normalized = str(connector_type or "").strip().lower()
    if normalized in _REPOSITORY_CONNECTOR_SCOPES:
        return "repo_path"
    if normalized in {"artifact", "repo_path", "wiki"}:
        return normalized
    raise SourceControlProductionAdapterError(
        "source_index_connector_scope_unsupported",
        status_code=400,
    )


class HubBoundSourceIndexSubmissionAdapter:
    """Submit only complete Hub-planned, revision-bound index jobs."""

    def __init__(self, *, planner: object, job_service: object) -> None:
        self._planner = planner
        self._jobs = job_service

    def submit(
        self,
        *,
        connection: SourceConnectionDB,
        revision: SourceRevisionDB,
        descriptor: Mapping[str, object],
        actor_id: str,
        idempotency_key: str,
        profile_name: str,
    ) -> Mapping[str, object]:
        plan_method = getattr(
            self._planner, "plan_bound_source_revision", None
        )
        submit_method = getattr(
            self._jobs, "submit_bound_source_revision_job", None
        )
        if not callable(plan_method) or not callable(submit_method):
            raise SourceControlProductionAdapterError(
                "source_index_governance_backend_unconfigured",
                status_code=503,
            )
        raw_plan = plan_method(
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            actor_id=actor_id,
            connection_id=connection.connection_id,
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            descriptor=dict(descriptor),
            idempotency_key=idempotency_key,
        )
        if not isinstance(raw_plan, Mapping):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_invalid", status_code=502
            )
        plan = dict(raw_plan)
        plan_fields = set(plan)
        remote_plan_fields = plan_fields & _BOUND_INDEX_REMOTE_PLAN_FIELDS
        if (
            plan_fields
            not in (
                _BOUND_INDEX_PLAN_FIELDS,
                _BOUND_INDEX_PLAN_FIELDS
                | _BOUND_INDEX_REMOTE_PLAN_FIELDS,
            )
            or remote_plan_fields
            not in (set(), set(_BOUND_INDEX_REMOTE_PLAN_FIELDS))
        ):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_invalid", status_code=502
            )
        if remote_plan_fields:
            if (
                re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(plan["source_payload_digest"]),
                )
                is None
                or plan["source_payload_connection_id"]
                != connection.connection_id
            ):
                raise SourceControlProductionAdapterError(
                    "source_index_governance_plan_stale", status_code=409
                )
        if (
            plan["source_revision_id"] != revision.source_revision_id
            or plan["source_revision_digest"] != revision.revision_digest
            or plan["source_scope"] != connection.connector_type
            or plan["source_id"] != str(descriptor.get("source_id") or "")
        ):
            raise SourceControlProductionAdapterError(
                "source_index_governance_plan_stale", status_code=409
            )
        execution_plan = {
            **{
                key: value
                for key, value in plan.items()
                if key not in _BOUND_INDEX_REMOTE_PLAN_FIELDS
            },
            "source_scope": _runtime_index_source_scope(
                plan["source_scope"]
            ),
        }
        result = submit_method(
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            owner_id=connection.owner_id,
            created_by=actor_id,
            idempotency_key=idempotency_key,
            profile_name=profile_name,
            source_operation="index",
            source_transformation="redacted",
            source_purpose="knowledge-index",
            source_policy_version=str(plan["policy_snapshot_id"]),
            **execution_plan,
        )
        public = _public(result)
        if not isinstance(public, Mapping):
            raise SourceControlProductionAdapterError(
                "source_index_submission_result_invalid", status_code=502
            )
        return dict(public)


class HubSourceControlOperationsAdapter:
    """Reuse existing Hub source and CodeCompass services under canonical scope."""

    def __init__(
        self,
        *,
        engine: Engine,
        registry: SourceRegistry,
        refresh: SourceRefreshService,
        index_submission: object | None,
        graph_resolver: CodeCompassGraphArtifactResolver,
        graph_projection: CodeCompassGraphProjectionService,
        graph_window: CodeCompassGraphWindowSelector,
        graph_domains: CodeCompassGraphDomainCatalogPort | None = None,
        graph_read: CodeCompassGraphReadPort | None = None,
        graph_store_cache: CodeCompassGraphStoreCache | None = None,
        domain_supplements: CodeCompassDomainSupplementPort | None = None,
        scanner: object | None = None,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._refresh = refresh
        self._index_submission = index_submission
        self._resolver = graph_resolver
        self._graph_domains = (
            graph_domains or get_codecompass_graph_domain_catalog_service()
        )
        self._graph_read = graph_read or CodeCompassGraphReadService(
            projection=graph_projection,
            window=graph_window,
            domains=self._graph_domains,
        )
        self._graph_store_cache = (
            graph_store_cache or get_codecompass_graph_store_cache()
        )
        self._domain_supplements = (
            domain_supplements or get_codecompass_domain_supplement_reader()
        )
        self._domain_supplement_graph_read = (
            CodeCompassDomainSupplementGraphReadCoordinator(
                graph_read=self._graph_read,
                graph_domains=self._graph_domains,
                domain_supplements=self._domain_supplements,
            )
        )
        self._scanner = scanner

    def refresh(self, **kwargs: object) -> Mapping[str, object]:
        descriptor = self._descriptor(**kwargs)
        connection = self._connection(**kwargs)
        refresh_descriptor = getattr(
            self._refresh, "refresh_descriptor", None
        )
        result = (
            refresh_descriptor(descriptor=descriptor, dry_run=False)
            if callable(refresh_descriptor)
            else self._refresh.refresh_source(
                source_id=str(descriptor["source_id"]),
                dry_run=False,
            )
        )
        if connection.state == ConnectionState.DRAFT.value:
            SQLSourceControlRepository(self._engine).transition_connection(
                tenant_id=connection.tenant_id,
                project_id=connection.project_id,
                owner_id=connection.owner_id,
                connection_id=connection.connection_id,
                target_state=ConnectionState.ACTIVE,
                expected_lock_version=int(connection.lock_version),
            )
        return self._bounded_receipt(result)

    def scan(self, **kwargs: object) -> Mapping[str, object]:
        descriptor = self._descriptor(**kwargs)
        connection = self._connection(**kwargs)
        source_id = str(descriptor["source_id"])
        resolve_revision = getattr(
            self._refresh, "resolve_revision_descriptor", None
        )
        inventory_descriptor = getattr(
            self._refresh, "inventory_descriptor", None
        )
        health_descriptor = getattr(
            self._refresh, "health_descriptor", None
        )
        revision = (
            resolve_revision(descriptor=descriptor)
            if callable(resolve_revision)
            else self._refresh.resolve_revision(source_id=source_id)
        )
        inventory = (
            inventory_descriptor(descriptor=descriptor)
            if callable(inventory_descriptor)
            else self._refresh.inventory(source_id=source_id)
        )
        health = (
            health_descriptor(descriptor=descriptor)
            if callable(health_descriptor)
            else self._refresh.health(source_id=source_id)
        )
        scan = getattr(self._scanner, "scan_source", None)
        if not callable(scan):
            raise SourceControlProductionAdapterError(
                "source_scan_backend_unconfigured", status_code=503
            )
        result = scan(
            descriptor=dict(descriptor),
            revision=revision,
            inventory=inventory,
        )
        scan_result = _public(result)
        if not isinstance(scan_result, Mapping):
            raise SourceControlProductionAdapterError(
                "source_scan_result_invalid", status_code=502
            )
        persisted = self._latest_revision(connection.connection_id)
        if (
            persisted is None
            or persisted.revision_digest != revision.revision_digest
            or persisted.content_manifest_digest
            != inventory.manifest_digest
            or persisted.admission_state not in {"admitted", "blocked"}
        ):
            raise SourceControlProductionAdapterError(
                "source_scan_admission_receipt_missing", status_code=502
            )
        return {
            "status": str(scan_result.get("status") or "completed"),
            "source_revision_id": persisted.source_revision_id,
            "revision_digest": revision.revision_digest,
            "manifest_digest": inventory.manifest_digest,
            "admission_state": persisted.admission_state,
            "item_count": inventory.item_count,
            "total_bytes": inventory.total_bytes,
            "health": _public(health),
            "scan": dict(scan_result),
        }

    def run(self, **kwargs: object) -> Mapping[str, object]:
        descriptor = self._descriptor(**kwargs)
        connection = self._connection(**kwargs)
        revision = self._latest_revision(connection.connection_id)
        if revision is None or revision.admission_state != "admitted":
            raise SourceControlProductionAdapterError(
                "source_revision_admission_required", status_code=409
            )
        submit = getattr(self._index_submission, "submit", None)
        if not callable(submit):
            raise SourceControlProductionAdapterError(
                "source_index_governance_backend_unconfigured",
                status_code=503,
            )
        payload = kwargs.get("payload")
        profile_name = (
            str(payload.get("index_profile_id"))
            if isinstance(payload, Mapping)
            else "default"
        )
        result = submit(
            connection=connection,
            revision=revision,
            descriptor=descriptor,
            actor_id=str(kwargs["actor_id"]),
            idempotency_key=str(kwargs["idempotency_key"]),
            profile_name=profile_name,
        )
        return self._bounded_receipt(result)

    def graph(self, **kwargs: object) -> Mapping[str, object]:
        index = self._active_index(**kwargs)
        parameters = kwargs.get("parameters")
        values = dict(parameters) if isinstance(parameters, Mapping) else {}
        domain_scope = str(values.get("domain_scope") or "").strip()
        view = str(values.get("view") or "default").strip().lower()
        try:
            base_store = self._graph_store(index)
            # The immutable supplement is opened only for catalog or scoped
            # reads. A regular graph request remains a base-artifact read.
            resolved = (
                self._resolve_domain_supplement(index)
                if view == "inventory" or domain_scope
                else None
            )
            if view != "inventory" and not domain_scope:
                return self._graph_read.read(
                    index_id=index.id,
                    store=base_store,
                    parameters=values,
                    artifact_status=self._artifact_projection(index),
                )
            if resolved is None:
                return self._graph_read.read(
                    index_id=index.id,
                    store=base_store,
                    parameters=values,
                    artifact_status=self._artifact_projection(index),
                )
            if resolved is not None:
                self._validate_domain_supplement_binding(
                    index=index,
                    resolved=resolved,
                    **kwargs,
                )
            coordinator = getattr(
                self,
                "_domain_supplement_graph_read",
                None,
            )
            if coordinator is None:
                # Compatibility for small unit doubles created via
                # object.__new__; normal production construction always wires
                # this dependency explicitly.
                coordinator = CodeCompassDomainSupplementGraphReadCoordinator(
                    graph_read=self._graph_read,
                    graph_domains=self._graph_domains,
                    domain_supplements=self._domain_supplements,
                )
                self._domain_supplement_graph_read = coordinator
            metadata = (
                index.index_metadata
                if isinstance(index.index_metadata, Mapping)
                else {}
            )
            return coordinator.read(
                index_id=index.id,
                base_store=base_store,
                parameters=values,
                artifact_status=self._artifact_projection(index),
                resolved=resolved,
                fallback_source_revision_id=str(
                    metadata.get("source_revision_id") or ""
                ),
                fallback_source_revision_digest=str(
                    metadata.get("source_revision_digest") or ""
                ),
            )
        except CodeCompassGraphReadError as exc:
            raise SourceControlProductionAdapterError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from exc
        except ValueError as exc:
            reason_code = str(exc) or "graph_domain_supplement_invalid"
            if reason_code == "graph_domain_scope_unknown":
                raise SourceControlProductionAdapterError(
                    reason_code,
                    status_code=400,
                ) from exc
            raise SourceControlProductionAdapterError(
                reason_code,
                status_code=(
                    409
                    if reason_code.startswith(
                        ("graph_domain_supplement_", "domain_supplement_")
                    )
                    else 400
                ),
            ) from exc

    def query(self, **kwargs: object) -> Mapping[str, object]:
        index = self._active_index(**kwargs)
        parameters = kwargs.get("parameters")
        values = parameters if isinstance(parameters, Mapping) else {}
        query = str(values.get("query") or "").strip()
        limit = min(max(int(values.get("limit", 20)), 1), 100)
        if not query:
            raise SourceControlProductionAdapterError("query_required")
        retrieval = KnowledgeIndexRetrievalService(
            knowledge_index_repository=_SingleIndexRepository(index),
            knowledge_link_repository=_EmptyKnowledgeLinks(),
        )
        chunks = retrieval.search_records(
            query,
            limit=limit,
            task_kind="code_review",
            retrieval_intent="fuzzy_semantic",
            allowed_index_ids={str(index.id)},
        )
        matches = [_public(chunk) for chunk in chunks]
        return {
            "answer": None,
            "matches": matches,
            "text_alternative": (
                f"{len(matches)} bounded matches for the active index."
            ),
            "artifact_status": self._artifact_projection(index),
        }

    def _resolve_domain_supplement(
        self,
        index: KnowledgeIndexDB,
    ) -> ResolvedCodeCompassDomainSupplement | None:
        resolve = getattr(
            getattr(self, "_resolver", None),
            "resolve_domain_supplement",
            None,
        )
        if not callable(resolve):
            return None
        result = resolve(index)
        if result is not None and not isinstance(
            result,
            ResolvedCodeCompassDomainSupplement,
        ):
            raise ValueError("graph_domain_supplement_binding_invalid")
        return result

    def _validate_domain_supplement_binding(
        self,
        *,
        index: KnowledgeIndexDB,
        resolved: ResolvedCodeCompassDomainSupplement,
        **kwargs: object,
    ) -> None:
        metadata = index.index_metadata
        if not isinstance(metadata, Mapping):
            raise ValueError("graph_domain_supplement_binding_invalid")
        binding = resolved.binding
        expected_source_id = f"bound-source:{binding.source_revision_id}"
        if (
            binding.knowledge_index_id != index.id
            or binding.source_id != expected_source_id
            or str(metadata.get("source_scope") or "") != binding.source_scope
            or str(metadata.get("source_id") or "") != expected_source_id
            or str(metadata.get("source_revision_id") or "")
            != binding.source_revision_id
            or str(metadata.get("source_revision_digest") or "")
            != binding.source_revision_digest
        ):
            raise ValueError("graph_domain_supplement_binding_stale")
        tenant_id = str(kwargs.get("tenant_id") or "")
        project_id = str(kwargs.get("project_id") or "")
        connection_id = str(kwargs.get("connection_id") or "")
        with Session(self._engine) as db:
            source_binding = db.get(
                KnowledgeIndexSourceBindingDB,
                index.id,
            )
            revision = (
                db.get(SourceRevisionDB, source_binding.source_revision_id)
                if source_binding is not None
                else None
            )
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id == tenant_id,
                    ActiveKnowledgeIndexDB.project_id == project_id,
                    ActiveKnowledgeIndexDB.connection_id == connection_id,
                )
            ).first()
        if (
            source_binding is None
            or revision is None
            or active is None
            or source_binding.knowledge_index_id != index.id
            or source_binding.tenant_id != tenant_id
            or source_binding.project_id != project_id
            or source_binding.connection_id != connection_id
            or source_binding.source_revision_id != binding.source_revision_id
            or revision.source_revision_id != binding.source_revision_id
            or revision.revision_digest != binding.source_revision_digest
            or revision.tenant_id != tenant_id
            or revision.project_id != project_id
            or revision.connection_id != connection_id
            or active.knowledge_index_id != index.id
            or active.source_revision_id != binding.source_revision_id
        ):
            raise ValueError("graph_domain_supplement_binding_stale")

    def artifact_status(self, **kwargs: object) -> Mapping[str, object]:
        index = self._active_index(**kwargs)
        parameters = kwargs.get("parameters")
        artifact_id = str(
            parameters.get("artifact_id") or ""
            if isinstance(parameters, Mapping)
            else ""
        )
        if artifact_id not in {index.id, str(index.latest_run_id or "")}:
            raise SourceControlProductionAdapterError(
                "artifact_not_found", status_code=404
            )
        result = self._artifact_projection(index)
        result["artifact_id"] = artifact_id
        return result

    def _descriptor(self, **kwargs: object) -> Mapping[str, object]:
        connection = self._connection(**kwargs)
        from agent.repositories.source_control_repository import (
            SQLSourceControlRepository,
        )

        selector = SQLSourceControlRepository(
            self._engine
        ).get_connection_selector(
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            connection_id=connection.connection_id,
        )
        if selector is not None:
            return selector.descriptor(
                display_name=connection.display_name,
                enabled=connection.state not in {"disabled", "tombstoned"},
            )
        for descriptor in self._registry.list_sources(
            include_disabled=True
        ):
            extensions = descriptor.get("extensions")
            extension_values = (
                extensions.get("source_control")
                if isinstance(extensions, Mapping)
                else None
            )
            binding = (
                extension_values
                if isinstance(extension_values, Mapping)
                else descriptor
            )
            if (
                str(
                    binding.get("connection_id")
                    or binding.get("source_control_connection_id")
                    or ""
                )
                == connection.connection_id
                and str(binding.get("tenant_id") or "")
                == connection.tenant_id
                and str(binding.get("project_id") or "")
                == connection.project_id
            ):
                return descriptor
        raise SourceControlProductionAdapterError(
            "source_connector_not_configured", status_code=503
        )

    def _connection(self, **kwargs: object) -> SourceConnectionDB:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceConnectionDB).where(
                    SourceConnectionDB.connection_id
                    == str(kwargs["connection_id"]),
                    SourceConnectionDB.tenant_id
                    == str(kwargs["tenant_id"]),
                    SourceConnectionDB.project_id
                    == str(kwargs["project_id"]),
                )
            ).first()
            if row is None:
                raise SourceControlProductionAdapterError(
                    "source_control_not_found", status_code=404
                )
            db.expunge(row)
            return row

    def _latest_revision(
        self, connection_id: str
    ) -> SourceRevisionDB | None:
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(SourceRevisionDB).where(
                        SourceRevisionDB.connection_id == connection_id
                    )
                ).all()
            )
            row = max(
                rows,
                key=lambda item: float(
                    getattr(item, "captured_at_epoch", 0.0)
                    or getattr(item, "created_at_epoch", 0.0)
                ),
                default=None,
            )
            if row is not None:
                db.expunge(row)
            return row

    def _active_index(self, **kwargs: object) -> KnowledgeIndexDB:
        with Session(self._engine) as db:
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.connection_id
                    == str(kwargs["connection_id"]),
                    ActiveKnowledgeIndexDB.tenant_id
                    == str(kwargs["tenant_id"]),
                    ActiveKnowledgeIndexDB.project_id
                    == str(kwargs["project_id"]),
                )
            ).first()
            if active is None:
                raise SourceControlProductionAdapterError(
                    "active_index_not_found", status_code=404
                )
            index = db.get(KnowledgeIndexDB, active.knowledge_index_id)
            if index is None:
                raise SourceControlProductionAdapterError(
                    "knowledge_index_not_found", status_code=404
                )
            db.expunge(index)
            return index

    def _graph_store(self, index: KnowledgeIndexDB):
        artifact_resolver = getattr(self._resolver, "resolve_artifacts", None)
        if callable(artifact_resolver):
            index_path, visual_metrics_path = artifact_resolver(index)
        else:
            index_path = self._resolver.resolve(index)
            visual_metrics_path = Path(index_path).with_name(
                GRAPH_VISUAL_METRICS_FILENAME
            )
        cache = (
            getattr(self, "_graph_store_cache", None)
            or get_codecompass_graph_store_cache()
        )
        return cache.get(
            index_path=index_path,
            visual_metrics_path=visual_metrics_path,
        )

    @staticmethod
    def _bounded_receipt(value: object) -> Mapping[str, object]:
        result = _public(value)
        if not isinstance(result, Mapping):
            raise SourceControlProductionAdapterError(
                "source_operation_result_invalid", status_code=502
            )
        allowed = {
            "status",
            "reason_code",
            "job_id",
            "run_id",
            "knowledge_index_id",
            "source_id",
            "snapshot_id",
            "revision_digest",
            "manifest_digest",
        }
        return {
            key: result[key]
            for key in allowed
            if key in result
        }

    @staticmethod
    def _artifact_projection(index: KnowledgeIndexDB) -> dict[str, object]:
        output = Path(str(index.output_dir or ""))
        manifest = Path(str(index.manifest_path or ""))
        return {
            "state": (
                "available"
                if output.is_dir() and manifest.is_file()
                else "unavailable"
            ),
            "reason_code": (
                None
                if output.is_dir() and manifest.is_file()
                else "artifact_not_materialized"
            ),
            "knowledge_index_id": index.id,
            "manifest_present": manifest.is_file(),
        }


class ScopedWorkerModelDestinationCatalog:
    """Project destinations from validated workers and the Hub model catalog."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_supplier: Callable[[], Sequence[object]],
        clock=time.time,
        cache_ttl_seconds: int = 30,
    ) -> None:
        self._engine = engine
        self._models = model_supplier
        self._clock = clock
        self._ttl = max(1, min(cache_ttl_seconds, 300))
        self._cached_at = 0.0
        self._model_index: dict[tuple[str, str], object] = {}

    def resolve(
        self,
        *,
        worker_id: str,
        runtime_id: str,
        provider_id: str,
        model_id: str,
    ) -> DestinationCatalogRecord | None:
        for record, target in self._records():
            if (
                record.worker_id == worker_id
                and record.runtime_id == runtime_id
                and record.provider_id == provider_id
                and record.model_id == model_id
                and bool(target.get("global_source_access"))
            ):
                return record
        return None

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> DestinationDescriptor | None:
        for descriptor in self._scoped_descriptors(
            tenant_id, project_id
        ):
            if descriptor.destination_id == destination_id:
                return descriptor
        return None

    def list(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, object],
    ) -> tuple[Sequence[DestinationDescriptor], str | None]:
        if limit < 1 or limit > 200:
            raise SourceControlProductionAdapterError(
                "destination_limit_invalid"
            )
        after = self._decode_destination_cursor(cursor)
        values = sorted(
            self._scoped_descriptors(tenant_id, project_id),
            key=lambda item: item.destination_id,
        )
        selected = [
            item
            for item in values
            if (after is None or item.destination_id > after)
            and (
                not filters.get("provider_id")
                or item.provider_id == str(filters["provider_id"])
            )
            and (
                not filters.get("model_class")
                or item.model_class == str(filters["model_class"])
            )
        ][: limit + 1]
        visible = selected[:limit]
        return (
            tuple(visible),
            (
                base64.urlsafe_b64encode(
                    visible[-1].destination_id.encode("ascii")
                )
                .decode("ascii")
                .rstrip("=")
                if len(selected) > limit and visible
                else None
            ),
        )

    def _scoped_descriptors(
        self, tenant_id: str, project_id: str
    ) -> list[DestinationDescriptor]:
        descriptors: list[DestinationDescriptor] = []
        for record, target in self._records():
            if not self._scope_allowed(
                target, tenant_id=tenant_id, project_id=project_id
            ):
                continue
            descriptors.append(
                DestinationDescriptor.create(
                    worker_id=record.worker_id,
                    worker_kind=record.worker_kind,
                    runtime_id=record.runtime_id,
                    runtime_kind=record.runtime_kind,
                    provider_id=record.provider_id,
                    model_id=record.model_id,
                    model_class=record.model_class,
                    provider_location=record.provider_location,
                    data_residency=record.data_residency,
                )
            )
        return descriptors

    def _records(
        self,
    ) -> list[tuple[DestinationCatalogRecord, Mapping[str, object]]]:
        model_index = self._model_catalog()
        with Session(self._engine) as db:
            workers = list(db.exec(select(AgentInfoDB)).all())
        records: list[
            tuple[DestinationCatalogRecord, Mapping[str, object]]
        ] = []
        for worker in workers:
            if (
                worker.role != "worker"
                or worker.status != "online"
                or not worker.registration_validated
            ):
                continue
            worker_id = str(worker.name or worker.url)
            for raw in list(worker.runtime_targets or []):
                if not isinstance(raw, Mapping):
                    continue
                target = dict(raw)
                runtime_id = str(target.get("runtime_id") or "")
                runtime_kind = str(
                    target.get("runtime_kind")
                    or target.get("kind")
                    or ""
                )
                provider_id = str(target.get("provider_id") or "")
                model_provider_id = str(
                    target.get("model_provider_id") or provider_id
                )
                model_ids = target.get("model_ids")
                if not isinstance(model_ids, list):
                    model_ids = [target.get("model_id")]
                for raw_model_id in model_ids:
                    model_id = str(raw_model_id or "")
                    model = model_index.get(
                        (model_provider_id, model_id)
                    )
                    if (
                        not runtime_id
                        or not runtime_kind
                        or model is None
                    ):
                        continue
                    try:
                        location = ProviderLocation(
                            str(target["provider_location"])
                        )
                        data_residency = str(
                            target["data_residency"]
                        )
                        model_class = str(
                            target.get("model_class")
                            or self._model_class(model)
                        )
                    except (KeyError, ValueError):
                        continue
                    if not data_residency or not model_class:
                        continue
                    records.append(
                        (
                            DestinationCatalogRecord(
                                worker_id=worker_id,
                                worker_kind=str(
                                    target.get("worker_kind")
                                    or worker.role
                                ),
                                runtime_id=runtime_id,
                                runtime_kind=runtime_kind,
                                provider_id=provider_id,
                                model_id=model_id,
                                model_class=model_class,
                                provider_location=location,
                                data_residency=data_residency,
                                enabled=bool(
                                    target.get("enabled", True)
                                ),
                                authorization_status=(
                                    "authorized"
                                    if self._model_available(model)
                                    and bool(
                                        target.get(
                                            "source_access_authorized"
                                        )
                                    )
                                    else "denied"
                                ),
                            ),
                            target,
                        )
                    )
        return [
            (record, target)
            for record, target in records
            if record.enabled
            and record.authorization_status == "authorized"
        ]

    def _model_catalog(self) -> dict[tuple[str, str], object]:
        now = float(self._clock())
        if now - self._cached_at >= self._ttl:
            self._model_index = {
                (
                    str(
                        getattr(item, "provider_id", None)
                        or (
                            item.get("provider_id")
                            if isinstance(item, Mapping)
                            else ""
                        )
                    ),
                    str(
                        getattr(item, "model_id", None)
                        or (
                            item.get("model_id")
                            if isinstance(item, Mapping)
                            else ""
                        )
                    ),
                ): item
                for item in self._models()
            }
            self._cached_at = now
        return self._model_index

    @staticmethod
    def _model_available(model: object) -> bool:
        availability = getattr(model, "availability", None)
        health = getattr(model, "health", None)
        return (
            str(getattr(availability, "value", availability))
            == ModelAvailability.AVAILABLE.value
            and str(getattr(health, "value", health))
            != ModelHealth.UNAVAILABLE.value
        )

    @staticmethod
    def _model_class(model: object) -> str:
        capabilities = getattr(model, "capabilities", ())
        return next(
            (
                str(item).removeprefix("class:")
                for item in capabilities
                if str(item).startswith("class:")
            ),
            "general",
        )

    @staticmethod
    def _scope_allowed(
        target: Mapping[str, object],
        *,
        tenant_id: str,
        project_id: str,
    ) -> bool:
        if target.get("global_source_access") is True:
            return True
        if (
            target.get("tenant_id") == tenant_id
            and target.get("project_id") == project_id
        ):
            return True
        scopes = target.get("source_access_scopes")
        return isinstance(scopes, list) and any(
            isinstance(scope, Mapping)
            and scope.get("tenant_id") == tenant_id
            and scope.get("project_id") == project_id
            for scope in scopes
        )

    @staticmethod
    def _decode_destination_cursor(cursor: str | None) -> str | None:
        if cursor is None:
            return None
        try:
            value = cursor + "=" * (-len(cursor) % 4)
            return base64.urlsafe_b64decode(value).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceControlProductionAdapterError(
                "destination_cursor_invalid"
            ) from exc


class SQLSourceRevisionAccessCatalog:
    """Tenant/project-scoped source revision projection for access evaluation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> EffectiveSourceRevision | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id
                    == source_revision_id,
                    SourceRevisionDB.tenant_id == tenant_id,
                    SourceRevisionDB.project_id == project_id,
                )
            ).first()
            return None if row is None else self._record(row)

    def list_revisions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[EffectiveSourceRevision], str | None]:
        after = self._decode_cursor(cursor)
        with Session(self._engine) as db:
            statement = select(SourceRevisionDB).where(
                SourceRevisionDB.tenant_id == tenant_id,
                SourceRevisionDB.project_id == project_id,
            )
            if after is not None:
                statement = statement.where(
                    SourceRevisionDB.source_revision_id > after
                )
            if filters.get("source_type"):
                statement = statement.where(
                    SourceRevisionDB.connector_type
                    == filters["source_type"]
                )
            if filters.get("sensitivity"):
                statement = statement.where(
                    SourceRevisionDB.sensitivity
                    == filters["sensitivity"]
                )
            rows = list(
                db.exec(
                    statement.order_by(
                        SourceRevisionDB.source_revision_id
                    ).limit(limit + 1)
                ).all()
            )
        visible = rows[:limit]
        return (
            tuple(self._record(row) for row in visible),
            (
                base64.urlsafe_b64encode(
                    visible[-1].source_revision_id.encode("ascii")
                )
                .decode("ascii")
                .rstrip("=")
                if len(rows) > limit and visible
                else None
            ),
        )

    @staticmethod
    def _record(row: SourceRevisionDB) -> EffectiveSourceRevision:
        return EffectiveSourceRevision(
            source_revision_id=row.source_revision_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_type=row.connector_type,
            sensitivity=row.sensitivity,
            revision_digest=row.revision_digest,
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> str | None:
        if cursor is None:
            return None
        try:
            value = cursor + "=" * (-len(cursor) % 4)
            return base64.urlsafe_b64decode(value).decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceControlProductionAdapterError(
                "source_revision_cursor_invalid"
            ) from exc


class ScopedEffectiveDestinationCatalog:
    def __init__(
        self,
        catalog: ScopedWorkerModelDestinationCatalog,
        *,
        tenant_id: str,
        project_id: str,
    ) -> None:
        self._catalog = catalog
        self._tenant_id = tenant_id
        self._project_id = project_id

    def get_destination(
        self, *, destination_id: str
    ) -> DestinationDescriptor | None:
        return self._catalog.get(
            tenant_id=self._tenant_id,
            project_id=self._project_id,
            destination_id=destination_id,
        )

    def list_destinations(
        self,
        *,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[DestinationDescriptor], str | None]:
        return self._catalog.list(
            tenant_id=self._tenant_id,
            project_id=self._project_id,
            cursor=cursor,
            limit=limit,
            filters=filters,
        )


class PersistentGrantEffectivePolicy:
    """Evaluate effective access from exact active grant coordinates."""

    def __init__(self, engine: Engine, *, clock=time.time) -> None:
        self._engine = engine
        self._clock = clock

    def evaluate(
        self,
        *,
        source_revision: EffectiveSourceRevision,
        destination: DestinationDescriptor,
        operation: object,
        transformation: object,
        purpose: str,
    ) -> EffectivePolicyEvaluation:
        operation_value = str(getattr(operation, "value", operation))
        transformation_value = str(
            getattr(transformation, "value", transformation)
        )
        with Session(self._engine) as db:
            grants = list(
                db.exec(
                    select(SourceAccessGrantDB).where(
                        SourceAccessGrantDB.tenant_id
                        == source_revision.tenant_id,
                        SourceAccessGrantDB.project_id
                        == source_revision.project_id,
                        SourceAccessGrantDB.source_revision_id
                        == source_revision.source_revision_id,
                        SourceAccessGrantDB.destination_id
                        == destination.destination_id,
                        SourceAccessGrantDB.operation == operation_value,
                        SourceAccessGrantDB.transformation
                        == transformation_value,
                        SourceAccessGrantDB.purpose == purpose,
                        SourceAccessGrantDB.state == "active",
                        SourceAccessGrantDB.expires_at_epoch
                        > float(self._clock()),
                    )
                ).all()
            )
        grant = max(
            grants, key=lambda item: item.grant_version, default=None
        )
        if grant is None:
            return EffectivePolicyEvaluation(
                decision="deny",
                reason_codes=("active_grant_not_found",),
                matched_rule_path=(),
                default_applied=True,
                approval_requirement=None,
                policy_digest=hashlib.sha256(
                    b"ananta.source-control.no-active-grant.v1"
                ).hexdigest(),
            )
        policy_digest = self._resolve_policy_snapshot_digest(
            tenant_id=source_revision.tenant_id,
            project_id=source_revision.project_id,
            policy_snapshot_id=grant.policy_version,
            stored_digest=grant.policy_snapshot_digest,
        )
        return EffectivePolicyEvaluation(
            decision="allow",
            reason_codes=("active_grant",),
            matched_rule_path=(grant.grant_id,),
            default_applied=False,
            approval_requirement=None,
            policy_digest=policy_digest,
        )

    def _resolve_policy_snapshot_digest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_snapshot_id: str,
        stored_digest: str | None,
    ) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", str(stored_digest or "")):
            raise SourceControlProductionAdapterError(
                "policy_snapshot_digest_missing",
                status_code=409,
            )
        with Session(self._engine) as db:
            rows = db.exec(
                select(ContextPolicyVersionDB).where(
                    ContextPolicyVersionDB.tenant_id == tenant_id,
                    ContextPolicyVersionDB.project_id == project_id,
                    ContextPolicyVersionDB.state == "active",
                )
            ).all()
        matched = [
            row
            for row in rows
            if derive_policy_snapshot_id(
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                policy_id=row.policy_id,
                version=int(row.version),
                policy_digest=row.policy_digest,
            )
            == policy_snapshot_id
        ]
        if len(matched) != 1 or matched[0].policy_digest != stored_digest:
            raise SourceControlProductionAdapterError(
                "policy_snapshot_binding_mismatch",
                status_code=409,
            )
        return str(stored_digest)


def derive_policy_snapshot_id(
    *,
    tenant_id: str,
    project_id: str,
    policy_id: str,
    version: int,
    policy_digest: str,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "policy_id": policy_id,
        "version": version,
        "policy_digest": policy_digest,
    }
    return "cpv_" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def build_scoped_effective_access_service(
    *,
    engine: Engine,
    destinations: ScopedWorkerModelDestinationCatalog,
    tenant_id: str,
    project_id: str,
) -> EffectiveSourceAccessService:
    return EffectiveSourceAccessService(
        sources=SQLSourceRevisionAccessCatalog(engine),
        destinations=ScopedEffectiveDestinationCatalog(
            destinations,
            tenant_id=tenant_id,
            project_id=project_id,
        ),
        policy=PersistentGrantEffectivePolicy(engine),
    )


class ContainedArtifactDeletionService:
    """Delete approved index artifacts only under the configured Hub root."""

    def __init__(
        self,
        *,
        engine: Engine,
        artifact_root: str | Path,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._root = Path(artifact_root).resolve()
        self._clock = clock

    def is_deleted(self, *, knowledge_index_id: str) -> bool:
        with Session(self._engine) as db:
            row = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            return bool(row is not None and row.state == "completed")

    def requires_deletion(self, *, knowledge_index_id: str) -> bool:
        with Session(self._engine) as db:
            binding = db.get(
                KnowledgeIndexSourceBindingDB, knowledge_index_id
            )
            if binding is None:
                return False
            if binding.artifact_manifest_digest:
                return not self.is_deleted(
                    knowledge_index_id=knowledge_index_id
                )
            return bool(
                db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                        == knowledge_index_id,
                        KnowledgeIndexRunSourceBindingDB.artifact_manifest_digest.is_not(
                            None
                        ),
                    )
                ).first()
            )

    def delete(
        self,
        *,
        principal: SourceControlPrincipal,
        knowledge_index_id: str,
        expected_version: int,
        approval_id: str | None,
    ) -> Mapping[str, object]:
        if "admin" not in principal.roles:
            raise SourceControlProductionAdapterError(
                "purge_admin_required", status_code=403
            )
        if not approval_id:
            raise SourceControlProductionAdapterError(
                "artifact_retention_approval_required", status_code=409
            )
        with Session(self._engine) as db:
            binding = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id
                    == principal.tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id
                    == principal.project_id,
                )
            ).first()
            if binding is None:
                raise SourceControlProductionAdapterError(
                    "knowledge_index_not_found", status_code=404
                )
            if (
                binding.status != "tombstoned"
                or int(binding.lock_version) != expected_version
            ):
                raise SourceControlProductionAdapterError(
                    "index_version_conflict", status_code=412
                )
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id
                    == principal.tenant_id,
                    ActiveKnowledgeIndexDB.project_id
                    == principal.project_id,
                    ActiveKnowledgeIndexDB.knowledge_index_id
                    == knowledge_index_id,
                )
            ).first()
            if active is not None:
                raise SourceControlProductionAdapterError(
                    "active_index_cannot_be_purged", status_code=409
                )
            index = db.get(KnowledgeIndexDB, knowledge_index_id)
            if index is None:
                raise SourceControlProductionAdapterError(
                    "knowledge_index_not_found", status_code=404
                )
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is not None:
                if (
                    receipt.tenant_id != principal.tenant_id
                    or receipt.project_id != principal.project_id
                    or receipt.approval_id != approval_id
                ):
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_conflict", status_code=409
                    )
                if receipt.state == "completed":
                    return {
                        "status": "completed",
                        "manifest_digest": receipt.manifest_digest,
                    }
                raw_output = Path(str(index.output_dir or ""))
                if not raw_output.exists():
                    return self._recover_claimed_deletion(
                        index=index,
                        receipt=receipt,
                    )
            metadata = dict(index.index_metadata or {})
            if metadata.get("retention_released") is not True:
                raise SourceControlProductionAdapterError(
                    "artifact_retention_not_released", status_code=409
                )
            nonterminal = db.exec(
                select(KnowledgeIndexRunDB).where(
                    KnowledgeIndexRunDB.knowledge_index_id
                    == knowledge_index_id
                )
            ).all()
            if any(
                str(run.status) not in _TERMINAL_RUN_STATES
                for run in nonterminal
            ):
                raise SourceControlProductionAdapterError(
                    "artifact_run_reference_active", status_code=409
                )
            output_dir = self._contained_output(index)
            manifest_path = self._contained_manifest(
                index, output_dir
            )
            manifest_digest = self._sha256(manifest_path)
            expected_digests = {
                str(binding.artifact_manifest_digest or "")
            }
            expected_digests.update(
                str(row.artifact_manifest_digest or "")
                for row in db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                        == knowledge_index_id
                    )
                ).all()
            )
            expected_digests.discard("")
            if manifest_digest not in expected_digests:
                raise SourceControlProductionAdapterError(
                    "artifact_manifest_digest_mismatch", status_code=409
                )
            request_digest = hashlib.sha256(
                json.dumps(
                    {
                        "tenant_id": principal.tenant_id,
                        "project_id": principal.project_id,
                        "knowledge_index_id": knowledge_index_id,
                        "expected_version": expected_version,
                        "approval_id": approval_id,
                        "manifest_digest": manifest_digest,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest()
            quarantine = (
                self._root
                / ".source-control-purge"
                / f"{knowledge_index_id}.{request_digest[:16]}"
            )
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is not None:
                if receipt.request_digest != request_digest:
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_conflict", status_code=409
                    )
                if receipt.state == "completed":
                    return {
                        "status": "completed",
                        "manifest_digest": manifest_digest,
                    }
                db.expunge(index)
            else:
                db.add(
                    SourceControlArtifactDeletionDB(
                        knowledge_index_id=knowledge_index_id,
                        tenant_id=principal.tenant_id,
                        project_id=principal.project_id,
                        request_digest=request_digest,
                        manifest_digest=manifest_digest,
                        approval_id=approval_id,
                        quarantine_path_digest=hashlib.sha256(
                            str(quarantine).encode("utf-8")
                        ).hexdigest(),
                        state="claimed",
                        created_at_epoch=float(self._clock()),
                    )
                )
                try:
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_in_progress",
                        status_code=409,
                    ) from exc
                db.expunge(index)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if output_dir.exists():
            if quarantine.exists():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_conflict", status_code=409
                )
            os.replace(output_dir, quarantine)
        if quarantine.exists():
            if quarantine.is_symlink() or not quarantine.is_dir():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_invalid", status_code=409
                )
            shutil.rmtree(quarantine)
        with Session(self._engine) as db:
            stored = db.get(KnowledgeIndexDB, knowledge_index_id)
            if stored is not None:
                metadata = dict(stored.index_metadata or {})
                metadata.update(
                    {
                        "artifacts_purged": True,
                        "artifact_manifest_digest": manifest_digest,
                    }
                )
                stored.index_metadata = metadata
                stored.output_dir = None
                stored.manifest_path = None
                stored.updated_at = float(self._clock())
                db.add(stored)
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is None:
                raise SourceControlProductionAdapterError(
                    "artifact_deletion_receipt_missing",
                    status_code=500,
                )
            receipt.state = "completed"
            receipt.completed_at_epoch = float(self._clock())
            db.add(receipt)
            db.commit()
        _LOG.info(
            "source_control_artifact_deleted index=%s manifest=%s actor=%s",
            knowledge_index_id,
            manifest_digest,
            principal.subject_id,
        )
        return {
            "status": "completed",
            "manifest_digest": manifest_digest,
        }

    def delete_approved(
        self,
        *,
        scope: object,
        index: object,
        expected_version: int,
        approval_id: str,
        approval_request_digest: str,
        approval_claim_id: str,
    ) -> Mapping[str, object]:
        """Defense-in-depth check before crossing the filesystem boundary."""

        tenant_id = str(getattr(scope, "tenant_id", ""))
        project_id = str(getattr(scope, "project_id", ""))
        actor_id = str(getattr(scope, "actor_id", ""))
        roles = frozenset(getattr(scope, "roles", ()))
        knowledge_index_id = str(
            getattr(index, "knowledge_index_id", "")
        )
        with Session(self._engine) as db:
            approval = db.get(
                SourceControlPurgeApprovalDB, approval_id
            )
            if (
                approval is None
                or approval.tenant_id != tenant_id
                or approval.project_id != project_id
                or approval.action != "purge"
                or approval.object_type != "knowledge_index"
                or approval.object_id != knowledge_index_id
                or approval.request_digest != approval_request_digest
                or approval.claim_id != approval_claim_id
                or approval.state not in {"claimed", "consumed"}
                or (
                    approval.state == "claimed"
                    and float(approval.claim_expires_at_epoch or 0)
                    <= float(self._clock())
                )
            ):
                raise SourceControlProductionAdapterError(
                    "purge_approval_not_claimed", status_code=409
                )

        class _Principal:
            subject_id = actor_id

        principal = _Principal()
        principal.tenant_id = tenant_id
        principal.project_id = project_id
        principal.roles = roles
        return self.delete(
            principal=principal,  # type: ignore[arg-type]
            knowledge_index_id=knowledge_index_id,
            expected_version=expected_version,
            approval_id=approval_id,
        )

    def _contained_output(self, index: KnowledgeIndexDB) -> Path:
        raw = Path(str(index.output_dir or ""))
        if not raw.is_absolute() or raw.is_symlink():
            raise SourceControlProductionAdapterError(
                "artifact_output_path_invalid", status_code=409
            )
        resolved = raw.resolve(strict=True)
        try:
            relative = resolved.relative_to(self._root)
        except ValueError as exc:
            raise SourceControlProductionAdapterError(
                "artifact_output_outside_root", status_code=409
            ) from exc
        if not relative.parts or not resolved.is_dir():
            raise SourceControlProductionAdapterError(
                "artifact_output_path_invalid", status_code=409
            )
        return resolved

    def _recover_claimed_deletion(
        self,
        *,
        index: KnowledgeIndexDB,
        receipt: SourceControlArtifactDeletionDB,
    ) -> Mapping[str, object]:
        quarantine = (
            self._root
            / ".source-control-purge"
            / (
                f"{receipt.knowledge_index_id}."
                f"{receipt.request_digest[:16]}"
            )
        )
        if (
            hashlib.sha256(str(quarantine).encode("utf-8")).hexdigest()
            != receipt.quarantine_path_digest
        ):
            raise SourceControlProductionAdapterError(
                "artifact_quarantine_digest_mismatch", status_code=409
            )
        if quarantine.exists():
            if quarantine.is_symlink() or not quarantine.is_dir():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_invalid", status_code=409
                )
            resolved = quarantine.resolve(strict=True)
            try:
                resolved.relative_to(self._root)
            except ValueError as exc:
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_outside_root", status_code=409
                ) from exc
            shutil.rmtree(resolved)
        with Session(self._engine) as db:
            stored = db.get(
                KnowledgeIndexDB, receipt.knowledge_index_id
            )
            if stored is not None:
                metadata = dict(stored.index_metadata or {})
                metadata.update(
                    {
                        "artifacts_purged": True,
                        "artifact_manifest_digest": (
                            receipt.manifest_digest
                        ),
                    }
                )
                stored.index_metadata = metadata
                stored.output_dir = None
                stored.manifest_path = None
                stored.updated_at = float(self._clock())
                db.add(stored)
            current = db.get(
                SourceControlArtifactDeletionDB,
                receipt.knowledge_index_id,
            )
            if current is None or current.state != "claimed":
                raise SourceControlProductionAdapterError(
                    "artifact_deletion_receipt_invalid",
                    status_code=409,
                )
            current.state = "completed"
            current.completed_at_epoch = float(self._clock())
            db.add(current)
            db.commit()
        return {
            "status": "completed",
            "manifest_digest": receipt.manifest_digest,
        }

    @staticmethod
    def _contained_manifest(
        index: KnowledgeIndexDB, output_dir: Path
    ) -> Path:
        raw = Path(str(index.manifest_path or ""))
        if not raw.is_absolute() or raw.is_symlink():
            raise SourceControlProductionAdapterError(
                "artifact_manifest_path_invalid", status_code=409
            )
        resolved = raw.resolve(strict=True)
        try:
            resolved.relative_to(output_dir)
        except ValueError as exc:
            raise SourceControlProductionAdapterError(
                "artifact_manifest_outside_output", status_code=409
            ) from exc
        if not resolved.is_file():
            raise SourceControlProductionAdapterError(
                "artifact_manifest_path_invalid", status_code=409
            )
        return resolved

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    "ContainedArtifactDeletionService",
    "HubBoundSourceIndexSubmissionAdapter",
    "HubSourceControlOperationsAdapter",
    "PersistentGrantEffectivePolicy",
    "SQLSourceRevisionAccessCatalog",
    "ScopedEffectiveDestinationCatalog",
    "ScopedWorkerModelDestinationCatalog",
    "SourceControlProductionAdapterError",
    "build_scoped_effective_access_service",
]
