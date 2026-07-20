from __future__ import annotations

import time

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechLineageEdgeDB,
    SpeechLineageNodeDB,
    SpeechLineageOutboxDB,
)
from agent.repositories.speech_evidence_lineage import (
    LINEAGE_KINDS,
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
    SpeechLineageRepositoryError,
)
from agent.services.ml_intern_speech_lineage_service import MlInternSpeechLineageService
from agent.services.voice_governance_domain import VoicePrincipal
from tests.speech_evidence_support import digest, principal


def test_forward_backward_transitive_lineage_is_persistent_and_tenant_scoped() -> None:
    prefix = "lineage-chain"
    repo = SpeechEvidenceLineageRepository()
    kinds = [
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
    ]
    nodes = tuple(SpeechLineageNode(kind, digest(f"{prefix}-{kind}")) for kind in kinds)
    edges = tuple(
        SpeechLineageEdge(kinds[index], nodes[index].digest, kinds[index + 1], nodes[index + 1].digest, "derived_from")
        for index in range(len(nodes) - 1)
    )
    repo.publish(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        nodes=nodes,
        edges=edges,
    )
    forward = repo.traverse(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        root_kind="evidence",
        root_digest=nodes[0].digest,
        direction="forward",
    )
    backward = repo.traverse(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        root_kind="receipt",
        root_digest=nodes[-1].digest,
        direction="backward",
    )
    assert {node["kind"] for node in forward.nodes} == set(kinds)
    assert {node["kind"] for node in backward.nodes} == set(kinds)
    assert set(kinds) == set(LINEAGE_KINDS)
    assert "transcript" not in str(forward.nodes).lower()
    with pytest.raises(SpeechLineageRepositoryError, match="speech_lineage_root_not_found"):
        repo.traverse(
            tenant_id="other-tenant",
            owner_subject=principal(prefix).subject,
            root_kind="evidence",
            root_digest=nodes[0].digest,
            direction="forward",
        )


def test_identical_digest_is_isolated_between_owners_in_one_tenant() -> None:
    repo = SpeechEvidenceLineageRepository()
    shared_digest = digest("same-opaque-digest")
    first = VoicePrincipal("tenant-lineage-owner-scope", "owner-a")
    second = VoicePrincipal("tenant-lineage-owner-scope", "owner-b")

    for scoped_principal in (first, second):
        repo.publish(
            tenant_id=scoped_principal.tenant_id,
            owner_subject=scoped_principal.subject,
            nodes=(SpeechLineageNode("evidence", shared_digest),),
            edges=(),
        )

    for scoped_principal in (first, second):
        page = repo.traverse(
            tenant_id=scoped_principal.tenant_id,
            owner_subject=scoped_principal.subject,
            root_kind="evidence",
            root_digest=shared_digest,
            direction="forward",
        )
        assert len(page.nodes) == 1


def test_impact_digest_is_stable_across_revocation_status_transition() -> None:
    prefix = "lineage-impact-status"
    scoped_principal = principal(prefix)
    repository = SpeechEvidenceLineageRepository()
    service = MlInternSpeechLineageService(repository)
    root = SpeechLineageNode("evidence", digest(f"{prefix}-root"))
    child = SpeechLineageNode("manifest", digest(f"{prefix}-child"))
    repository.publish(
        tenant_id=scoped_principal.tenant_id,
        owner_subject=scoped_principal.subject,
        nodes=(root, child),
        edges=(
            SpeechLineageEdge(
                "evidence", root.digest, "manifest", child.digest, "included_in"
            ),
        ),
    )

    before = service.impact(
        scoped_principal,
        root_kind="evidence",
        root_digest=root.digest,
        revocation_epoch=1,
    )
    repository.mark_status(
        tenant_id=scoped_principal.tenant_id,
        owner_subject=scoped_principal.subject,
        nodes=(("evidence", root.digest), ("manifest", child.digest)),
        status="revoked",
        revocation_epoch=1,
        now_ms=1_000_000,
    )
    after = service.impact(
        scoped_principal,
        root_kind="evidence",
        root_digest=root.digest,
        revocation_epoch=1,
    )

    assert before.impact_digest == after.impact_digest
    assert {node["status"] for node in after.nodes} == {"revoked"}


