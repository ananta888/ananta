"""Tests for TaskArtifactModel — COSMOS-004"""
from __future__ import annotations

import hashlib

import pytest

from agent.services.task_artifact_model import (
    ArtifactLifecycle,
    ArtifactPolicyClass,
    ArtifactType,
    TaskArtifact,
    TaskArtifactService,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _svc() -> TaskArtifactService:
    return TaskArtifactService()


def _artifact(service: TaskArtifactService, **kwargs) -> TaskArtifact:
    defaults = dict(
        run_id="run-123",
        artifact_type=ArtifactType.WORKER_OUTPUT,
        created_by="worker-1",
        content=b"hello world",
        policy_class=ArtifactPolicyClass.INTERNAL,
    )
    defaults.update(kwargs)
    return service.create_artifact(**defaults)


# ── Factory / creation ────────────────────────────────────────────────────────

def test_create_sets_content_hash():
    svc = _svc()
    content = b"hello world"
    artifact = _artifact(svc, content=content)
    expected_hash = hashlib.sha256(content).hexdigest()
    assert artifact.content_hash == expected_hash


def test_create_lifecycle_is_created():
    svc = _svc()
    artifact = _artifact(svc)
    assert artifact.lifecycle is ArtifactLifecycle.CREATED


def test_create_artifact_id_is_unique():
    svc = _svc()
    a1 = _artifact(svc)
    a2 = _artifact(svc)
    assert a1.artifact_id != a2.artifact_id


def test_create_string_content_hashed_as_utf8():
    svc = _svc()
    content = "hello utf-8"
    artifact = _artifact(svc, content=content)
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert artifact.content_hash == expected


# ── Access control ────────────────────────────────────────────────────────────

def test_can_be_read_by_granted_worker():
    svc = _svc()
    artifact = _artifact(svc, created_by="hub")
    # Explicitly grant worker-2
    svc.grant_access(artifact.artifact_id, "worker-2")
    # granted_artifact_ids is the list of artifact IDs the worker may access
    granted_artifact_ids = [artifact.artifact_id]
    assert artifact.can_be_read_by("worker-2", granted_artifact_ids=granted_artifact_ids) is True


def test_cannot_be_read_by_ungranted_worker():
    """Worker without explicit grant must not read — even for PUBLIC policy."""
    svc = _svc()
    artifact = _artifact(svc, policy_class=ArtifactPolicyClass.PUBLIC)
    # worker-99 has NOT been granted access — empty granted list for it
    assert artifact.can_be_read_by("worker-99", granted_artifact_ids=[]) is False


def test_creator_is_automatically_granted():
    """The creator is added to grants — get_for_worker returns the artifact."""
    svc = _svc()
    artifact = _artifact(svc, created_by="worker-1")
    # The service adds the creator; get_for_worker should return the artifact
    result = svc.get_for_worker(
        worker_id="worker-1",
        artifact_ids=[artifact.artifact_id],
    )
    assert any(a.artifact_id == artifact.artifact_id for a in result)


# ── SECRET_REF ────────────────────────────────────────────────────────────────

def test_secret_ref_has_no_content_hash():
    svc = _svc()
    artifact = _artifact(svc, policy_class=ArtifactPolicyClass.SECRET_REF, content=b"top secret")
    assert artifact.content_hash == ""


def test_secret_ref_has_no_storage_ref():
    svc = _svc()
    artifact = _artifact(svc, policy_class=ArtifactPolicyClass.SECRET_REF, content=b"top secret")
    assert artifact.storage_ref == ""


def test_is_secret_ref():
    svc = _svc()
    secret = _artifact(svc, policy_class=ArtifactPolicyClass.SECRET_REF)
    normal = _artifact(svc, policy_class=ArtifactPolicyClass.INTERNAL)
    assert svc.is_secret_ref(secret) is True
    assert svc.is_secret_ref(normal) is False


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def test_archive_transitions_lifecycle():
    svc = _svc()
    artifact = _artifact(svc)
    assert artifact.lifecycle is ArtifactLifecycle.CREATED
    archived = svc.archive(artifact.artifact_id)
    assert archived.lifecycle is ArtifactLifecycle.ARCHIVED


def test_archive_unknown_id_raises():
    svc = _svc()
    with pytest.raises(KeyError):
        svc.archive("nonexistent-id")


# ── get_for_worker ────────────────────────────────────────────────────────────

def test_get_for_worker_filters_correctly():
    svc = _svc()
    a1 = _artifact(svc, created_by="hub")
    a2 = _artifact(svc, created_by="hub")
    a3 = _artifact(svc, created_by="hub")

    svc.grant_access(a1.artifact_id, "worker-5")
    svc.grant_access(a3.artifact_id, "worker-5")
    # a2 is NOT granted to worker-5

    result = svc.get_for_worker(
        worker_id="worker-5",
        artifact_ids=[a1.artifact_id, a2.artifact_id, a3.artifact_id],
    )
    result_ids = {a.artifact_id for a in result}
    assert a1.artifact_id in result_ids
    assert a3.artifact_id in result_ids
    assert a2.artifact_id not in result_ids


def test_get_for_worker_unknown_artifact_id_skipped():
    svc = _svc()
    result = svc.get_for_worker(worker_id="worker-1", artifact_ids=["nonexistent-uuid"])
    assert result == []


# ── Summary ───────────────────────────────────────────────────────────────────

def test_summary_counts():
    svc = _svc()
    run_id = "run-summary-test"
    _artifact(svc, run_id=run_id, artifact_type=ArtifactType.WORKER_OUTPUT)
    _artifact(svc, run_id=run_id, artifact_type=ArtifactType.WORKER_OUTPUT)
    a3 = _artifact(svc, run_id=run_id, artifact_type=ArtifactType.TEST_REPORT)
    svc.archive(a3.artifact_id)

    # Artifact from a different run — must not appear in summary
    _artifact(svc, run_id="other-run", artifact_type=ArtifactType.FINAL_SUMMARY)

    s = svc.summary(run_id)
    assert s["run_id"] == run_id
    assert s["total"] == 3
    assert s["by_type"]["worker_output"] == 2
    assert s["by_type"]["test_report"] == 1
    assert s["by_lifecycle"]["created"] == 2
    assert s["by_lifecycle"]["archived"] == 1


def test_summary_empty_run():
    svc = _svc()
    s = svc.summary("nonexistent-run")
    assert s["total"] == 0
    assert s["by_type"] == {}
    assert s["by_lifecycle"] == {}


# ── as_dict ───────────────────────────────────────────────────────────────────

def test_as_dict_contains_expected_keys():
    svc = _svc()
    artifact = _artifact(svc)
    d = artifact.as_dict()
    for key in ("artifact_id", "run_id", "artifact_type", "version",
                "policy_class", "created_at", "created_by",
                "content_hash", "storage_ref", "lifecycle", "metadata"):
        assert key in d, f"Missing key: {key}"


def test_as_dict_values_are_strings_not_enums():
    svc = _svc()
    artifact = _artifact(svc)
    d = artifact.as_dict()
    assert isinstance(d["artifact_type"], str)
    assert isinstance(d["policy_class"], str)
    assert isinstance(d["lifecycle"], str)
