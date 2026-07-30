"""Hub-owned source index history, activation, rollback, and purge."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceIndexLifecycleError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceIndexLifecycleScope:
    tenant_id: str
    project_id: str
    actor_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class SourceIndexRecord:
    knowledge_index_id: str
    run_id: str
    connection_id: str
    source_revision_id: str
    revision_digest: str
    policy_digest: str
    manifest_digest: str
    status: str
    coverage: Mapping[str, int | float]
    artifact_verified: bool
    completed_at: str | None
    tombstoned: bool
    sensitive: bool


@dataclass(frozen=True)
class ActiveIndexPointer:
    connection_id: str
    knowledge_index_id: str
    source_revision_id: str
    generation: int


@dataclass(frozen=True)
class SourceIndexHistoryPage:
    items: tuple[SourceIndexRecord, ...]
    active: ActiveIndexPointer | None
    next_cursor: str | None


@dataclass(frozen=True)
class PurgeBlocker:
    blocker_kind: str
    blocker_id: str


class SourceIndexLifecycleRepositoryPort(Protocol):
    def list_history(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> SourceIndexHistoryPage: ...

    def get_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
    ) -> SourceIndexRecord | None: ...

    def get_active(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
    ) -> ActiveIndexPointer | None: ...

    def compare_and_activate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        knowledge_index_id: str,
        source_revision_id: str,
        expected_generation: int,
        actor_id: str,
        reason_code: str,
    ) -> ActiveIndexPointer: ...

    def disable_connection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        expected_version: int,
        actor_id: str,
    ) -> int: ...

    def tombstone_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
        actor_id: str,
        expected_version: int,
    ) -> int: ...

    def purge_blockers(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
    ) -> Sequence[PurgeBlocker]: ...

    def purge_index(
        self,
        *,
        tenant_id: str,
        project_id: str,
        knowledge_index_id: str,
        actor_id: str,
        expected_version: int,
        approval_id: str | None,
    ) -> None: ...


class SourceIndexLifecycleAuditPort(Protocol):
    def record(
        self,
        *,
        operation: str,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        connection_id: str,
        knowledge_index_id: str | None,
        reason_code: str,
    ) -> None: ...


class SourceIndexPurgeApprovalPort(Protocol):
    def claim(
        self,
        *,
        approval_id: str,
        tenant_id: str,
        project_id: str,
        action: str,
        object_type: str,
        object_id: str,
        request_digest: str,
        claim_id: str,
    ) -> None: ...

    def consume(
        self,
        *,
        approval_id: str,
        request_digest: str,
        claim_id: str,
    ) -> None: ...


class SourceIndexArtifactDeletionPort(Protocol):
    def delete_approved(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        index: SourceIndexRecord,
        expected_version: int,
        approval_id: str,
        approval_request_digest: str,
        approval_claim_id: str,
    ) -> Mapping[str, object]: ...


class SourceIndexLifecycleService:
    def __init__(
        self,
        *,
        repository: SourceIndexLifecycleRepositoryPort,
        audit: SourceIndexLifecycleAuditPort,
        approvals: SourceIndexPurgeApprovalPort | None = None,
        artifacts: SourceIndexArtifactDeletionPort | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._approvals = approvals
        self._artifacts = artifacts

    def history(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        connection_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> SourceIndexHistoryPage:
        _validate_scope(scope)
        _validate_id("connection_id", connection_id)
        if limit < 1 or limit > 200:
            raise SourceIndexLifecycleError("history_limit_invalid")
        if cursor is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", cursor):
            raise SourceIndexLifecycleError("history_cursor_invalid")
        page = self._repository.list_history(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=connection_id,
            cursor=cursor,
            limit=limit,
        )
        for item in page.items:
            if item.connection_id != connection_id:
                raise SourceIndexLifecycleError("history_scope_mismatch")
        return page

    def compare(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        left_index_id: str,
        right_index_id: str,
    ) -> dict[str, object]:
        left = self._required_index(scope, left_index_id)
        right = self._required_index(scope, right_index_id)
        if left.connection_id != right.connection_id:
            raise SourceIndexLifecycleError("index_connection_mismatch")
        return {
            "schema": "ananta.source-control.index-comparison.v1",
            "connection_id": left.connection_id,
            "left": _comparison_projection(left),
            "right": _comparison_projection(right),
            "changes": {
                "revision_changed": left.revision_digest != right.revision_digest,
                "policy_changed": left.policy_digest != right.policy_digest,
                "manifest_changed": left.manifest_digest != right.manifest_digest,
                "coverage": _coverage_delta(left.coverage, right.coverage),
                "status_changed": left.status != right.status,
            },
        }

    def activate(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        knowledge_index_id: str,
        expected_generation: int,
    ) -> ActiveIndexPointer:
        _require_mutator(scope)
        index = self._required_index(scope, knowledge_index_id)
        _require_activatable(index)
        pointer = self._repository.compare_and_activate(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            source_revision_id=index.source_revision_id,
            expected_generation=expected_generation,
            actor_id=scope.actor_id,
            reason_code="activate",
        )
        self._audit.record(
            operation="activate",
            actor_id=scope.actor_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            reason_code="index_activated",
        )
        return pointer

    def rollback(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        target_index_id: str,
        expected_generation: int,
    ) -> ActiveIndexPointer:
        _require_mutator(scope)
        target = self._required_index(scope, target_index_id)
        _require_activatable(target)
        current = self._repository.get_active(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=target.connection_id,
        )
        if current is None or current.knowledge_index_id == target.knowledge_index_id:
            raise SourceIndexLifecycleError("rollback_target_not_historical")
        if current.generation != expected_generation:
            raise SourceIndexLifecycleError("active_index_version_conflict")
        pointer = self._repository.compare_and_activate(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=target.connection_id,
            knowledge_index_id=target.knowledge_index_id,
            source_revision_id=target.source_revision_id,
            expected_generation=expected_generation,
            actor_id=scope.actor_id,
            reason_code="rollback",
        )
        self._audit.record(
            operation="rollback",
            actor_id=scope.actor_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=target.connection_id,
            knowledge_index_id=target.knowledge_index_id,
            reason_code="index_rolled_back",
        )
        return pointer

    def disable(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        connection_id: str,
        expected_version: int,
    ) -> int:
        _require_mutator(scope)
        _validate_id("connection_id", connection_id)
        if expected_version < 1:
            raise SourceIndexLifecycleError("connection_version_invalid")
        version = self._repository.disable_connection(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=connection_id,
            expected_version=expected_version,
            actor_id=scope.actor_id,
        )
        self._audit.record(
            operation="disable",
            actor_id=scope.actor_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=connection_id,
            knowledge_index_id=None,
            reason_code="connection_disabled",
        )
        return version

    def tombstone(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        knowledge_index_id: str,
        expected_version: int,
    ) -> int:
        _require_mutator(scope)
        if expected_version < 1:
            raise SourceIndexLifecycleError("index_version_invalid")
        index = self._required_index(scope, knowledge_index_id)
        active = self._repository.get_active(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
        )
        if active and active.knowledge_index_id == index.knowledge_index_id:
            raise SourceIndexLifecycleError("active_index_cannot_be_tombstoned")
        version = self._repository.tombstone_index(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            knowledge_index_id=knowledge_index_id,
            actor_id=scope.actor_id,
            expected_version=expected_version,
        )
        self._audit.record(
            operation="tombstone",
            actor_id=scope.actor_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            reason_code="index_tombstoned",
        )
        return version

    def purge(
        self,
        *,
        scope: SourceIndexLifecycleScope,
        knowledge_index_id: str,
        expected_version: int,
        approval_id: str | None = None,
        approval_claim_id: str | None = None,
    ) -> None:
        if "admin" not in scope.roles:
            raise SourceIndexLifecycleError("purge_admin_required")
        if expected_version < 1:
            raise SourceIndexLifecycleError("index_version_invalid")
        index = self._required_index(scope, knowledge_index_id)
        if not index.tombstoned:
            raise SourceIndexLifecycleError("purge_requires_tombstone")
        active = self._repository.get_active(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
        )
        if active and active.knowledge_index_id == index.knowledge_index_id:
            raise SourceIndexLifecycleError("active_index_cannot_be_purged")
        blockers = tuple(
            self._repository.purge_blockers(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                knowledge_index_id=knowledge_index_id,
            )
        )
        if blockers:
            blocker_kinds = ",".join(
                sorted({blocker.blocker_kind for blocker in blockers})
            )
            raise SourceIndexLifecycleError(f"purge_blocked:{blocker_kinds}")
        if not _OPAQUE_ID.fullmatch(str(approval_id or "")):
            raise SourceIndexLifecycleError("purge_approval_required")
        if not _OPAQUE_ID.fullmatch(str(approval_claim_id or "")):
            raise SourceIndexLifecycleError("purge_approval_claim_required")
        if self._approvals is None:
            raise SourceIndexLifecycleError(
                "purge_approval_store_unavailable"
            )
        approval_request_digest = derive_source_index_purge_digest(
            scope=scope,
            index=index,
            expected_version=expected_version,
        )
        self._approvals.claim(
            approval_id=str(approval_id),
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            action="purge",
            object_type="knowledge_index",
            object_id=index.knowledge_index_id,
            request_digest=approval_request_digest,
            claim_id=str(approval_claim_id),
        )
        if self._artifacts is not None:
            self._artifacts.delete_approved(
                scope=scope,
                index=index,
                expected_version=expected_version,
                approval_id=str(approval_id),
                approval_request_digest=approval_request_digest,
                approval_claim_id=str(approval_claim_id),
            )
        self._approvals.consume(
            approval_id=str(approval_id),
            request_digest=approval_request_digest,
            claim_id=str(approval_claim_id),
        )
        self._repository.purge_index(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            knowledge_index_id=knowledge_index_id,
            actor_id=scope.actor_id,
            expected_version=expected_version,
            approval_id=approval_id,
        )
        self._audit.record(
            operation="purge",
            actor_id=scope.actor_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            connection_id=index.connection_id,
            knowledge_index_id=index.knowledge_index_id,
            reason_code="index_purged",
        )

    def _required_index(
        self,
        scope: SourceIndexLifecycleScope,
        knowledge_index_id: str,
    ) -> SourceIndexRecord:
        _validate_scope(scope)
        _validate_id("knowledge_index_id", knowledge_index_id)
        index = self._repository.get_index(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            knowledge_index_id=knowledge_index_id,
        )
        if index is None:
            raise SourceIndexLifecycleError("knowledge_index_not_found")
        return index


def _require_activatable(index: SourceIndexRecord) -> None:
    if index.status != "completed":
        raise SourceIndexLifecycleError("index_not_completed")
    if not index.artifact_verified:
        raise SourceIndexLifecycleError("index_artifact_unverified")
    if index.tombstoned:
        raise SourceIndexLifecycleError("index_tombstoned")
    for name, value in (
        ("revision_digest", index.revision_digest),
        ("policy_digest", index.policy_digest),
        ("manifest_digest", index.manifest_digest),
    ):
        if not _SHA256.fullmatch(str(value or "")):
            raise SourceIndexLifecycleError(f"{name}_invalid")


def _require_mutator(scope: SourceIndexLifecycleScope) -> None:
    _validate_scope(scope)
    if not {"admin", "project_owner"} & scope.roles:
        raise SourceIndexLifecycleError("index_mutation_forbidden")


def _validate_scope(scope: SourceIndexLifecycleScope) -> None:
    for name in ("tenant_id", "project_id", "actor_id"):
        _validate_id(name, getattr(scope, name))


def _validate_id(name: str, value: str) -> None:
    if not _OPAQUE_ID.fullmatch(str(value or "")):
        raise SourceIndexLifecycleError(f"{name}_invalid")


def _comparison_projection(index: SourceIndexRecord) -> dict[str, object]:
    return {
        "knowledge_index_id": index.knowledge_index_id,
        "run_id": index.run_id,
        "source_revision_id": index.source_revision_id,
        "revision_digest": index.revision_digest,
        "policy_digest": index.policy_digest,
        "manifest_digest": index.manifest_digest,
        "status": index.status,
        "coverage": dict(index.coverage),
        "completed_at": index.completed_at,
    }


def _coverage_delta(
    left: Mapping[str, int | float],
    right: Mapping[str, int | float],
) -> dict[str, float]:
    keys = sorted(set(left) | set(right))
    if len(keys) > 32:
        raise SourceIndexLifecycleError("coverage_dimension_limit_exceeded")
    result: dict[str, float] = {}
    for key in keys:
        left_value = left.get(key, 0)
        right_value = right.get(key, 0)
        if (
            isinstance(left_value, bool)
            or isinstance(right_value, bool)
            or not isinstance(left_value, (int, float))
            or not isinstance(right_value, (int, float))
        ):
            raise SourceIndexLifecycleError("coverage_value_invalid")
        result[key] = float(right_value) - float(left_value)
    return result


def derive_source_index_purge_digest(
    *,
    scope: SourceIndexLifecycleScope,
    index: SourceIndexRecord,
    expected_version: int,
) -> str:
    """Bind an approval to exact immutable purge coordinates."""

    payload = {
        "action": "purge",
        "expected_version": expected_version,
        "knowledge_index_id": index.knowledge_index_id,
        "manifest_digest": index.manifest_digest,
        "object_type": "knowledge_index",
        "policy_digest": index.policy_digest,
        "project_id": scope.project_id,
        "revision_digest": index.revision_digest,
        "source_revision_id": index.source_revision_id,
        "tenant_id": scope.tenant_id,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
