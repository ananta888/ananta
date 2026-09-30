"""Concrete Hub adapters for the canonical source-control HTTP API.

``SourceControlApiRuntime`` is the public port behind the Source Control v1
blueprint. It keeps its dataclass fields (other Hub code reads ``engine``,
``reads``, ``projection`` and friends) and delegates each API family to a
composed facet, every one a keyword-only overridable field with a
production default built in ``__post_init__`` (SRP, DIP):

* ``SourceControlConnectionCommands`` -- connections and content admission
* ``SourceControlGrantCatalogApi`` -- catalogs, grants, index access
* ``SourceControlIndexLifecycleCommands`` -- history, lifecycle, bulk
* ``SourceControlOperationGateway`` -- operations, reads, downloads, CodeHug
* ``SourceControlPolicyAccessApi`` -- effective access, Context Policy

The SQL adapters live in ``source_control_read_repository``,
``source_control_index_lifecycle_repository``,
``source_control_job_event_repository`` and
``source_control_operation_store``; they stay importable from here.
``build_source_control_api_runtime`` remains defined in this module because
the Source Control boundary gate only allows it to be called from the
canonical bootstrap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine

from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)
from agent.services.source_control_index_lifecycle_repository import (
    SQLSourceIndexLifecycleRepository,
)
from agent.services.source_control_job_event_repository import (
    SQLSourceControlJobEventRepository,
)
from agent.services.source_control_job_events import (
    SourceControlJobEventService,
)
from agent.services.source_control_operation_store import (
    SQLSourceControlOperationStore,
)
from agent.services.source_control_prepare_index_access import (
    SourceControlPrepareIndexAccessService,
)
from agent.services.source_control_production_adapters import (
    ContainedArtifactDeletionService,
)
from agent.services.source_control_projection_service import (
    SourceControlProjectionService,
)
from agent.services.source_control_purge_approval import (
    SQLSourceControlPurgeApprovalStore,
)
from agent.services.source_control_read_repository import (
    SQLSourceControlReadRepository,
)
from agent.services.source_control_runtime_connections import (
    SourceControlConnectionCommands,
)
from agent.services.source_control_runtime_grants import (
    SourceControlGrantCatalogApi,
)
from agent.services.source_control_runtime_lifecycle import (
    SourceControlIndexLifecycleCommands,
)
from agent.services.source_control_runtime_operations import (
    SourceControlOperationGateway,
)
from agent.services.source_control_runtime_policy_access import (
    SourceControlPolicyAccessApi,
)
from agent.services.source_control_runtime_support import (
    LifecycleAuditLog,
    SourceControlApiRuntimeError,
    runtime_principal,
    wire,
)
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleService,
)


def _facet() -> object:
    return field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class SourceControlApiRuntime:
    engine: Engine
    reads: SQLSourceControlReadRepository
    projection: SourceControlProjectionService
    lifecycle: SourceIndexLifecycleService
    events: SourceControlJobEventService
    idempotency: SQLSourceControlOperationStore
    access: object | None = None
    operations: object | None = None
    context_policy: object | None = None
    artifact_deletion: ContainedArtifactDeletionService | None = None
    content_admission: object | None = None
    catalogs: object | None = None
    grants: object | None = None
    index_access: object | None = None
    connection_intents: object | None = None
    codehug_mutations: object | None = None
    artifact_downloads: object | None = None
    connection_commands: SourceControlConnectionCommands | None = _facet()
    grant_catalog: SourceControlGrantCatalogApi | None = _facet()
    index_lifecycle: SourceControlIndexLifecycleCommands | None = _facet()
    operation_gateway: SourceControlOperationGateway | None = _facet()
    policy_access: SourceControlPolicyAccessApi | None = _facet()

    def __post_init__(self) -> None:
        defaults = {
            "connection_commands": lambda: SourceControlConnectionCommands(
                engine=self.engine,
                idempotency=self.idempotency,
                connection_intents=self.connection_intents,
                content_admission=self.content_admission,
            ),
            "grant_catalog": lambda: SourceControlGrantCatalogApi(
                catalogs=self.catalogs,
                grants=self.grants,
                index_access=self.index_access,
            ),
            "index_lifecycle": lambda: SourceControlIndexLifecycleCommands(
                reads=self.reads,
                projection=self.projection,
                lifecycle=self.lifecycle,
                idempotency=self.idempotency,
                operations=self.operations,
            ),
            "operation_gateway": lambda: SourceControlOperationGateway(
                projection=self.projection,
                idempotency=self.idempotency,
                operations=self.operations,
                catalogs=self.catalogs,
                artifact_downloads=self.artifact_downloads,
                codehug_mutations=self.codehug_mutations,
            ),
            "policy_access": lambda: SourceControlPolicyAccessApi(
                engine=self.engine,
                access=self.access,
                context_policy=self.context_policy,
            ),
        }
        for name, build in defaults.items():
            if getattr(self, name) is None:
                object.__setattr__(self, name, build())


    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None:
        return self.reads.binding(
            resource_kind=resource_kind, resource_id=resource_id
        )

    def validate_connection(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self.connection_commands.validate_connection(
            principal=principal,
            payload=payload,
        )

    def create_connection(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.connection_commands.create_connection(
            principal=principal,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def validate_content_admission(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self.connection_commands.validate_content_admission(
            principal=principal,
            payload=payload,
        )

    def create_content_admission(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.connection_commands.create_content_admission(
            principal=principal,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def list_source_control_catalog(
        self,
        *,
        principal: object,
        catalog: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        return self.grant_catalog.list_source_control_catalog(
            principal=principal,
            catalog=catalog,
            project_id=project_id,
            cursor=cursor,
            limit=limit,
            filters=filters,
        )

    def list_grant_presets(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        return self.grant_catalog.list_grant_presets(
            principal=principal,
            project_id=project_id,
            cursor=cursor,
            limit=limit,
            filters=filters,
        )

    def list_grants(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        return self.grant_catalog.list_grants(
            principal=principal,
            project_id=project_id,
            cursor=cursor,
            limit=limit,
            filters=filters,
        )

    def create_grant(
        self,
        *,
        principal: object,
        project_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.grant_catalog.create_grant(
            principal=principal,
            project_id=project_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def revoke_grant(
        self,
        *,
        principal: object,
        project_id: str,
        grant_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.grant_catalog.revoke_grant(
            principal=principal,
            project_id=project_id,
            grant_id=grant_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def prepare_index_access_options(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
    ) -> Mapping[str, object]:
        return self.grant_catalog.prepare_index_access_options(
            principal=principal,
            project_id=project_id,
            connection_id=connection_id,
        )

    def prepare_index_access(
        self,
        *,
        principal: object,
        project_id: str,
        connection_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.grant_catalog.prepare_index_access(
            principal=principal,
            project_id=project_id,
            connection_id=connection_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def list_connections(
        self,
        *,
        principal: object,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]:
        return wire(
            self.projection.list(
                principal=runtime_principal(principal),
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        )

    def get_connection(
        self, *, principal: object, connection_id: str
    ) -> tuple[Mapping[str, object], str]:
        projection = self.projection.get(
            principal=runtime_principal(principal),
            connection_id=connection_id,
        )
        return wire(projection), projection.etag

    def run_history(
        self,
        *,
        principal: object,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        return self.index_lifecycle.run_history(
            principal=principal,
            connection_id=connection_id,
            cursor=cursor,
            limit=limit,
        )

    def compare_indices(
        self,
        *,
        principal: object,
        left_index_id: str,
        right_index_id: str,
    ) -> Mapping[str, object]:
        return self.index_lifecycle.compare_indices(
            principal=principal,
            left_index_id=left_index_id,
            right_index_id=right_index_id,
        )

    def mutate(
        self,
        *,
        principal: object,
        operation: str,
        resource_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.index_lifecycle.mutate(
            principal=principal,
            operation=operation,
            resource_id=resource_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    def dispatch_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.operation_gateway.dispatch_operation(
            principal=principal,
            operation=operation,
            connection_id=connection_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            payload=payload,
        )

    def graph(
        self,
        *,
        principal: object,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.operation_gateway.graph(
            principal=principal,
            connection_id=connection_id,
            parameters=parameters,
        )

    def query(
        self,
        *,
        principal: object,
        connection_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.operation_gateway.query(
            principal=principal,
            connection_id=connection_id,
            payload=payload,
        )

    def artifact_status(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
    ) -> Mapping[str, object]:
        return self.operation_gateway.artifact_status(
            principal=principal,
            connection_id=connection_id,
            artifact_id=artifact_id,
        )

    def bulk_plan(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self.index_lifecycle.bulk_plan(
            principal=principal,
            payload=payload,
        )

    def bulk_execute(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.index_lifecycle.bulk_execute(
            principal=principal,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def poll_events(
        self,
        *,
        principal: object,
        after_sequence: int,
        limit: int,
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        return self.events.poll(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def access_preview(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self.policy_access.access_preview(
            principal=principal,
            payload=payload,
        )

    def access_matrix(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        return self.policy_access.access_matrix(
            principal=principal,
            payload=payload,
        )

    def context_policy_list(
        self, *, principal: object, cursor: str | None, limit: int
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_list(
            principal=principal,
            cursor=cursor,
            limit=limit,
        )

    def context_policy_versions(
        self,
        *,
        principal: object,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_versions(
            principal=principal,
            policy_id=policy_id,
            cursor=cursor,
            limit=limit,
        )

    def context_policy_detail(
        self, *, principal: object, policy_id: str, version: int
    ) -> tuple[Mapping[str, object], str]:
        return self.policy_access.context_policy_detail(
            principal=principal,
            policy_id=policy_id,
            version=version,
        )

    def context_policy_active(
        self, *, principal: object, policy_id: str
    ) -> tuple[Mapping[str, object], str]:
        return self.policy_access.context_policy_active(
            principal=principal,
            policy_id=policy_id,
        )

    def context_policy_draft(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_draft(
            principal=principal,
            policy_id=policy_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def context_policy_lint(
        self, *, principal: object, policy_id: str, version: int
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_lint(
            principal=principal,
            policy_id=policy_id,
            version=version,
        )

    def context_policy_preview(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_preview(
            principal=principal,
            policy_id=policy_id,
            payload=payload,
        )

    def context_policy_transition(
        self,
        *,
        principal: object,
        operation: str,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_transition(
            principal=principal,
            operation=operation,
            policy_id=policy_id,
            version=version,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def context_policy_rollback(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.policy_access.context_policy_rollback(
            principal=principal,
            policy_id=policy_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )

    def artifact_download(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream:
        return self.operation_gateway.artifact_download(
            principal=principal,
            connection_id=connection_id,
            artifact_id=artifact_id,
            range_header=range_header,
        )

    def codehug_mutation(
        self,
        *,
        principal: object,
        mutation_intent_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        return self.operation_gateway.codehug_mutation(
            principal=principal,
            mutation_intent_id=mutation_intent_id,
            idempotency_key=idempotency_key,
        )


def build_source_control_api_runtime(
    *,
    engine: Engine,
    access: object | None = None,
    operations: object | None = None,
    context_policy: object | None = None,
    artifact_deletion: ContainedArtifactDeletionService | None = None,
    content_admission: object | None = None,
    catalogs: object | None = None,
    grants: object | None = None,
    destinations: object | None = None,
    index_access: object | None = None,
    connection_intents: object | None = None,
    codehug_mutations: object | None = None,
    artifact_downloads: object | None = None,
) -> SourceControlApiRuntime:
    reads = SQLSourceControlReadRepository(engine)
    projection = SourceControlProjectionService(reads)
    idempotency = SQLSourceControlOperationStore(engine)
    resolved_index_access = index_access
    if (
        resolved_index_access is None
        and destinations is not None
        and context_policy is not None
        and grants is not None
    ):
        resolved_index_access = SourceControlPrepareIndexAccessService(
            projections=projection,
            bindings=reads,
            destinations=destinations,
            policies=context_policy,
            grants=grants,
            idempotency=idempotency,
        )
    repository = SQLSourceIndexLifecycleRepository(
        engine,
        artifact_deletion=artifact_deletion,
    )
    purge_approvals = SQLSourceControlPurgeApprovalStore(engine)
    return SourceControlApiRuntime(
        engine=engine,
        reads=reads,
        projection=projection,
        lifecycle=SourceIndexLifecycleService(
            repository=repository,
            audit=LifecycleAuditLog(),
            approvals=purge_approvals,
            artifacts=artifact_deletion,
        ),
        events=SourceControlJobEventService(
            SQLSourceControlJobEventRepository(engine)
        ),
        idempotency=idempotency,
        access=access,
        operations=operations,
        context_policy=context_policy,
        artifact_deletion=artifact_deletion,
        content_admission=content_admission,
        catalogs=catalogs,
        grants=grants,
        index_access=resolved_index_access,
        connection_intents=connection_intents,
        codehug_mutations=codehug_mutations,
        artifact_downloads=artifact_downloads,
    )


__all__ = [
    "SQLSourceControlJobEventRepository",
    "SQLSourceControlOperationStore",
    "SQLSourceControlReadRepository",
    "SQLSourceIndexLifecycleRepository",
    "SourceControlApiRuntime",
    "SourceControlApiRuntimeError",
    "build_source_control_api_runtime",
]
