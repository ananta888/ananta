"""Exact temporary-file deletion only; synthetic policies and test-only identities."""

import errno
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.persona_asset_errors import PersonaStorageRetryableError
from agent.services.persona_file_erasure_store import PersonaFileErasureStore
from agent.services.persona_video_erasure import create_video_erasure_service
from tests.test_persona_video_catalog import video_catalog as video_catalog
from tests.test_persona_video_storage import video_storage as video_storage


@pytest.fixture
def retired_video(request):
    catalog, storage, asset, value = request.getfixturevalue("video_catalog")
    catalog.reserve(asset, actor="actor")
    with catalog.storage_guard("tenant", "project", "clip", expected_revision=1, state="pending"):
        paths = storage.write(asset, value, checkpoint=Mock())
    catalog.transition(
        "tenant", "project", "clip", expected_revision=1, state="active", actor="actor", stored_paths=paths
    )
    catalog.transition("tenant", "project", "clip", expected_revision=2, state="revoked", actor="actor")
    service = create_video_erasure_service(policy=Mock(), catalog=catalog, base_dir=storage.store.base_dir)
    return (
        service,
        SimpleNamespace(tenant_id="tenant", subject_id="actor"),
        {key: Path(path) for key, path in paths.items()},
    )


def test_only_bound_clip_and_preview_are_deleted_and_tombstone_remains(retired_video, tmp_path):
    service, principal, paths = retired_video
    unrelated = tmp_path / "keep.mp4"
    # Creating deterministic content inside this test's disposable directory.
    unrelated.touch()
    assert service.purge(principal, "project", "clip", expected_revision=3) == 5
    assert not any(path.exists() for path in paths.values()) and unrelated.is_file()
    assert service.status(principal, "project", "clip") == {"revision": 5, "state": "purged"}
    assert service.purge(principal, "project", "clip", expected_revision=5) == 5


def test_interrupted_second_member_resumes_exactly_without_reviving_asset(retired_video):
    service, principal, paths = retired_video
    original = service.eraser
    calls = 0

    def erase(reference, size, *, checkpoint):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PersonaStorageRetryableError("synthetic interruption")
        return original.erase(reference, size, checkpoint=checkpoint)

    service.eraser = Mock(erase=erase)
    with pytest.raises(PersonaStorageRetryableError):
        service.purge(principal, "project", "clip", expected_revision=3)
    assert not paths["clip"].exists() and paths["clip-preview"].is_file()
    assert service.status(principal, "project", "clip") == {"revision": 4, "state": "purging"}
    service.eraser = original
    assert service.purge(principal, "project", "clip", expected_revision=4) == 5


@pytest.mark.parametrize("change", ["bytes", "symlink", "hardlink", "parent_symlink"])
def test_changed_file_or_links_remain_untouched(retired_video, tmp_path, change):
    service, principal, paths = retired_video
    clip_path = paths["clip"]
    elsewhere = tmp_path / "unrelated"
    if change == "bytes":
        clip_path.write_bytes(b"changed test bytes")
    elif change == "symlink":
        clip_path.replace(elsewhere)
        clip_path.symlink_to(elsewhere)
    elif change == "hardlink":
        elsewhere.hardlink_to(clip_path)
    else:
        clip_path.parent.replace(elsewhere)
        clip_path.parent.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError):
        service.purge(principal, "project", "clip", expected_revision=3)
    assert clip_path.exists() and paths["clip-preview"].is_file()
    assert service.status(principal, "project", "clip")["state"] == "purging"


def test_stale_revision_or_revoked_membership_never_starts_erasure(retired_video):
    service, principal, paths = retired_video
    eraser = Mock()
    service.eraser = eraser
    with pytest.raises(ValueError, match="revision_conflict"):
        service.purge(principal, "project", "clip", expected_revision=2)
    service.policy.require_revoke.side_effect = PermissionError("membership revoked")
    with pytest.raises(PermissionError):
        service.purge(principal, "project", "clip", expected_revision=3)
    eraser.erase.assert_not_called()
    assert all(path.is_file() for path in paths.values())


def test_fsync_failure_after_unlink_is_retryable_and_rechecks_missing_member_directory(retired_video, monkeypatch):
    service, principal, paths = retired_video
    original = os.fsync
    count = 0

    def sync(descriptor):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError(errno.EIO, "synthetic fsync failure")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(PersonaStorageRetryableError):
        service.purge(principal, "project", "clip", expected_revision=3)
    assert not paths["clip"].exists() and paths["clip-preview"].exists()
    assert service.purge(principal, "project", "clip", expected_revision=4) == 5
    assert count == 3  # First failure, missing clip directory sync, preview sync.


@pytest.mark.parametrize("profile", ["../file", "arbitrary.mp4", [], None])
def test_erasure_accepts_only_fixed_operator_profiles(tmp_path, profile):
    with pytest.raises(ValueError, match="profile_invalid"):
        PersonaFileErasureStore(tmp_path, profile=profile)


def test_active_clip_requires_prior_revocation(request):
    catalog, storage, asset, value = request.getfixturevalue("video_catalog")
    catalog.reserve(asset, actor="actor")
    paths = storage.write(asset, value, checkpoint=Mock())
    catalog.transition(
        "tenant", "project", "clip", expected_revision=1, state="active", actor="actor", stored_paths=paths
    )
    service = create_video_erasure_service(policy=Mock(), catalog=catalog, base_dir=storage.store.base_dir)
    service.eraser = Mock()
    with pytest.raises(ValueError, match="not_retired"):
        service.purge(SimpleNamespace(tenant_id="tenant", subject_id="actor"), "project", "clip", expected_revision=2)
    service.eraser.erase.assert_not_called()
