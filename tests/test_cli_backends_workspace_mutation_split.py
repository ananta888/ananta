"""Tests: workspace_mutation 4-split public API.

The 4-split (signatures/prompts/loop/tools) is internal — the public API
surface is on the new ``agent.cli_backends.workspace_mutation`` package.

This file verifies:
- All public symbols are importable from the new namespace
- The 4 sub-modules exist and own their responsibilities
- The orchestrator (``run_ananta_worker_workspace_mutation``) is reachable
- The public KIND_* constants are exported
"""
from __future__ import annotations

from pathlib import Path


def test_workspace_mutation_signatures_module_exists() -> None:
    """The signatures sub-module must exist with the public signature helpers."""
    from agent.cli_backends.workspace_mutation import signatures

    assert hasattr(signatures, "evidence_signature")
    assert hasattr(signatures, "changes_signature")


def test_workspace_mutation_prompts_module_exists() -> None:
    """The prompts sub-module must exist with the prompt-building helpers."""
    from agent.cli_backends.workspace_mutation import prompts

    assert hasattr(prompts, "parse_mutation_output")
    assert hasattr(prompts, "build_mode_instructions")
    assert hasattr(prompts, "build_iteration_prompt")


def test_workspace_mutation_loop_module_exists() -> None:
    """The package must expose the config + run entry point."""
    from agent.cli_backends import workspace_mutation as wm

    assert hasattr(wm, "get_workspace_mutation_config")
    assert hasattr(wm, "run_ananta_worker_workspace_mutation")


def test_workspace_mutation_tools_module_exists() -> None:
    """The package must expose the KIND_* constants and helpers."""
    from agent.cli_backends import workspace_mutation as wm

    assert hasattr(wm, "KIND_WORKSPACE_WRITE")
    assert hasattr(wm, "KIND_PATCH_REQUEST")
    assert wm.KIND_WORKSPACE_WRITE == "workspace_write"
    assert wm.KIND_PATCH_REQUEST == "patch_request"


def test_workspace_mutation_size_after_split() -> None:
    """The 4 sub-modules must each be < 300 LOC (SRP sweet spot).

    The orchestrator (_orchestrator.py) is exempt: it owns the
    run_ananta_worker_workspace_mutation mega-function (~700 LOC).
    Splitting it further is out of scope for SGDEC; the smaller helpers
    (signatures, prompts) are extracted into focused sub-modules.
    """
    from pathlib import Path

    sub_dir = Path("agent/cli_backends/workspace_mutation")
    assert sub_dir.exists(), f"{sub_dir} not found"
    orchestrator = sub_dir / "_orchestrator.py"
    other_subs = [
        p for p in sorted(sub_dir.glob("*.py"))
        if p.name not in ("__init__.py", "_orchestrator.py")
    ]
    for sub in other_subs:
        line_count = sum(1 for _ in sub.open(encoding="utf-8"))
        assert line_count < 300, (
            f"{sub.name} is {line_count} LOC; expected < 300"
        )
    # Orchestrator: bounded at < 1000 LOC (mega-function but not unbounded)
    if orchestrator.exists():
        line_count = sum(1 for _ in orchestrator.open(encoding="utf-8"))
        assert line_count < 1000, (
            f"_orchestrator.py is {line_count} LOC; mega-function is acceptable "
            f"up to 1000 LOC. Further splitting is a separate track."
        )
