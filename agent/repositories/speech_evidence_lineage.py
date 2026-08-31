"""Persistent content-free speech lineage graph with restart-stable outbox."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechLineageEdgeDB,
    SpeechLineageNodeDB,
    SpeechLineageOutboxDB,
)
from ananta_contracts.speech_evidence_governance import canonical_json

LINEAGE_KINDS = frozenset(
    {
        "evidence",
        "manifest",
        "split",
        "reconciliation",
        "job",
        "checkpoint",
        "evaluation",
        "model",
        "adapter",
        "export",
        "receipt",
    }
)
LINEAGE_STATUSES = frozenset({"active", "fenced", "revoked", "deleted", "unresolved"})


class SpeechLineageRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechLineageNode:
    kind: str
    digest: str
    status: str = "active"
    consent_id: str | None = None
    revocation_epoch: int = 0


@dataclass(frozen=True)
class SpeechLineageEdge:
    source_kind: str
    source_digest: str
    target_kind: str
    target_digest: str
    relation: str


@dataclass(frozen=True)
class SpeechLineagePage:
    nodes: tuple[dict[str, object], ...]
    total_discovered: int
    next_offset: int | None
    truncated: bool


class SpeechEvidenceLineageRepository:
    MAX_DEPTH = 32
    MAX_PAGE_SIZE = 1000
    MAX_TRAVERSAL_NODES = 100_000

    def publish(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        nodes: Sequence[SpeechLineageNode],
        edges: Sequence[SpeechLineageEdge],
        now_ms: int | None = None,
    ) -> str:
        timestamp = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        with Session(engine) as session:
            event_digest = self.stage(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                nodes=nodes,
                edges=edges,
                now_ms=timestamp,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
        self.process_outbox(
            event_digest=event_digest,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
        return event_digest

    def stage(
        self,
        session: Session,
        *,
        tenant_id: str,
        owner_subject: str,
        nodes: Sequence[SpeechLineageNode],
        edges: Sequence[SpeechLineageEdge],
        now_ms: int,
    ) -> str:
        """Stage lineage in the caller's domain transaction."""

        normalized_nodes, normalized_edges = _validate_graph(nodes, edges)
        payload = {
            "nodes": [asdict(node) for node in normalized_nodes],
            "edges": [asdict(edge) for edge in normalized_edges],
        }
        event_digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        existing = session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.tenant_id == tenant_id,
                SpeechLineageOutboxDB.owner_subject == owner_subject,
                SpeechLineageOutboxDB.event_digest == event_digest,
            )
        ).first()
        if existing is None:
            session.add(
                SpeechLineageOutboxDB(
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    event_digest=event_digest,
                    payload=payload,
                    created_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
            )
        return event_digest

    def process_outbox(self, *, event_digest: str, tenant_id: str, owner_subject: str) -> bool:
        with Session(engine) as session:
            event = session.exec(
                select(SpeechLineageOutboxDB)
                .where(
                    SpeechLineageOutboxDB.tenant_id == tenant_id,
                    SpeechLineageOutboxDB.owner_subject == owner_subject,
                    SpeechLineageOutboxDB.event_digest == event_digest,
                )
                .with_for_update()
            ).first()
            if event is None:
                raise SpeechLineageRepositoryError("speech_lineage_outbox_not_found")
            if event.state == "published":
                return False
            payload = dict(event.payload or {})
            nodes = tuple(SpeechLineageNode(**dict(raw)) for raw in payload.get("nodes", []))
            edges = tuple(SpeechLineageEdge(**dict(raw)) for raw in payload.get("edges", []))
            self._write_graph(
                session,
                tenant_id=tenant_id,
                owner_subject=event.owner_subject,
                nodes=nodes,
                edges=edges,
                now_ms=event.created_at_ms,
            )
            event.state = "published"
            event.attempt_count += 1
            event.updated_at_ms = time.time_ns() // 1_000_000
            session.add(event)
            session.commit()
            return True

    def recover_pending(
        self,
        *,
        limit: int = 100,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> int:
        if not 1 <= limit <= 1000:
            raise SpeechLineageRepositoryError("speech_lineage_limit_invalid")
        with Session(engine) as session:
            statement = select(SpeechLineageOutboxDB).where(SpeechLineageOutboxDB.state == "pending")
            if tenant_id is not None:
                statement = statement.where(SpeechLineageOutboxDB.tenant_id == tenant_id)
            if owner_subject is not None:
                statement = statement.where(SpeechLineageOutboxDB.owner_subject == owner_subject)
            rows = session.exec(statement.order_by(SpeechLineageOutboxDB.created_at_ms.asc()).limit(limit)).all()
            keys = tuple((row.tenant_id, row.owner_subject, row.event_digest) for row in rows)
        for pending_tenant, pending_owner, digest in keys:
            self.process_outbox(
                event_digest=digest,
                tenant_id=pending_tenant,
                owner_subject=pending_owner,
            )
        return len(keys)

    def traverse(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        root_kind: str,
        root_digest: str,
        direction: str,
        depth_limit: int = 16,
        offset: int = 0,
        limit: int = 200,
        _impact_internal: bool = False,
    ) -> SpeechLineagePage:
        if root_kind not in LINEAGE_KINDS or direction not in {"forward", "backward"}:
            raise SpeechLineageRepositoryError("speech_lineage_query_invalid")
        max_limit = self.MAX_TRAVERSAL_NODES if _impact_internal else self.MAX_PAGE_SIZE
        if not 0 <= offset <= self.MAX_TRAVERSAL_NODES or not 1 <= limit <= max_limit:
            raise SpeechLineageRepositoryError("speech_lineage_pagination_invalid")
        if not 0 <= depth_limit <= self.MAX_DEPTH:
            raise SpeechLineageRepositoryError("speech_lineage_depth_invalid")
        with Session(engine) as session:
            root = session.exec(
                select(SpeechLineageNodeDB).where(
                    SpeechLineageNodeDB.tenant_id == tenant_id,
                    SpeechLineageNodeDB.owner_subject == owner_subject,
                    SpeechLineageNodeDB.kind == root_kind,
                    SpeechLineageNodeDB.digest == root_digest,
                )
            ).first()
            if root is None:
                raise SpeechLineageRepositoryError("speech_lineage_root_not_found")
            discovered: dict[str, tuple[SpeechLineageNodeDB, int]] = {root.id: (root, 0)}
            frontier = {root.id}
            truncated = False
            for depth in range(1, depth_limit + 1):
                if not frontier:
                    break
                statement = (
                    select(SpeechLineageEdgeDB)
                    .where(SpeechLineageEdgeDB.tenant_id == tenant_id)
                    .where(
                        SpeechLineageEdgeDB.source_id.in_(frontier)
                        if direction == "forward"
                        else SpeechLineageEdgeDB.target_id.in_(frontier)
                    )
                )
                edge_rows = session.exec(statement).all()
                next_ids = {
                    edge.target_id if direction == "forward" else edge.source_id
                    for edge in edge_rows
                    if (edge.target_id if direction == "forward" else edge.source_id) not in discovered
                }
                if not next_ids:
                    frontier = set()
                    continue
                remaining = self.MAX_TRAVERSAL_NODES - len(discovered)
                if len(next_ids) > remaining:
                    next_ids = set(sorted(next_ids)[:remaining])
                    truncated = True
                rows: list[SpeechLineageNodeDB] = []
                for ids in _chunks(sorted(next_ids), 500):
                    rows.extend(
                        session.exec(
                            select(SpeechLineageNodeDB).where(
                                SpeechLineageNodeDB.tenant_id == tenant_id,
                                SpeechLineageNodeDB.owner_subject == owner_subject,
                                SpeechLineageNodeDB.id.in_(ids),
                            )
                        ).all()
                    )
                for row in rows:
                    discovered[row.id] = (row, depth)
                frontier = {row.id for row in rows}
                if truncated:
                    break
        ordered = sorted(discovered.values(), key=lambda item: (item[1], item[0].kind, item[0].digest))
        selected = ordered[offset : offset + limit]
        next_offset = offset + limit if offset + limit < len(ordered) else None
        return SpeechLineagePage(
            nodes=tuple(
                {
                    "kind": row.kind,
                    "digest": row.digest,
                    "status": row.status,
                    "consent_id": row.consent_id,
                    "revocation_epoch": int(row.revocation_epoch),
                    "depth": depth,
                }
                for row, depth in selected
            ),
            total_discovered=len(ordered),
            next_offset=next_offset,
            truncated=truncated,
        )

    def traverse_impact(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        root_kind: str,
        root_digest: str,
    ) -> SpeechLineagePage:
        return self.traverse(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            root_kind=root_kind,
            root_digest=root_digest,
            direction="forward",
            depth_limit=self.MAX_DEPTH,
            offset=0,
            limit=self.MAX_TRAVERSAL_NODES,
            _impact_internal=True,
        )

    def mark_status(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        nodes: Iterable[tuple[str, str]],
        status: str,
        revocation_epoch: int,
        now_ms: int,
    ) -> int:
        if status not in LINEAGE_STATUSES or revocation_epoch < 0:
            raise SpeechLineageRepositoryError("speech_lineage_status_invalid")
        changed = 0
        with Session(engine) as session:
            for kind, digest in nodes:
                result = session.exec(
                    update(SpeechLineageNodeDB)
                    .where(
                        SpeechLineageNodeDB.tenant_id == tenant_id,
                        SpeechLineageNodeDB.owner_subject == owner_subject,
                        SpeechLineageNodeDB.kind == kind,
                        SpeechLineageNodeDB.digest == digest,
                        SpeechLineageNodeDB.revocation_epoch <= revocation_epoch,
                    )
                    .values(status=status, revocation_epoch=revocation_epoch, updated_at_ms=now_ms)
                )
                changed += int(result.rowcount)
            session.commit()
        return changed

    def _write_graph(
        self,
        session: Session,
        *,
        tenant_id: str,
        owner_subject: str,
        nodes: Sequence[SpeechLineageNode],
        edges: Sequence[SpeechLineageEdge],
        now_ms: int,
    ) -> None:
        # Keep the high-volume path on SQLAlchemy Core executemany.  Creating
        # 100k ORM instances causes one INSERT/identity-map entry per node and
        # edge and makes the mandatory impact fixture both slow and memory
        # hungry.  Deterministic opaque IDs preserve idempotence without
        # exposing any content.
        by_key: dict[tuple[str, str], tuple[str, str, str | None]] = {}
        by_kind: dict[str, list[SpeechLineageNode]] = {}
        for node in nodes:
            by_kind.setdefault(node.kind, []).append(node)
        for kind, kind_nodes in by_kind.items():
            for batch in _chunks(kind_nodes, 500):
                digests = [node.digest for node in batch]
                rows = session.exec(
                    select(SpeechLineageNodeDB).where(
                        SpeechLineageNodeDB.tenant_id == tenant_id,
                        SpeechLineageNodeDB.owner_subject == owner_subject,
                        SpeechLineageNodeDB.kind == kind,
                        SpeechLineageNodeDB.digest.in_(digests),
                    )
                ).all()
                by_key.update({(row.kind, row.digest): (row.id, row.owner_subject, row.consent_id) for row in rows})
        pending: list[dict[str, object]] = []
        for node in nodes:
            key = (node.kind, node.digest)
            binding = by_key.get(key)
            if binding is None:
                node_id = _opaque_id(
                    "speech-lineage-node",
                    tenant_id,
                    owner_subject,
                    node.kind,
                    node.digest,
                )
                pending.append(
                    {
                        "id": node_id,
                        "tenant_id": tenant_id,
                        "owner_subject": owner_subject,
                        "kind": node.kind,
                        "digest": node.digest,
                        "status": node.status,
                        "consent_id": node.consent_id,
                        "revocation_epoch": node.revocation_epoch,
                        "created_at_ms": now_ms,
                        "updated_at_ms": now_ms,
                    }
                )
                by_key[key] = (node_id, owner_subject, node.consent_id)
            elif binding[1] != owner_subject or (
                node.consent_id is not None and binding[2] not in {None, node.consent_id}
            ):
                raise SpeechLineageRepositoryError("speech_lineage_node_binding_conflict")
        _insert_nodes_idempotently(session, pending, by_key)
        source_ids = sorted({by_key[(edge.source_kind, edge.source_digest)][0] for edge in edges})
        existing_edges: set[tuple[str, str, str]] = set()
        for batch in _chunks(source_ids, 500):
            rows = session.exec(
                select(SpeechLineageEdgeDB).where(
                    SpeechLineageEdgeDB.tenant_id == tenant_id,
                    SpeechLineageEdgeDB.source_id.in_(batch),
                )
            ).all()
            existing_edges.update((row.source_id, row.target_id, row.relation) for row in rows)
        pending_edges: list[dict[str, object]] = []
        for edge in edges:
            source_id = by_key[(edge.source_kind, edge.source_digest)][0]
            target_id = by_key[(edge.target_kind, edge.target_digest)][0]
            key = (source_id, target_id, edge.relation)
            if key not in existing_edges:
                pending_edges.append(
                    {
                        "id": _opaque_id(
                            "speech-lineage-edge",
                            tenant_id,
                            source_id,
                            target_id,
                            edge.relation,
                        ),
                        "tenant_id": tenant_id,
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation": edge.relation,
                        "created_at_ms": now_ms,
                    }
                )
                existing_edges.add(key)
        _insert_edges_idempotently(session, tenant_id=tenant_id, rows=pending_edges)


def _insert_nodes_idempotently(
    session: Session,
    rows: Sequence[dict[str, object]],
    bindings: dict[tuple[str, str], tuple[str, str, str | None]],
) -> None:
    """Insert nodes while allowing another Hub process to win the same key.

    The pre-insert lookup in :meth:`_write_graph` is an optimization, not a
    lock across containers.  A savepoint keeps a duplicate-key race local to
    one batch, after which the winner's immutable node binding is adopted.
    """

    for batch in _chunks(rows, 5000):
        remaining = list(batch)
        while remaining:
            try:
                with session.begin_nested():
                    session.execute(insert(SpeechLineageNodeDB.__table__), remaining)
                break
            except IntegrityError:
                missing: list[dict[str, object]] = []
                for row in remaining:
                    existing = session.exec(
                        select(SpeechLineageNodeDB).where(
                            SpeechLineageNodeDB.tenant_id == row["tenant_id"],
                            SpeechLineageNodeDB.owner_subject == row["owner_subject"],
                            SpeechLineageNodeDB.kind == row["kind"],
                            SpeechLineageNodeDB.digest == row["digest"],
                        )
                    ).first()
                    if existing is None:
                        missing.append(row)
                        continue
                    consent_id = row["consent_id"]
                    if consent_id is not None and existing.consent_id not in {None, consent_id}:
                        raise SpeechLineageRepositoryError("speech_lineage_node_binding_conflict")
                    bindings[(str(row["kind"]), str(row["digest"]))] = (
                        existing.id,
                        existing.owner_subject,
                        existing.consent_id,
                    )
                if len(missing) == len(remaining):
                    raise
                remaining = missing


def _insert_edges_idempotently(
    session: Session, *, tenant_id: str, rows: Sequence[dict[str, object]]
) -> None:
    """Insert immutable edges while converging with a concurrent outbox worker."""

    for batch in _chunks(rows, 5000):
        remaining = list(batch)
        while remaining:
            try:
                with session.begin_nested():
                    session.execute(insert(SpeechLineageEdgeDB.__table__), remaining)
                break
            except IntegrityError:
                source_ids = {str(row["source_id"]) for row in remaining}
                existing = {
                    (row.source_id, row.target_id, row.relation)
                    for row in session.exec(
                        select(SpeechLineageEdgeDB).where(
                            SpeechLineageEdgeDB.tenant_id == tenant_id,
                            SpeechLineageEdgeDB.source_id.in_(source_ids),
                        )
                    ).all()
                }
                missing = [
                    row
                    for row in remaining
                    if (str(row["source_id"]), str(row["target_id"]), str(row["relation"])) not in existing
                ]
                if len(missing) == len(remaining):
                    raise
                remaining = missing


def _validate_graph(
    nodes: Sequence[SpeechLineageNode], edges: Sequence[SpeechLineageEdge]
) -> tuple[tuple[SpeechLineageNode, ...], tuple[SpeechLineageEdge, ...]]:
    if not nodes or len(nodes) > 100_001 or len(edges) > 200_000:
        raise SpeechLineageRepositoryError("speech_lineage_graph_size_invalid")
    keys: set[tuple[str, str]] = set()
    for node in nodes:
        if node.kind not in LINEAGE_KINDS or node.status not in LINEAGE_STATUSES or not _digest(node.digest):
            raise SpeechLineageRepositoryError("speech_lineage_node_invalid")
        if node.revocation_epoch < 0 or (node.kind, node.digest) in keys:
            raise SpeechLineageRepositoryError("speech_lineage_node_duplicate")
        keys.add((node.kind, node.digest))
    edge_keys: set[tuple[str, str, str, str, str]] = set()
    for edge in edges:
        source = (edge.source_kind, edge.source_digest)
        target = (edge.target_kind, edge.target_digest)
        key = (*source, *target, edge.relation)
        if source not in keys or target not in keys or source == target:
            raise SpeechLineageRepositoryError("speech_lineage_edge_invalid")
        if not edge.relation or len(edge.relation) > 64 or key in edge_keys:
            raise SpeechLineageRepositoryError("speech_lineage_edge_duplicate")
        edge_keys.add(key)
    return tuple(nodes), tuple(edges)


def _digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _opaque_id(prefix: str, *bindings: str) -> str:
    material = "\0".join(bindings).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()}"


def _chunks(values: Sequence, size: int):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


_repository = SpeechEvidenceLineageRepository()


def get_speech_evidence_lineage_repository() -> SpeechEvidenceLineageRepository:
    return _repository


__all__ = [
    "LINEAGE_KINDS",
    "SpeechEvidenceLineageRepository",
    "SpeechLineageEdge",
    "SpeechLineageNode",
    "SpeechLineagePage",
    "SpeechLineageRepositoryError",
    "get_speech_evidence_lineage_repository",
]
