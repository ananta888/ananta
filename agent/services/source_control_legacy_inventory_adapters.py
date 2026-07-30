"""Read-only adapters composing legacy source, knowledge and policy inventory."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models import ContextAccessPolicyDB
from agent.db_models.knowledge import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.services.source_control_legacy_migration import (
    LegacyCitation,
    LegacyContextPolicyVersion,
    LegacyKnowledgeIndex,
    LegacyKnowledgeIndexRun,
    LegacyMigrationInventory,
    LegacySourceSnapshot,
)
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore

_CONNECTOR_TYPES = frozenset(
    {
        "keycloak_docs",
        "wikimedia_dump",
        "web_doc",
        "local_dump",
        "open_notebook",
        "wiki",
    }
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def _epoch(value: object) -> float:
    text = str(value or "").strip()
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class LegacyCitationReaderPort(Protocol):
    def list_citations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> tuple[LegacyCitation, ...]: ...


class EmptyLegacyCitationReader:
    def list_citations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> tuple[LegacyCitation, ...]:
        del tenant_id, project_id, owner_id
        return ()


class SourceRegistrySnapshotReader:
    """Project valid immutable file snapshots without mutating legacy stores."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        snapshots: SourceSnapshotStore,
    ) -> None:
        self._registry = registry
        self._snapshots = snapshots

    def read(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> tuple[LegacySourceSnapshot, ...]:
        projected: list[LegacySourceSnapshot] = []
        for descriptor in self._registry.list_sources(
            include_disabled=True
        ):
            source_id = str(descriptor.get("source_id") or "").strip()
            connector_type = str(
                descriptor.get("source_type") or ""
            ).strip()
            if not source_id or connector_type not in _CONNECTOR_TYPES:
                continue
            extensions = dict(descriptor.get("extensions") or {})
            sensitivity = str(
                extensions.get("sensitivity") or "internal"
            ).strip()
            identity_digest = str(
                extensions.get("descriptor_hash") or ""
            ).strip()
            if not _is_sha256(identity_digest):
                identity_digest = _digest(descriptor)
            for snapshot in self._snapshots.list_snapshots(
                source_id=source_id
            ):
                content_digest = str(
                    snapshot.get("content_hash") or ""
                ).strip()
                metadata_digest = str(
                    snapshot.get("metadata_hash") or ""
                ).strip()
                descriptor_digest = str(
                    snapshot.get("descriptor_hash") or ""
                ).strip()
                if not all(
                    _is_sha256(value)
                    for value in (
                        content_digest,
                        metadata_digest,
                        descriptor_digest,
                    )
                ):
                    continue
                revision_digest = _digest(
                    {
                        "content_hash": content_digest,
                        "descriptor_hash": descriptor_digest,
                        "metadata_hash": metadata_digest,
                        "snapshot_id": snapshot.get("snapshot_id"),
                    }
                )
                manifest_digest = _digest(
                    {
                        "byte_size": snapshot.get("byte_size"),
                        "content_hash": content_digest,
                        "item_count": snapshot.get("item_count"),
                        "metadata_hash": metadata_digest,
                    }
                )
                status = str(snapshot.get("status") or "")
                admission_state = (
                    "admitted"
                    if status in {"indexed", "duplicate", "superseded"}
                    else "blocked"
                    if status == "failed"
                    else "pending"
                )
                try:
                    captured_at = _epoch(snapshot.get("created_at"))
                except (TypeError, ValueError):
                    continue
                projected.append(
                    LegacySourceSnapshot(
                        legacy_source_key=source_id,
                        legacy_snapshot_key=str(
                            snapshot.get("snapshot_id") or ""
                        ),
                        tenant_id=tenant_id,
                        project_id=project_id,
                        owner_id=owner_id,
                        connector_type=connector_type,
                        display_name=str(
                            descriptor.get("display_name") or source_id
                        ),
                        sensitivity=sensitivity,
                        enabled=bool(descriptor.get("enabled", True)),
                        connection_identity_digest=identity_digest,
                        revision_token=str(
                            snapshot.get("snapshot_id") or ""
                        ),
                        revision_digest=revision_digest,
                        content_manifest_id=f"manifest_{manifest_digest}",
                        content_manifest_digest=manifest_digest,
                        admission_state=admission_state,
                        captured_at_epoch=captured_at,
                    )
                )
        return tuple(
            sorted(
                projected,
                key=lambda item: (
                    item.legacy_source_key,
                    item.legacy_snapshot_key,
                ),
            )
        )


class SQLLegacyKnowledgePolicyReader:
    """Read existing SQLModel rows; missing bindings remain absent, not guessed."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> tuple[
        tuple[LegacyContextPolicyVersion, ...],
        tuple[LegacyKnowledgeIndex, ...],
        tuple[LegacyKnowledgeIndexRun, ...],
    ]:
        with Session(self._engine) as db:
            policy_rows = db.exec(
                select(ContextAccessPolicyDB).where(
                    ContextAccessPolicyDB.project_id == project_id
                )
            ).all()
            index_rows = db.exec(select(KnowledgeIndexDB)).all()
            run_rows = db.exec(select(KnowledgeIndexRunDB)).all()

        policies: list[LegacyContextPolicyVersion] = []
        for row in policy_rows:
            payload = dict(row.policy_json or {})
            policy_digest = _digest(
                {
                    "enabled": bool(row.enabled),
                    "lint_status": row.lint_status,
                    "policy": payload,
                    "policy_id": row.policy_id,
                    "version": row.version,
                }
            )
            policies.append(
                LegacyContextPolicyVersion(
                    legacy_policy_key=f"{row.policy_id}:{row.version}",
                    tenant_id=tenant_id,
                    project_id=project_id,
                    owner_id=owner_id,
                    policy_snapshot_id=(
                        f"policy-snapshot-{policy_digest[:32]}"
                    ),
                    policy_version=f"{row.policy_id}:v{row.version}",
                    policy_snapshot_digest=policy_digest,
                )
            )

        indexes: list[LegacyKnowledgeIndex] = []
        index_key_by_id: dict[str, str] = {}
        for row in index_rows:
            metadata = dict(row.index_metadata or {})
            snapshot_key = str(
                metadata.get("source_snapshot_id") or ""
            ).strip()
            policy_id = str(
                metadata.get("context_policy_id") or ""
            ).strip()
            policy_version = metadata.get("context_policy_version")
            if (
                not snapshot_key
                or not policy_id
                or isinstance(policy_version, bool)
                or not isinstance(policy_version, int)
                or policy_version < 1
            ):
                continue
            artifact_digest = metadata.get("artifact_manifest_digest")
            if artifact_digest is not None and not _is_sha256(
                artifact_digest
            ):
                artifact_digest = None
            key = f"knowledge-index:{row.id}"
            index_key_by_id[row.id] = key
            indexes.append(
                LegacyKnowledgeIndex(
                    legacy_index_key=key,
                    knowledge_index_id=row.id,
                    legacy_snapshot_key=snapshot_key,
                    legacy_policy_key=f"{policy_id}:{policy_version}",
                    status=str(row.status or "pending"),
                    index_contract_version=str(
                        metadata.get("index_contract_version")
                        or "legacy-knowledge-index-v1"
                    ),
                    artifact_manifest_digest=artifact_digest,
                    created_at_epoch=float(row.created_at),
                    updated_at_epoch=float(row.updated_at),
                )
            )

        runs: list[LegacyKnowledgeIndexRun] = []
        for row in run_rows:
            index_key = index_key_by_id.get(row.knowledge_index_id)
            if index_key is None:
                continue
            metadata = dict(row.run_metadata or {})
            artifact_digest = metadata.get("artifact_manifest_digest")
            if artifact_digest is not None and not _is_sha256(
                artifact_digest
            ):
                artifact_digest = None
            verified = bool(
                metadata.get("artifacts_verified", False)
                and artifact_digest
            )
            runs.append(
                LegacyKnowledgeIndexRun(
                    legacy_run_key=f"knowledge-attempt:{row.id}",
                    index_run_id=row.id,
                    legacy_index_key=index_key,
                    status=str(row.status or "pending"),
                    artifact_manifest_digest=artifact_digest,
                    artifacts_verified=verified,
                    created_at_epoch=float(row.created_at),
                    completed_at_epoch=(
                        float(row.finished_at)
                        if row.finished_at is not None
                        else None
                    ),
                )
            )
        return (
            tuple(
                sorted(
                    policies,
                    key=lambda item: item.legacy_policy_key,
                )
            ),
            tuple(
                sorted(
                    indexes,
                    key=lambda item: item.legacy_index_key,
                )
            ),
            tuple(
                sorted(runs, key=lambda item: item.legacy_run_key)
            ),
        )


class ComposedLegacySourceInventoryAdapter:
    """Composition-root adapter implementing LegacySourceInventoryPort."""

    def __init__(
        self,
        *,
        sources: SourceRegistrySnapshotReader,
        knowledge: SQLLegacyKnowledgePolicyReader,
        citations: LegacyCitationReaderPort | None = None,
    ) -> None:
        self._sources = sources
        self._knowledge = knowledge
        self._citations = citations or EmptyLegacyCitationReader()

    def load_inventory(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> LegacyMigrationInventory:
        policies, indexes, runs = self._knowledge.read(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
        )
        return LegacyMigrationInventory(
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            source_snapshots=self._sources.read(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            ),
            context_policies=policies,
            knowledge_indexes=indexes,
            index_runs=runs,
            citations=self._citations.list_citations(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            ),
        )
