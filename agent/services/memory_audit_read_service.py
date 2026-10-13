"""Project-authorized, content-free operator projection of memory decisions."""

from dataclasses import asdict, dataclass
from typing import Protocol

from agent.services.memory_diff_service import compare_memory_snapshots
from agent.services.project_access_authority import ProjectAccessPort, ProjectCapability
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.memory_access import (
    MemoryNamespace,
    MemoryScope,
    MemorySecurityMetadata,
    MemoryTarget,
    require_memory_identifier,
)


@dataclass(frozen=True)
class MemoryAuditQuery:
    page_size: int = 50
    after_sequence: int = 0
    task_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    channel_id: str | None = None
    scope: MemoryScope | None = None

    def assert_valid(self):
        if (type(self.page_size) is not int or not 1 <= self.page_size <= 200
                or type(self.after_sequence) is not int or not 0 <= self.after_sequence < 2**63):
            raise ValueError("memory_audit_query_invalid")
        for field in ("task_id", "run_id", "trace_id", "channel_id"):
            value = getattr(self, field)
            if value is not None:
                require_memory_identifier(value, field)
        if self.scope is not None and not isinstance(self.scope, MemoryScope):
            raise ValueError("memory_scope_invalid")


class MemoryAuditProjectReadPort(Protocol):
    def project_events(self, *, tenant_id: str, project_id: str,
                       query: MemoryAuditQuery) -> list[dict]: ...


class MemorySnapshotProjectReadPort(Protocol):
    def read_run(self, *, tenant_id: str, project_id: str, query: MemoryAuditQuery) -> list[dict]: ...


class MemoryQuarantineProjectReadPort(Protocol):
    def project_quarantine(self, *, tenant_id: str, project_id: str, limit: int = 200) -> list[dict]: ...


class MemoryAuditReadService:
    def __init__(self, *, access: ProjectAccessPort, repository: MemoryAuditProjectReadPort,
                 snapshots: MemorySnapshotProjectReadPort | None = None,
                 quarantine: MemoryQuarantineProjectReadPort | None = None):
        self._access, self._repository = access, repository
        self._snapshots, self._quarantine = snapshots, quarantine

    def page(self, *, principal: HubSourcePrincipal, project_id: str, query: MemoryAuditQuery) -> dict:
        query.assert_valid()
        scope = self._authorize_scope(principal, project_id)
        rows = self._repository.project_events(tenant_id=scope.tenant_id, project_id=scope.project_id, query=query)
        items = [self._project(row, tenant_id=scope.tenant_id, project_id=scope.project_id)
                 for row in rows[:query.page_size]]
        return {"schema": "ananta.memory-audit-page.v1", "project_id": scope.project_id,
                "items": items, "next_after_sequence": items[-1]["sequence"] if len(rows) > query.page_size else None,
                "integrity": "event_digests_verified", "complete_chain_verified": False}

    def diffs(self, *, principal: HubSourcePrincipal, project_id: str, query: MemoryAuditQuery) -> dict:
        query.assert_valid()
        if not query.run_id or query.after_sequence:
            raise ValueError('memory_snapshot_run_required')
        scope = self._authorize_scope(principal, project_id)
        if self._snapshots is None:
            raise RuntimeError('memory_snapshots_unavailable')
        records = self._snapshots.read_run(tenant_id=scope.tenant_id, project_id=scope.project_id, query=query)
        groups = {}
        for record in records:
            namespace = record['namespace']
            if (namespace['tenant_id'], namespace['workspace_id'], record['run_id']) != (
                    scope.tenant_id, scope.project_id, query.run_id):
                raise ValueError('memory_snapshot_pair_binding_invalid')
            pair = groups.setdefault((record['channel_id'], record['scope']), {})
            if record['phase'] in pair:
                raise ValueError('memory_snapshot_pair_binding_invalid')
            pair[record['phase']] = record
        items = []
        for key in sorted(groups):
            pair = groups[key]
            if 'before' not in pair:
                raise ValueError('memory_snapshot_before_required')
            items.append(compare_memory_snapshots(pair['before'], pair.get('after')))
        return {'schema': 'ananta.memory-diff-page.v1', 'project_id': scope.project_id,
                'run_id': query.run_id, 'items': items, 'integrity': 'snapshot_and_audit_links_verified',
                'production_release_evidence': False}

    def quarantine_state(self, *, principal: HubSourcePrincipal, project_id: str, limit: int = 200) -> dict:
        """Content-free inventory of currently blocked result-memory entries and tree chunks."""
        if not isinstance(principal, HubSourcePrincipal):
            raise PermissionError("memory_audit_principal_scope_required")
        if self._quarantine is None:
            raise RuntimeError("memory_quarantine_read_unavailable")
        scope = self._authorize_scope(principal, project_id)
        items = self._quarantine.project_quarantine(
            tenant_id=scope.tenant_id, project_id=scope.project_id, limit=limit)
        return {"schema": "ananta.memory-quarantine-page.v1", "project_id": scope.project_id,
                "items": items, "integrity": "quarantine_state_projection",
                "production_release_evidence": False}

    def _authorize_scope(self, principal, project_id):
        if not principal.tenant_id or not principal.subject_id:
            raise PermissionError("memory_audit_principal_scope_required")
        require_memory_identifier(principal.tenant_id, "tenant_id")
        require_memory_identifier(principal.subject_id, "subject_id")
        require_memory_identifier(project_id, "project_id")
        if principal.project_id is not None and principal.project_id != project_id:
            raise PermissionError("memory_audit_project_denied")
        scope = self._access.require(tenant_id=principal.tenant_id, project_id=project_id,
            subject_id=principal.subject_id, capability=ProjectCapability.MANAGE,
            tenant_admin=bool({"admin", "tenant_admin"} & principal.roles))
        if (scope.tenant_id, scope.project_id, scope.subject_id, scope.capability) != (
                principal.tenant_id, project_id, principal.subject_id, ProjectCapability.MANAGE):
            raise PermissionError("memory_audit_project_denied")
        return scope

    @staticmethod
    def _project(row, *, tenant_id, project_id):
        payload = row["payload"]
        raw = payload["target"]
        target = MemoryTarget(raw["channel_id"], MemoryNamespace(**raw["namespace"]),
                              MemorySecurityMetadata.from_mapping(raw["security"]))
        if (row["tenant_id"] != tenant_id or target.namespace.tenant_id != tenant_id
                or target.namespace.workspace_id != project_id or payload["actor"]["tenant_id"] != tenant_id):
            raise ValueError("memory_audit_projection_scope_invalid")
        return {"event_id": row["id"], "sequence": row["sequence"], "event_hash": row["event_hash"],
                "previous_hash": row["previous_hash"], "created_at": row["created_at"],
                "retention_until": row["retention_until"], "actor_subject": payload["actor"]["subject"],
                "run_id": payload["run_id"], "trace_id": payload.get("trace_id"),
                "operation": payload["operation"], "allowed": payload["allowed"],
                "rule_id": payload["rule_id"], "reason_code": payload["reason_code"],
                "policy_revision": payload.get("policy_revision"), "policy_digest": payload.get("policy_digest"),
                "channel_id": target.channel_id, "namespace": asdict(target.namespace),
                "security": asdict(target.security), "event_kind": payload["event_kind"]}
