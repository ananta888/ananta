"""Image readiness must distinguish installed dependencies from CUDA availability."""

from importlib.metadata import PackageNotFoundError

import pytest

from worker.decision.runtime_inventory import inventory

LOCK = b"# pinned fixture\ntorch==2.8.0+cu128 \\\n    --hash=sha256:synthetic\ncryptography==50.0.2\n"


def test_matching_dependencies_are_only_a_technical_inventory():
    versions = {"torch": "2.8.0+cu128", "cryptography": "50.0.2"}
    result = inventory(LOCK, lookup=versions.__getitem__, python_version=(3, 12))
    assert result["status"] == "compatible"
    assert result["production_release_evidence"] is False
    assert result["verification"] == "installed_versions_only_not_wheel_or_image_attestation"


def test_missing_signing_dependency_blocks_even_when_torch_matches():
    def version(name):
        if name == "cryptography":
            raise PackageNotFoundError(name)
        return "2.8.0+cu128"

    result = inventory(LOCK, lookup=version, python_version=(3, 12))
    assert result["status"] == "blocked"
    assert result["missing"] == ["cryptography"]


def test_wrong_python_and_dependency_versions_cannot_pass():
    result = inventory(LOCK, lookup=lambda _: "wrong", python_version=(3, 11))
    assert result["status"] == "blocked"
    assert result["mismatched"] == ["cryptography", "torch"]


@pytest.mark.parametrize("lock", [b"", b"torch>=2\n", b"torch==2\ntorch==3\n", b"x" * (1024 * 1024 + 1)])
def test_unpinned_duplicate_or_oversized_locks_are_rejected(lock):
    with pytest.raises(ValueError, match="jeff_dependency_lock_"):
        inventory(lock)
