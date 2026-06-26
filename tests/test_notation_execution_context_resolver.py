"""Tests for the pattern-execution-context resolver with notation patterns (NOT-004).

Validates that the resolver:

* dispatches notation patterns to the NotationRenderer (and code patterns
  to PatternTemplateRenderer)
* rejects notation proposals that fail the policy gate (catalogue check
  and allow-list check)
* produces a stable context_hash for identical inputs
* renders on disk when target_root is set
* surfaces NotationRenderError as a blocked proposal with a clear reason
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.services.pattern_execution_context_resolver import (
    PatternExecutionContextResolver,
)


@pytest.fixture
def resolver() -> PatternExecutionContextResolver:
    return PatternExecutionContextResolver()


# ---------------------------------------------------------------------------
# Dry-run: accepted but no artifact
# ---------------------------------------------------------------------------


def test_resolver_accepts_mermaid_class_in_dry_run(resolver):
    ctx = resolver.resolve(raw_proposal={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "task_kind": "diagram_mermaid",
        "parameters_provided": {
            "direction": "LR",
            "classes": [
                {"name": "A"},
                {"name": "B"},
            ],
            "relationships": [
                {"type": "inheritance", "from": "B", "to": "A"},
            ],
        },
    })
    assert ctx.accepted
    assert ctx.notation_artifact is None
    assert ctx.render_manifest is None
    assert ctx.manifest_sha256 is None


# ---------------------------------------------------------------------------
# Dry-run: rejected
# ---------------------------------------------------------------------------


def test_resolver_rejects_notation_proposal_under_wrong_task_kind(resolver):
    ctx = resolver.resolve(raw_proposal={
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "task_kind": "coding",  # not in the diagram_mermaid allow-list
        "parameters_provided": {
            "classes": [{"name": "A"}],
        },
    })
    assert not ctx.accepted
    assert ctx.blocked_reason is not None
    assert "not in the default allow-list" in ctx.blocked_reason


def test_resolver_rejects_unknown_notation_id(resolver):
    ctx = resolver.resolve(raw_proposal={
        "pattern_id": "mermaid.totally_made_up",
        "language": "mermaid",
        "task_kind": "diagram_mermaid",
        "parameters_provided": {},
    })
    assert not ctx.accepted
    assert ctx.blocked_reason is not None


def test_resolver_no_pattern_proposal_is_accepted(resolver):
    """A worker may decline to propose a pattern — that must remain a no-op."""
    ctx = resolver.resolve(raw_proposal=None)
    assert ctx.accepted
    assert ctx.pattern_proposal.get("audit", {}).get("reason") == "no_pattern_proposed"


# ---------------------------------------------------------------------------
# On-disk: writes file
# ---------------------------------------------------------------------------


def test_resolver_renders_mermaid_class_to_disk(resolver, tmp_path: Path):
    ctx = resolver.resolve(
        raw_proposal={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "task_kind": "diagram_mermaid",
            "parameters_provided": {
                "classes": [
                    {"name": "A", "stereotype": "interface",
                     "methods": ["foo(): int"]},
                    {"name": "B", "methods": ["foo(): int"]},
                ],
                "relationships": [
                    {"type": "realization", "from": "B", "to": "A"},
                ],
            },
        },
        target_root=str(tmp_path),
    )
    assert ctx.accepted
    assert ctx.notation_artifact is not None
    output_file = tmp_path / ctx.notation_artifact["output_filename"]
    assert output_file.exists()
    content = output_file.read_text()
    assert content.startswith("classDiagram")
    assert "<<interface>>" in content


def test_resolver_renders_bpmn_process_to_disk(resolver, tmp_path: Path):
    ctx = resolver.resolve(
        raw_proposal={
            "pattern_id": "bpmn.process",
            "language": "bpmn",
            "task_kind": "diagram_bpmn",
            "parameters_provided": {
                "definitions_id": "Definitions_1",
                "process_id": "Process_Order",
                "elements": [
                    {"type": "startEvent", "id": "S"},
                    {"type": "endEvent", "id": "E"},
                ],
                "flows": [
                    {"id": "F1", "sourceRef": "S", "targetRef": "E"},
                ],
            },
        },
        target_root=str(tmp_path),
    )
    assert ctx.accepted
    artifact = ctx.notation_artifact
    assert artifact is not None
    assert artifact["language"] == "bpmn"
    assert artifact["output_filename"] == "process.bpmn"
    out = tmp_path / artifact["output_filename"]
    assert out.exists()
    assert out.read_text().startswith("<?xml")
    assert "bpmn:process" in out.read_text()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_resolver_context_hash_is_stable(resolver):
    proposal = {
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "task_kind": "diagram_mermaid",
        "parameters_provided": {
            "classes": [{"name": "A"}],
            "relationships": [],
        },
    }
    ctx1 = resolver.resolve(raw_proposal=proposal)
    ctx2 = resolver.resolve(raw_proposal=proposal)
    assert ctx1.context_hash == ctx2.context_hash


def test_resolver_on_disk_manifest_sha_is_stable(resolver, tmp_path: Path):
    proposal = {
        "pattern_id": "mermaid.class",
        "language": "mermaid",
        "task_kind": "diagram_mermaid",
        "parameters_provided": {
            "classes": [{"name": "A"}],
            "relationships": [],
        },
    }
    ctx1 = resolver.resolve(raw_proposal=proposal, target_root=str(tmp_path))
    ctx2 = resolver.resolve(raw_proposal=proposal, target_root=str(tmp_path))
    assert ctx1.manifest_sha256 == ctx2.manifest_sha256


# ---------------------------------------------------------------------------
# Render failure -> blocked proposal
# ---------------------------------------------------------------------------


def test_resolver_blocks_when_renderer_raises(resolver, tmp_path: Path):
    """Malformed notation input must surface as a blocked proposal with
    the renderer error in blocked_reason — not as a 500 to the caller."""
    ctx = resolver.resolve(
        raw_proposal={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "task_kind": "diagram_mermaid",
            "parameters_provided": {
                # duplicate class name -> renderer must reject
                "classes": [{"name": "A"}, {"name": "A"}],
                "relationships": [],
            },
        },
        target_root=str(tmp_path),
    )
    assert not ctx.accepted
    assert ctx.blocked_reason is not None
    assert "duplicate" in ctx.blocked_reason


# ---------------------------------------------------------------------------
# Code-pattern path is unchanged
# ---------------------------------------------------------------------------


def test_resolver_routes_code_pattern_through_template_renderer(
    resolver, tmp_path: Path
):
    """Sanity: a code-pattern proposal must NOT route to NotationRenderer
    even when notation patterns exist in the catalogue."""
    from agent.services.pattern_template_renderer import TemplateFile
    ctx = resolver.resolve(
        raw_proposal={
            "pattern_id": "python.strategy",
            "language": "python",
            "task_kind": "coding",
            "parameters_provided": {"context_class": "Order"},
        },
        templates=[
            TemplateFile(
                template_name="protocol",
                output_path="strategy_protocol.py",
                content="class OrderStrategy: pass\n",
            ),
        ],
        target_root=str(tmp_path),
    )
    assert ctx.accepted
    assert ctx.notation_artifact is None
    assert ctx.render_manifest is not None
    assert (tmp_path / "strategy_protocol.py").exists()


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


def test_context_to_dict_includes_notation_artifact(resolver, tmp_path: Path):
    ctx = resolver.resolve(
        raw_proposal={
            "pattern_id": "mermaid.class",
            "language": "mermaid",
            "task_kind": "diagram_mermaid",
            "parameters_provided": {"classes": [{"name": "A"}]},
        },
        target_root=str(tmp_path),
    )
    d = ctx.to_dict()
    assert "notation_artifact" in d
    assert d["notation_artifact"] is not None
    assert d["notation_artifact"]["language"] == "mermaid"
    assert "manifest_sha256" in d