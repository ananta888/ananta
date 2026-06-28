"""Contract tests for X86CC-001: core-extension contract document must exist and document scope, non-goals, schema, safety, feature-flags."""

from pathlib import Path

import pytest

DOC_PATH = Path("docs/codecompass-x86-assembly-core-extension.md")


def test_doc_exists():
    assert DOC_PATH.is_file(), (
        f"X86CC-001 requires contract doc at {DOC_PATH}; "
        f"the file is missing — see todos/todo.codecompass-x86-assembly-core-extension.json"
    )


def test_doc_is_nonempty_markdown():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert len(text) >= 800, f"doc suspiciously short ({len(text)} chars); expected a real contract document"


def test_doc_names_scope_and_non_goals():
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    for marker in ("scope", "non-goal"):
        assert marker in text, f"doc must mention '{marker}'"


def test_doc_disavows_binary_execution():
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "no" in text and "execution" in text, "doc must explicitly disavow binary execution"


def test_doc_names_feature_flag():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "ANANTA_CODECOMPASS_X86_ENABLED" in text, "doc must document the master feature-flag"


def test_doc_separates_core_from_malware():
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "malware" in text and "core" in text, "doc must separate core from malware analysis"


@pytest.mark.parametrize(
    "section",
    [
        "Architecture",
        "Non-Goals",
        "Node Schema",
        "Edge Schema",
        "LocationRef",
        "Safety Policy",
        "Feature Flags",
    ],
)
def test_doc_has_required_section(section):
    text = DOC_PATH.read_text(encoding="utf-8")
    assert section in text, f"doc must have a '{section}' section heading"