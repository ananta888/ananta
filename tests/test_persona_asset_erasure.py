"""Scoped physical removal uses only temporary synthetic artifacts, never live data."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from agent.services.persona_asset_erasure import PersonaAssetErasureService
from agent.services.persona_image_erasure_store import PersonaImageErasureStore
from tests.test_persona_assets import application
from tests.test_persona_assets import setup as setup

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def retired(request):
    fixture = request.getfixturevalue("setup")
    service, principal, kwargs = application(fixture)
    asset = service.admit_image(principal, "project", **kwargs)
    service.revoke(principal, "project", asset.image.artifact_id, expected_revision=2)
    erasure = PersonaAssetErasureService(
        policy=service.policy, catalog=service.catalog, eraser=PersonaImageErasureStore(service.storage.store.base_dir)
    )
    return service, erasure, principal, asset


def paths(service, asset):
    return tuple(
        service.storage.store.base_dir / ref.artifact_id / "v0001__image.png" for ref in (asset.image, asset.preview)
    )


def test_purge_removes_only_the_retired_bundle_and_keeps_a_tombstone(retired):
    service, erasure, principal, asset = retired
    files = paths(service, asset)
    assert all(path.is_file() for path in files)
    assert erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=3) == 5
    assert all(not path.exists() for path in files)
    assert erasure.status(principal, "project", asset.image.artifact_id) == {"revision": 5, "state": "purged"}
    assert erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=5) == 5
    with pytest.raises(ValueError, match="not_active"):
        service.catalog.get_active("tenant", "project", asset.image.artifact_id)


def test_partial_erasure_resumes_without_reviving_or_requiring_a_person(retired):
    service, erasure, principal, asset = retired
    original = erasure.eraser
    count = 0

    def interrupted(reference, expected_size, *, checkpoint):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("synthetic disk interruption")
        original.erase(reference, expected_size, checkpoint=checkpoint)

    erasure.eraser = Mock(erase=interrupted)
    with pytest.raises(ValueError, match="interruption"):
        erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=3)
    first, second = paths(service, asset)
    assert not first.exists() and second.is_file()
    status = erasure.status(principal, "project", asset.image.artifact_id)
    assert status == {"revision": 4, "state": "purging"}
    erasure.eraser = original
    assert erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=status["revision"]) == 5
    assert not second.exists()


@pytest.mark.parametrize("change", ["bytes", "symlink", "hardlink", "directory_symlink"])
def test_changed_bytes_or_links_are_never_deleted(retired, tmp_path, change):
    service, erasure, principal, asset = retired
    first, _ = paths(service, asset)
    external = tmp_path / "unrelated.png"
    if change == "bytes":
        first.write_bytes(b"unrelated replacement")
    elif change == "symlink":
        first.replace(external)
        first.symlink_to(external)
    elif change == "hardlink":
        external.hardlink_to(first)
    else:
        external = tmp_path / "unrelated-directory"
        first.parent.replace(external)
        first.parent.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=3)
    assert first.exists()
    if change == "directory_symlink":
        assert (external / first.name).is_file()
    elif change != "bytes":
        assert external.is_file()
    assert erasure.status(principal, "project", asset.image.artifact_id)["state"] == "purging"


def test_permission_and_stale_revision_fail_before_any_file_operation(retired):
    service, erasure, principal, asset = retired
    erasure.eraser = Mock()
    with pytest.raises(ValueError, match="revision_conflict"):
        erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=2)
    service.policy.require_revoke.side_effect = PermissionError("revoked membership")
    with pytest.raises(PermissionError):
        erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=3)
    erasure.eraser.erase.assert_not_called()


def test_catalog_revocation_waits_for_the_existing_cross_process_storage_fence(request):
    fixture = request.getfixturevalue("setup")
    catalog, _, asset, _ = fixture
    catalog.reserve(asset, actor="actor")
    entered, attempt = Event(), Event()

    def revoke():
        attempt.set()
        catalog.transition("tenant", "project", "image", expected_revision=1, state="revoked", actor="actor")
        entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with catalog.storage_guard("tenant", "project", "image", expected_revision=1, state="pending"):
            result = executor.submit(revoke)
            assert attempt.wait(timeout=2)
            assert not entered.wait(timeout=0.05)
        result.result(timeout=3)
    assert entered.is_set()
    with pytest.raises(ValueError, match="guard_conflict"):
        with catalog.storage_guard("tenant", "project", "image", expected_revision=1, state="pending"):
            pytest.fail("a stale writer must not regain access")


def test_active_images_cannot_be_purged_without_a_prior_revocation(request):
    fixture = request.getfixturevalue("setup")
    service, principal, kwargs = application(fixture)
    asset = service.admit_image(principal, "project", **kwargs)
    eraser = Mock()
    erasure = PersonaAssetErasureService(policy=service.policy, catalog=service.catalog, eraser=eraser)
    with pytest.raises(ValueError, match="not_retired"):
        erasure.purge(principal, "project", asset.image.artifact_id, expected_revision=2)
    eraser.erase.assert_not_called()
    assert all(path.is_file() for path in paths(service, asset))
