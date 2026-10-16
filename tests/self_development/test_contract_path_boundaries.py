"""Boundary tests for self-development contract source path validation."""
import pytest

from agent.services.self_development.contracts import source_path


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../tests/self_development/test_example.py",
        "tests/./self_development/test_example.py",
        "tests\self_development\test_example.py",
        "tests/*_example.py",
        "tests/secrets/key.txt",
        "tests/fixtures/example.json",
        "tests",
    ],
)
def test_source_path_rejects_invalid_boundaries(bad_path):
    with pytest.raises(ValueError):
        source_path(bad_path)


@pytest.mark.parametrize(
    "good_path",
    [
        "tests/self_development/test_example.py",
        "scripts/run_checks.sh",
    ],
)
def test_source_path_accepts_regular_paths(good_path):
    assert source_path(good_path) == good_path
