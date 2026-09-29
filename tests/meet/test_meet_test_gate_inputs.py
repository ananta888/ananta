"""Actual local Git/build inputs; unrelated work is preserved but never used as evidence."""

import subprocess
from pathlib import Path

import pytest

from scripts.meet_test_gate_inputs import frontend_digest, require_immutable_test_image, snapshot_repository


@pytest.mark.parametrize("image", [None, "latest", "sha256:" + "a" * 63, "sha256:" + "g" * 64, True])
def test_gpu_gate_never_inherits_the_serving_image_or_mutable_tag(image):
    with pytest.raises(ValueError, match="immutable_image_required"):
        require_immutable_test_image(image)


def test_exact_packaged_gpu_reference_is_accepted_without_pulling_or_deploying():
    assert require_immutable_test_image("sha256:" + "a" * 64) is None


def test_clean_tracked_snapshot_rejects_changed_or_new_selected_sources(tmp_path):
    def git(*args):
        return subprocess.run(("git", *args), cwd=tmp_path, check=True, capture_output=True, timeout=5)

    git("init", "-q")
    (tmp_path / "owned").mkdir()
    source = tmp_path / "owned/source.py"
    source.write_text("value = 1\n")
    git("add", "owned/source.py")
    git(
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "test(meet): create isolated input fixture",
    )
    before, files = snapshot_repository(tmp_path, ("owned",))
    assert files == (Path("owned/source.py"),)
    assert len(before["revision"]) == 40 and len(before["digest"]) == 64
    (tmp_path / "unrelated.txt").write_text("outside the selected execution inputs\n")
    assert snapshot_repository(tmp_path, ("owned",))[0] == before
    source.write_text("value = 2\n")
    with pytest.raises(subprocess.CalledProcessError):
        snapshot_repository(tmp_path, ("owned",))
    source.write_text("value = 1\n")
    (tmp_path / "owned/new.py").write_text("unexpected = True\n")
    with pytest.raises(ValueError, match="untracked_sources"):
        snapshot_repository(tmp_path, ("owned",))


def test_private_bundle_digest_changes_with_actual_bytes_and_rejects_links(tmp_path):
    (tmp_path / "index.html").write_text("<html>synthetic</html>")
    original = frontend_digest(tmp_path)
    (tmp_path / "main.js").write_text("console.log('synthetic');")
    assert frontend_digest(tmp_path) != original
    (tmp_path / "linked.js").symlink_to(tmp_path / "main.js")
    with pytest.raises(ValueError, match="frontend_linked"):
        frontend_digest(tmp_path)


def test_missing_browser_bundle_is_not_ready(tmp_path):
    with pytest.raises(ValueError, match="frontend_missing"):
        frontend_digest(tmp_path)


def test_actual_hub_registry_reserves_test_identity_with_explicit_meet_policy_before_completion(tmp_path):
    from scripts.hub_browser_test_evidence import HubBrowserTestRun

    for name in ("owned.py", "AGENTS.md"):
        (tmp_path / name).write_text("synthetic test input\n")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True, capture_output=True, timeout=5)
    subprocess.run(("git", "add", "owned.py", "AGENTS.md"), cwd=tmp_path, check=True, capture_output=True, timeout=5)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "test(meet): bind isolated registry source",
        ),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=5,
    )
    run = HubBrowserTestRun.reserve(
        root=tmp_path,
        registry_db=tmp_path / "registry.sqlite",
        task_id="synthetic-gate",
        source_paths=(Path("owned.py"),),
        policy_paths=(Path("AGENTS.md"),),
        execution_profile={"synthetic": True},
        environment={"fixture": True},
    )
    assert run.source_id.startswith("SRC_") and run.run_id.startswith("RUN_")
    result = run.complete({"passed": True, "synthetic": True}, succeeded=True)
    assert result["scope"] == "test" and result["synthetic"] is True
    assert result["production_release_eligible"] is False
