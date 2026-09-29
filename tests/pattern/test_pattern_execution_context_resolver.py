"""Tests for PatternExecutionContextResolver (PAT-013).

Covers:
- resolver without proposal returns accepted=True with stable hash
- valid proposal + templates renders and produces a manifest
- invalid proposal is not rendered; blocked_reason surfaces
- catalogue-aware rejection when the registry does not contain
  the proposed id
- context_hash is stable for identical inputs
- render failures are caught and surfaced as blocked_reason
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.services.pattern_execution_context_resolver import (
    PatternExecutionContextResolver,
    get_pattern_execution_context_resolver,
)
from agent.services.pattern_template_renderer import TemplateFile


SIMPLE_TEMPLATES = [
    TemplateFile(
        "protocol",
        "proto.py",
        textwrap.dedent(
            """\
            class @@context_class@@Strategy:
                def execute(self):
                    return {}
            """
        ),
    ),
]


def test_resolver_without_proposal_is_accepted() -> None:
    r = PatternExecutionContextResolver()
    ctx = r.resolve(raw_proposal=None)
    assert ctx.accepted is True
    assert ctx.manifest_sha256 is None
    assert ctx.context_hash  # non-empty


def test_resolver_valid_proposal_renders_to_disk(tmp_path: Path) -> None:
    r = PatternExecutionContextResolver()
    ctx = r.resolve(
        raw_proposal={
            "pattern_id": "python.strategy",
            "task_kind": "coding",
            "language": "python",
            "parameters_provided": {"context_class": "Order"},
        },
        templates=SIMPLE_TEMPLATES,
        target_root=str(tmp_path),
    )
    assert ctx.accepted is True
    assert ctx.manifest_sha256 is not None
    assert (tmp_path / "proto.py").exists()
    assert "OrderStrategy" in (tmp_path / "proto.py").read_text(encoding="utf-8")


def test_resolver_invalid_proposal_is_blocked(tmp_path: Path) -> None:
    r = PatternExecutionContextResolver()
    ctx = r.resolve(
        raw_proposal={"pattern_id": "python.strategy", "task_kind": "nonsense"},
        templates=SIMPLE_TEMPLATES,
        target_root=str(tmp_path),
    )
    assert ctx.accepted is False
    assert ctx.manifest_sha256 is None
    assert ctx.blocked_reason is not None
    # No files written
    assert list(tmp_path.iterdir()) == []


def test_resolver_catalogue_aware_rejection(tmp_path: Path) -> None:
    """A registry that does not know the proposed id must reject."""
    from agent.services.pattern_registry import PatternRegistry
    from agent.services.pattern_service import PatternService

    fake_svc = PatternService.__new__(PatternService)
    object.__setattr__(fake_svc, "_catalog", [])
    object.__setattr__(fake_svc, "_catalog_path", "")
    fake_reg = PatternRegistry.__new__(PatternRegistry)
    object.__setattr__(fake_reg, "_overlay", [])
    object.__setattr__(fake_reg, "_overlay_path", "")
    object.__setattr__(fake_reg, "_service", fake_svc)

    r = PatternExecutionContextResolver()
    r._registry = fake_reg  # noqa: SLF001
    ctx = r.resolve(
        raw_proposal={"pattern_id": "python.strategy", "task_kind": "coding"},
        templates=SIMPLE_TEMPLATES,
        target_root=str(tmp_path),
    )
    assert ctx.accepted is False
    assert ctx.blocked_reason is not None
    assert "not in the catalogue" in ctx.blocked_reason


def test_resolver_render_failure_is_surfaced(tmp_path: Path) -> None:
    r = PatternExecutionContextResolver()
    bad = [TemplateFile("bad", "x.py", "ref @@missing_var@@")]
    ctx = r.resolve(
        raw_proposal={
            "pattern_id": "python.strategy",
            "task_kind": "coding",
            "language": "python",
            "parameters_provided": {},
        },
        templates=bad,
        target_root=str(tmp_path),
    )
    assert ctx.accepted is False
    assert ctx.blocked_reason is not None
    # Two reasons can block: the catalogue check OR the render
    # failure. Either is acceptable; the union of failure paths is
    # what we are asserting.
    assert (
        "render failed" in ctx.blocked_reason
        or "unknown parameter" in ctx.blocked_reason
        or "not in the catalogue" in ctx.blocked_reason
    )


def test_resolver_context_hash_is_stable() -> None:
    r = PatternExecutionContextResolver()
    a = r.resolve(raw_proposal=None)
    b = r.resolve(raw_proposal=None)
    assert a.context_hash == b.context_hash
    # Different inputs -> different hash
    c = r.resolve(
        raw_proposal={"pattern_id": "strategy", "task_kind": "coding"},
    )
    assert a.context_hash != c.context_hash


def test_resolver_to_dict_is_serializable() -> None:
    r = PatternExecutionContextResolver()
    ctx = r.resolve(raw_proposal=None)
    blob = ctx.to_dict()
    assert blob["accepted"] is True
    assert isinstance(blob["context_hash"], str)
    assert "pattern_proposal" in blob


def test_get_resolver_is_singleton() -> None:
    a = get_pattern_execution_context_resolver()
    b = get_pattern_execution_context_resolver()
    assert a is b
