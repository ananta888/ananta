"""Reject stale private browser assets before any expensive test resources."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from tests.meet_companion_build import require_current_browser_build


@pytest.fixture
def build(tmp_path):
    repository = tmp_path / "meet"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=5)
    files = ["angular.json", "package.json", "frontend/src/main.ts", "src/shared.js"]
    for name in files:
        source = repository / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("synthetic")
        os.utime(source, (1000, 1000))
    # Include untracked browser changes as well as tracked inputs.
    subprocess.run(["git", "add", "angular.json", "package.json"], cwd=repository, check=True, timeout=5)
    directory = repository / "dist/browser"
    directory.mkdir(parents=True)
    index = directory / "index.html"
    index.write_text("synthetic")
    os.utime(index, (2000, 2000))
    return SimpleNamespace(**locals())


def test_current_build_is_explicitly_only_a_timestamp_observation(build):
    assert require_current_browser_build(build.repository) == {
        "freshness_check": "mtime_only",
        "source_files": 4,
        "production_release_evidence": False,
    }


@pytest.mark.parametrize("name", ["angular.json", "package.json", "frontend/src/main.ts", "src/shared.js"])
def test_newer_tracked_and_untracked_input_requires_rebuild(build, name):
    os.utime(build.repository / name, (3000, 3000))
    with pytest.raises(ValueError, match="build_stale_rebuild_in_private_directory"):
        require_current_browser_build(build.repository)


def test_explicit_private_build_does_not_replace_the_serving_directory(build, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "index.html").write_text("private")
    os.utime(build.index, (500, 500))
    require_current_browser_build(build.repository, str(private))
    assert build.index.read_text() == "synthetic" and build.index.stat().st_mtime == 500


def test_missing_build_or_incomplete_source_never_counts_as_current(build):
    with pytest.raises(ValueError, match="path_invalid"):
        require_current_browser_build(build.repository, "relative")
    with pytest.raises(ValueError, match="build_missing"):
        require_current_browser_build(build.repository, str(build.repository / "missing"))
    (build.repository / "frontend/src/main.ts").unlink()
    with pytest.raises(ValueError, match="inputs_missing"):
        require_current_browser_build(build.repository)
