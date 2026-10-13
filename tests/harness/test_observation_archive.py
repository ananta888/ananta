"""SEH-006: observation archive scope safety, exact recall and token savings."""

from __future__ import annotations

import pytest

from agent.services.observation_archive import ObservationArchive, ObservationScope
from ananta_contracts.harness_genome import content_digest

SCOPE = ObservationScope("tenant-a", "project-a", "task-1", "run-1")


def test_original_is_archived_unchanged_and_hash_referenced():
    archive = ObservationArchive()
    content = "line1\n" + "x" * 4000
    handle = archive.store_observation(SCOPE, content)
    assert handle.digest == content_digest(content)
    assert handle.byte_size == len(content.encode("utf-8"))
    assert archive.recall(SCOPE, handle.handle_id) == content
    assert len(handle.preview) < len(content)


def test_handles_are_not_transferable_across_scopes():
    archive = ObservationArchive()
    handle = archive.store_observation(SCOPE, "secret output")
    other = ObservationScope("tenant-a", "project-a", "task-1", "run-2")
    with pytest.raises(ValueError, match="observation_handle_scope_mismatch"):
        archive.recall(other, handle.handle_id)
    foreign_tenant = ObservationScope("tenant-b", "project-a", "task-1", "run-1")
    with pytest.raises(ValueError, match="observation_handle_scope_mismatch"):
        archive.recall(foreign_tenant, handle.handle_id)


def test_same_content_in_different_scope_yields_different_handle():
    archive = ObservationArchive()
    a = archive.store_observation(SCOPE, "same")
    b = archive.store_observation(ObservationScope("tenant-a", "project-a", "task-2", "run-1"), "same")
    assert a.handle_id != b.handle_id and a.digest == b.digest


def test_unknown_handle_is_rejected():
    with pytest.raises(ValueError, match="observation_handle_unknown"):
        ObservationArchive().recall(SCOPE, "0" * 64)


def test_selective_recall_returns_exact_source_range():
    archive = ObservationArchive()
    content = "prefix-" + "middle" + "-suffix"
    handle = archive.store_observation(SCOPE, content)
    assert archive.recall_range(SCOPE, handle.handle_id, 7, 13) == "middle"
    with pytest.raises(ValueError, match="observation_range_invalid"):
        archive.recall_range(SCOPE, handle.handle_id, 10, 5)
    with pytest.raises(ValueError, match="observation_range_invalid"):
        archive.recall_range(SCOPE, handle.handle_id, 0, len(content) + 1)


def test_token_savings_are_measured():
    archive = ObservationArchive()
    handle = archive.store_observation(SCOPE, "y" * 8000)
    assert archive.token_savings(handle) > 0
    small = archive.store_observation(SCOPE, "tiny")
    assert archive.token_savings(small) == 0


def test_invalid_scope_and_content_are_rejected():
    with pytest.raises(ValueError, match="observation_scope_tenant_id_invalid"):
        ObservationScope("", "p", "t", "r").assert_valid()
    with pytest.raises(ValueError, match="observation_content_invalid"):
        ObservationArchive().store_observation(SCOPE, b"bytes")  # type: ignore[arg-type]
