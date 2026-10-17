"""Tests for HSM-003 decision rules."""

from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent


def test_comparison_criteria_document_exists():
    doc = WORKSPACE / "docs/hsm-003-comparison-criteria.md"
    assert doc.exists(), "Comparison criteria document must exist."
    content = doc.read_text(encoding="utf-8")
    for term in ("Correctness", "Scope Control", "Reproducibility", "Minimal Change", "Evidence-Based Completion"):
        assert term in content, f"Missing required criterion: {term}."


def test_decision_rules_test_file_exists():
    test_path = WORKSPACE / "tests/test_hsm_003_decision_rules.py"
    assert test_path.exists(), "Decision rules test file must exist."


def test_scope_control_rule():
    doc = (WORKSPACE / "docs/hsm-003-comparison-criteria.md").read_text(encoding="utf-8")
    assert "Only declared files may be modified." in doc
    assert "No commits, pushes, task creation, policy changes, Git metadata edits, or worker contact are allowed." in doc


def test_reproducibility_rule():
    doc = (WORKSPACE / "docs/hsm-003-comparison-criteria.md").read_text(encoding="utf-8")
    command = (
        "python -m pytest -q --confcutdir=tests/self_development"
        " -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development"
    )
    assert command in doc
    assert "exit code 0" in doc


def test_minimal_change_rule():
    doc = (WORKSPACE / "docs/hsm-003-comparison-criteria.md").read_text(encoding="utf-8")
    assert "Prefer the smallest diff that satisfies correctness and scope control." in doc
    assert "Avoid unrelated refactors, formatting changes, or metadata updates." in doc


def test_evidence_based_completion_rule():
    doc = (WORKSPACE / "docs/hsm-003-comparison-criteria.md").read_text(encoding="utf-8")
    assert "A decision is complete only when the Hub confirms the required test result with exit code 0." in doc
    assert "No completion claim may be made without confirmed evidence." in doc
