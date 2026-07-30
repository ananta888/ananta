from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.source_index_lifecycle_service import (
    ActiveIndexPointer,
    PurgeBlocker,
    SourceIndexHistoryPage,
    SourceIndexLifecycleError,
    SourceIndexLifecycleScope,
    SourceIndexLifecycleService,
    SourceIndexRecord,
)


def _index(**overrides) -> SourceIndexRecord:
    values = {
        "knowledge_index_id": "index-example",
        "run_id": "run-example",
        "connection_id": "connection-example",
        "source_revision_id": "revision-example",
        "revision_digest": "a" * 64,
        "policy_digest": "b" * 64,
        "manifest_digest": "c" * 64,
        "status": "completed",
        "coverage": {"symbol_ratio": 0.8},
        "artifact_verified": True,
        "completed_at": "2026-01-01T00:00:00Z",
        "tombstoned": False,
        "sensitive": False,
    }
    values.update(overrides)
    return SourceIndexRecord(**values)


class _Repository:
    def __init__(self, indices=None) -> None:
        self.indices = {
            index.knowledge_index_id: index
            for index in (indices or [_index()])
        }
        self.active = ActiveIndexPointer(
            connection_id="connection-example",
            knowledge_index_id="current-index",
            source_revision_id="current-revision",
            generation=2,
        )
        self.blockers = ()
        self.purged = []
        self.versions = {
            index.knowledge_index_id: 1 for index in self.indices.values()
        }

    def list_history(self, **kwargs):
        return SourceIndexHistoryPage(
            items=tuple(self.indices.values()),
            active=self.active,
            next_cursor=None,
        )

    def get_index(self, **kwargs):
        return self.indices.get(kwargs["knowledge_index_id"])

    def get_active(self, **kwargs):
        return self.active

    def compare_and_activate(self, **kwargs):
        if kwargs["expected_generation"] != self.active.generation:
            raise SourceIndexLifecycleError("active_index_version_conflict")
        self.active = ActiveIndexPointer(
            connection_id=kwargs["connection_id"],
            knowledge_index_id=kwargs["knowledge_index_id"],
            source_revision_id=kwargs["source_revision_id"],
            generation=self.active.generation + 1,
        )
        return self.active

    def disable_connection(self, **kwargs):
        return kwargs["expected_version"] + 1

    def tombstone_index(self, **kwargs):
        if kwargs["expected_version"] != self.versions[
            kwargs["knowledge_index_id"]
        ]:
            raise SourceIndexLifecycleError("index_version_conflict")
        index = self.indices[kwargs["knowledge_index_id"]]
        self.indices[index.knowledge_index_id] = replace(index, tombstoned=True)
        self.versions[index.knowledge_index_id] += 1
        return self.versions[index.knowledge_index_id]

    def purge_blockers(self, **kwargs):
        return self.blockers

    def purge_index(self, **kwargs):
        self.purged.append(kwargs)


class _Audit:
    def __init__(self) -> None:
        self.events = []

    def record(self, **event):
        self.events.append(event)


class _Approvals:
    def __init__(self) -> None:
        self.claims = []
        self.consumptions = []

    def claim(self, **values):
        self.claims.append(values)

    def consume(self, **values):
        self.consumptions.append(values)


def _scope(*roles: str) -> SourceIndexLifecycleScope:
    return SourceIndexLifecycleScope(
        tenant_id="tenant-example",
        project_id="project-example",
        actor_id="actor-example",
        roles=frozenset(roles or ("project_owner",)),
    )


def test_activation_and_rollback_use_atomic_generation() -> None:
    repository = _Repository()
    audit = _Audit()
    service = SourceIndexLifecycleService(repository=repository, audit=audit)

    activated = service.activate(
        scope=_scope(),
        knowledge_index_id="index-example",
        expected_generation=2,
    )

    assert activated.generation == 3
    assert audit.events[-1]["operation"] == "activate"

    repository.indices["current-index"] = _index(
        knowledge_index_id="current-index",
        run_id="current-run",
        source_revision_id="current-revision",
    )
    rolled_back = service.rollback(
        scope=_scope(),
        target_index_id="current-index",
        expected_generation=3,
    )
    assert rolled_back.knowledge_index_id == "current-index"
    assert rolled_back.generation == 4


