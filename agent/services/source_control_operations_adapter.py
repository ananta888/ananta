"""Canonical-scope Source Control operations over existing Hub services.

``HubSourceControlOperationsAdapter`` reuses the source refresh/registry and
CodeCompass graph/retrieval services for refresh, scan, index run, graph,
query and artifact-status operations of one tenant/project connection.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models import KnowledgeIndexDB
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexSourceBindingDB,
    SourceConnectionDB,
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
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)
from agent.services.source_control_adapter_common import (
    EmptyKnowledgeLinks,
    SingleIndexRepository,
    SourceControlProductionAdapterError,
    public_projection,
)
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from ananta_contracts.source_control import ConnectionState


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
        retrieval_factory: Callable[..., object] = KnowledgeIndexRetrievalService,
    ) -> None:
        self._engine = engine
        self._retrieval_factory = retrieval_factory
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
        scan_result = public_projection(result)
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
            "health": public_projection(health),
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
        retrieval = self._retrieval_factory(
            knowledge_index_repository=SingleIndexRepository(index),
            knowledge_link_repository=EmptyKnowledgeLinks(),
        )
        chunks = retrieval.search_records(
            query,
            limit=limit,
            task_kind="code_review",
            retrieval_intent="fuzzy_semantic",
            allowed_index_ids={str(index.id)},
        )
        matches = [public_projection(chunk) for chunk in chunks]
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
        result = public_projection(value)
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


__all__ = ["HubSourceControlOperationsAdapter"]
