"""Tests for the workspace-context writer with notation_hints (NOT-005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.services._worker_workspace_context_writer import (
    prepare_ananta_worker_context_files,
    prepare_opencode_context_files,
)
from agent.services.worker_workspace_service import WorkerWorkspaceContext


@pytest.fixture
def ws(tmp_path: Path) -> WorkerWorkspaceContext:
    (tmp_path / "rag").mkdir()
    (tmp_path / "artifacts").mkdir()
    return WorkerWorkspaceContext(
        workspace_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        rag_helper_dir=tmp_path / "rag",
        artifact_sync={},
    )


# ---------------------------------------------------------------------------
# opencode path
# ---------------------------------------------------------------------------


def test_opencode_writer_writes_notation_contract_when_hints_given(ws):
    task = {
        "id": "T1",
        "title": "Render UML",
        "description": "",
        "agent_template": "opencode",
    }
    manifest = prepare_opencode_context_files(
        task=task,
        workspace_context=ws,
        base_prompt="Render a UML class diagram.",
        system_prompt=None,
        context_text=None,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=None,
        include_response_contract=True,
        notation_hints={
            "allowed_notations": [
                "mermaid.class", "mermaid.sequence", "bpmn.process",
            ],
            "preferred_notations": ["mermaid.class"],
            "forbid_notations": ["bpmn.collaboration"],
            "default_notation": "mermaid.class",
            "task_kind": "diagram_mermaid",
        },
    )
    assert manifest["notation_selection_contract_path"] == (
        ".ananta/notation/notation-selection-contract.json"
    )
    assert manifest["notation_allowed_path"] == (
        ".ananta/notation/allowed-notations.md"
    )
    contract_file = ws.workspace_dir / manifest["notation_selection_contract_path"]
    assert contract_file.exists()
    contract = contract_file.read_text(encoding="utf-8")
    assert "mermaid.class" in contract
    assert "diagram_mermaid" in contract

    allowed_md = ws.workspace_dir / manifest["notation_allowed_path"]
    content = allowed_md.read_text(encoding="utf-8")
    assert "Allowed Diagram Notations" in content
    assert "bpmn.collaboration" in content
    assert "must NOT be used" in content
    assert "Default notation:" in content


def test_opencode_writer_context_index_lists_notation_files(ws):
    manifest = prepare_opencode_context_files(
        task={
            "id": "T1",
            "title": "T",
            "description": "",
            "agent_template": "opencode",
        },
        workspace_context=ws,
        base_prompt="Render.",
        system_prompt=None,
        context_text=None,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=None,
        include_response_contract=False,
        notation_hints={
            "allowed_notations": ["mermaid.class"],
            "preferred_notations": [],
            "forbid_notations": [],
            "default_notation": "mermaid.class",
            "task_kind": "diagram_mermaid",
        },
    )
    idx = (ws.workspace_dir / ".ananta" / "context-index.md").read_text()
    assert ".ananta/notation/notation-selection-contract.json" in idx
    assert ".ananta/notation/allowed-notations.md" in idx


def test_opencode_writer_skips_notation_when_hints_empty(ws):
    manifest = prepare_opencode_context_files(
        task={
            "id": "T1",
            "title": "T",
            "description": "",
            "agent_template": "opencode",
        },
        workspace_context=ws,
        base_prompt="Do something else.",
        system_prompt=None,
        context_text=None,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=None,
        include_response_contract=False,
        notation_hints={},  # empty -> no notation files
    )
    assert "notation_selection_contract_path" not in manifest
    assert not (ws.workspace_dir / ".ananta" / "notation").exists()


def test_opencode_writer_notation_and_pattern_hints_coexist(ws):
    """Both hint types must be honoured at the same time."""
    manifest = prepare_opencode_context_files(
        task={
            "id": "T1",
            "title": "T",
            "description": "",
            "agent_template": "opencode",
        },
        workspace_context=ws,
        base_prompt="Render a strategy as UML.",
        system_prompt=None,
        context_text=None,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=None,
        include_response_contract=False,
        pattern_hints={
            "allowed_patterns": ["python.strategy"],
            "preferred_patterns": [],
            "forbid_patterns": [],
            "language_targets": ["python"],
            "require_tests": True,
        },
        notation_hints={
            "allowed_notations": ["mermaid.class"],
            "preferred_notations": ["mermaid.class"],
            "forbid_notations": [],
            "default_notation": "mermaid.class",
            "task_kind": "diagram_mermaid",
        },
    )
    assert "pattern_selection_contract_path" in manifest
    assert "notation_selection_contract_path" in manifest


# ---------------------------------------------------------------------------
# ananta-worker path
# ---------------------------------------------------------------------------


def test_ananta_worker_writer_accepts_notation_hints(ws):
    manifest = prepare_ananta_worker_context_files(
        task={
            "id": "T1",
            "title": "T",
            "description": "",
            "agent_template": "ananta_worker",
        },
        workspace_context=ws,
        base_prompt="Read-only diagram reasoning.",
        system_prompt=None,
        context_text=None,
        research_context=None,
        mutation_mode="read_only",
        notation_hints={
            "allowed_notations": ["mermaid.class", "bpmn.process"],
            "preferred_notations": ["mermaid.class"],
            "forbid_notations": [],
            "default_notation": "mermaid.class",
            "task_kind": "diagram_mermaid",
        },
    )
    assert "notation_selection_contract_path" in manifest
    assert (ws.workspace_dir / ".ananta" / "notation"
            / "notation-selection-contract.json").exists()