def test_compare_is_bounded_and_contains_no_artifact_paths() -> None:
    repository = _Repository(
        [
            _index(),
            _index(
                knowledge_index_id="index-new",
                run_id="run-new",
                revision_digest="d" * 64,
                coverage={"symbol_ratio": 1.0},
            ),
        ]
    )
    service = SourceIndexLifecycleService(repository=repository, audit=_Audit())

    result = service.compare(
        scope=_scope(),
        left_index_id="index-example",
        right_index_id="index-new",
    )

    assert result["changes"]["revision_changed"] is True
    assert result["changes"]["coverage"]["symbol_ratio"] == pytest.approx(0.2)
    assert "output_dir" not in str(result)
    assert "local_path" not in str(result)


def test_active_or_referenced_index_cannot_be_purged() -> None:
    index = _index(tombstoned=True)
    repository = _Repository([index])
    service = SourceIndexLifecycleService(repository=repository, audit=_Audit())
    repository.active = ActiveIndexPointer(
        connection_id=index.connection_id,
        knowledge_index_id=index.knowledge_index_id,
        source_revision_id=index.source_revision_id,
        generation=1,
    )
    with pytest.raises(SourceIndexLifecycleError, match="active_index"):
        service.purge(
            scope=_scope("admin"),
            knowledge_index_id=index.knowledge_index_id,
            expected_version=1,
        )

    repository.active = None
    repository.blockers = (
        PurgeBlocker("lease", "lease-example"),
        PurgeBlocker("citation", "citation-example"),
    )
    with pytest.raises(SourceIndexLifecycleError, match="purge_blocked"):
        service.purge(
            scope=_scope("admin"),
            knowledge_index_id=index.knowledge_index_id,
            expected_version=1,
        )


def test_sensitive_purge_requires_explicit_approval() -> None:
    index = _index(tombstoned=True, sensitive=True)
    repository = _Repository([index])
    repository.active = None
    approvals = _Approvals()
    service = SourceIndexLifecycleService(
        repository=repository,
        audit=_Audit(),
        approvals=approvals,
    )

    with pytest.raises(SourceIndexLifecycleError, match="approval_required"):
        service.purge(
            scope=_scope("admin"),
            knowledge_index_id=index.knowledge_index_id,
            expected_version=1,
        )
    service.purge(
        scope=_scope("admin"),
        knowledge_index_id=index.knowledge_index_id,
        expected_version=1,
        approval_id="approval-example",
        approval_claim_id="operation-example",
    )
    assert repository.purged[0]["approval_id"] == "approval-example"
    assert approvals.consumptions[0]["claim_id"] == "operation-example"


def test_disable_tombstone_and_purge_are_distinct() -> None:
    repository = _Repository()
    repository.active = None
    audit = _Audit()
    service = SourceIndexLifecycleService(repository=repository, audit=audit)

    assert service.disable(
        scope=_scope(),
        connection_id="connection-example",
        expected_version=1,
    ) == 2
    assert service.tombstone(
        scope=_scope(),
        knowledge_index_id="index-example",
        expected_version=1,
    ) == 2
    assert repository.indices["index-example"].tombstoned is True
    assert repository.purged == []


def test_tombstone_rejects_stale_index_version() -> None:
    service = SourceIndexLifecycleService(
        repository=_Repository(),
        audit=_Audit(),
    )

    with pytest.raises(
        SourceIndexLifecycleError,
        match="index_version_conflict",
    ):
        service.tombstone(
            scope=_scope(),
            knowledge_index_id="index-example",
            expected_version=2,
        )
