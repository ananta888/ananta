from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.scientific_skill_provenance import ScientificSkillProvenanceRepository
from agent.services.scientific_skill_provenance_service import (
    ScientificSkillProvenanceService,
    ScientificSkillToolCallEvidence,
)
from agent.services.scientific_skill_runtime_control_service import (
    JsonScientificSkillRuntimeControlRepository,
    ScientificSkillRuntimeControlService,
)


@pytest.fixture
def service():
    database = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(database)
    return ScientificSkillProvenanceService(ScientificSkillProvenanceRepository(database))


def _record(service, **changes):
    values = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "task_id": "task-1",
        "entry_id": "skillentry_" + "a" * 64,
        "upstream_pin": "0123456789abcdef",
        "skill_sha256": "b" * 64,
        "catalog_digest": "c" * 64,
        "catalog_entry_status": "approved",
        "policy_decision_digest": "d" * 64,
        "approval_request_id": "approval-1",
        "approval_digest": "e" * 64,
        "model_id": "local-model",
        "tool_calls": (ScientificSkillToolCallEvidence("citation_search", "f" * 64, "completed"),),
        "source_references": ("source-record-1",),
        "artifact_digests": ("1" * 64,),
        "result_digest": "2" * 64,
        "created_at_epoch": 1000.0,
    }
    values.update(changes)
    return service.record(**values)


def test_receipt_binds_skill_catalog_policy_approval_model_tools_sources_and_artifacts(service):
    receipt = _record(service)
    assert receipt.verified is True
    assert receipt.payload["upstream_pin"] == "0123456789abcdef"
    assert receipt.payload["catalog_digest"] == "c" * 64
    assert receipt.payload["catalog_entry_status"] == "approved"
    assert receipt.payload["policy_decision_digest"] == "d" * 64
    assert receipt.payload["approval_digest"] == "e" * 64
    assert receipt.payload["tool_calls"][0]["target_digest"] == "f" * 64
    assert service.get_verified(
        tenant_id="tenant-1", project_id="project-1", receipt_digest=receipt.receipt_digest
    ) == receipt
    assert service.get_verified(
        tenant_id="tenant-2", project_id="project-1", receipt_digest=receipt.receipt_digest
    ) is None


def test_identical_receipt_is_idempotent_but_append_only_conflicts_are_rejected(service):
    first = _record(service)
    second = _record(service)
    assert second.receipt_digest == first.receipt_digest
    with pytest.raises(ValueError, match="immutable_conflict"):
        existing = service._repository.get(
            tenant_id="tenant-1", project_id="project-1", receipt_digest=first.receipt_digest
        )
        assert existing is not None
        service._repository.append(existing.model_copy(update={"tenant_id": "tenant-2"}))


def test_tampered_or_incomplete_receipts_are_never_verified(service):
    receipt = _record(service)
    tampered = dict(receipt.payload)
    tampered["result_digest"] = "3" * 64
    assert service.verify(tampered, receipt_digest=receipt.receipt_digest).verified is False
    incomplete = dict(receipt.payload)
    incomplete.pop("catalog_digest")
    assert service.verify(incomplete, receipt_digest=receipt.receipt_digest).verified is False


def test_raw_credentials_and_private_keys_are_rejected(service):
    with pytest.raises(ValueError, match="secret_material_denied"):
        _record(service, source_references=("Authorization: Bearer abcdef",))


def test_legacy_v1_receipt_remains_verifiable_after_v2_addition(service):
    receipt = _record(service)
    legacy = dict(receipt.payload)
    legacy["schema"] = "ananta.scientific-skill-provenance.v1"
    legacy.pop("catalog_entry_status")
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    assert service.verify(legacy, receipt_digest=digest).verified is True


def test_runtime_deactivation_preserves_historical_pin_and_status(service, tmp_path):
    receipt = _record(service)
    runtime = ScientificSkillRuntimeControlService(
        JsonScientificSkillRuntimeControlRepository(tmp_path / "runtime-control.json")
    )
    runtime.set_global(
        enabled=True,
        expected_revision=0,
        actor_id="operator-1",
        reason="pilot",
    )
    runtime.set_entry(
        entry_id="skillentry_" + "a" * 64,
        enabled=False,
        expected_revision=1,
        actor_id="operator-1",
        reason="emergency-disable",
    )

    historical = service.get_verified(
        tenant_id="tenant-1",
        project_id="project-1",
        receipt_digest=receipt.receipt_digest,
    )
    assert historical is not None
    assert historical.payload["upstream_pin"] == "0123456789abcdef"
    assert historical.payload["catalog_entry_status"] == "approved"