def test_domain_rollback_does_not_publish_lineage_outbox_or_edges() -> None:
    prefix = "lineage-domain-rollback"
    repository = SpeechEvidenceLineageRepository()
    with pytest.raises(RuntimeError, match="injected domain rollback"):
        with Session(engine) as session:
            repository.stage(
                session,
                tenant_id=principal(prefix).tenant_id,
                owner_subject=principal(prefix).subject,
                nodes=(SpeechLineageNode("evidence", digest(f"{prefix}-evidence")),),
                edges=(),
                now_ms=1_000_000,
            )
            raise RuntimeError("injected domain rollback")

    with Session(engine) as session:
        assert session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.tenant_id == principal(prefix).tenant_id
            )
        ).all() == []
        assert session.exec(
            select(SpeechLineageNodeDB).where(
                SpeechLineageNodeDB.tenant_id == principal(prefix).tenant_id
            )
        ).all() == []


class _CrashAfterGraphWrite(SpeechEvidenceLineageRepository):
    def _write_graph(self, *args, **kwargs) -> None:
        super()._write_graph(*args, **kwargs)
        raise RuntimeError("injected crash before outbox acknowledgement")


def test_outbox_crash_rolls_back_partial_graph_and_restart_recovers_exactly_once() -> None:
    prefix = "lineage-outbox-recovery"
    normal = SpeechEvidenceLineageRepository()
    root = SpeechLineageNode("evidence", digest(f"{prefix}-root"))
    target = SpeechLineageNode("manifest", digest(f"{prefix}-manifest"))
    with Session(engine) as session:
        event_digest = normal.stage(
            session,
            tenant_id=principal(prefix).tenant_id,
            owner_subject=principal(prefix).subject,
            nodes=(root, target),
            edges=(
                SpeechLineageEdge(
                    "evidence", root.digest, "manifest", target.digest, "included_in"
                ),
            ),
            now_ms=1_000_000,
        )
        session.commit()

    with pytest.raises(RuntimeError, match="injected crash"):
        _CrashAfterGraphWrite().process_outbox(
            event_digest=event_digest,
            tenant_id=principal(prefix).tenant_id,
            owner_subject=principal(prefix).subject,
        )
    with Session(engine) as session:
        event = session.exec(
            select(SpeechLineageOutboxDB).where(
                SpeechLineageOutboxDB.event_digest == event_digest
            )
        ).one()
        assert event.state == "pending" and event.attempt_count == 0
        assert session.exec(
            select(SpeechLineageNodeDB).where(
                SpeechLineageNodeDB.tenant_id == principal(prefix).tenant_id
            )
        ).all() == []
        assert session.exec(
            select(SpeechLineageEdgeDB).where(
                SpeechLineageEdgeDB.tenant_id == principal(prefix).tenant_id
            )
        ).all() == []

    restarted = SpeechEvidenceLineageRepository()
    assert restarted.recover_pending(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
    ) == 1
    assert restarted.recover_pending(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
    ) == 0
    page = restarted.traverse(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        root_kind="evidence",
        root_digest=root.digest,
        direction="forward",
    )
    assert [(node["kind"], node["digest"]) for node in page.nodes] == [
        ("evidence", root.digest),
        ("manifest", target.digest),
    ]


@pytest.mark.manual_full_scan
def test_100k_reference_impact_fixture_is_bounded_and_paginated() -> None:
    prefix = "lineage-100k"
    repo = SpeechEvidenceLineageRepository()
    root = SpeechLineageNode("evidence", digest(f"{prefix}-root"))
    descendants = tuple(SpeechLineageNode("receipt", digest(f"{prefix}-{index}")) for index in range(99_999))
    started = time.monotonic()
    repo.publish(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        nodes=(root, *descendants),
        edges=tuple(
            SpeechLineageEdge("evidence", root.digest, "receipt", node.digest, "produced") for node in descendants
        ),
    )
    page = repo.traverse(
        tenant_id=principal(prefix).tenant_id,
        owner_subject=principal(prefix).subject,
        root_kind="evidence",
        root_digest=root.digest,
        direction="forward",
        limit=1000,
    )
    assert len(page.nodes) == 1000 and page.next_offset == 1000
    assert time.monotonic() - started < 30
