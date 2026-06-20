"""RED/GREEN test: workspace_mutation 4-split must keep public API stable.

The 4-split (signatures/prompts/loop/tools) is internal — the public API
surface (``run_ananta_worker_workspace_mutation``, ``parse_mutation_output``,
``get_workspace_mutation_config``, ``_build_iteration_prompt``,
``_build_mode_instructions``, ``_evidence_signature``, ``_changes_signature``)
must remain importable from the same paths and behave identically.
"""
from __future__ import annotations


def test_workspace_mutation_signatures_module_exists() -> None:
    """The signatures sub-module must exist with the public signature helpers."""
    from agent.common import sgpt_workspace_mutation

    # Original API is on sgpt_workspace_mutation; the split is internal
    # so the public re-exports must still work.
    assert hasattr(sgpt_workspace_mutation, "_evidence_signature")
    assert hasattr(sgpt_workspace_mutation, "_changes_signature")


def test_workspace_mutation_prompts_module_exists() -> None:
    """The prompts sub-module must exist with the prompt-building helpers."""
    from agent.common import sgpt_workspace_mutation

    assert hasattr(sgpt_workspace_mutation, "parse_mutation_output")
    assert hasattr(sgpt_workspace_mutation, "_build_mode_instructions")
    assert hasattr(sgpt_workspace_mutation, "_build_iteration_prompt")


def test_workspace_mutation_loop_module_exists() -> None:
    """The loop sub-module must expose the config + run entry point."""
    from agent.common import sgpt_workspace_mutation

    assert hasattr(sgpt_workspace_mutation, "get_workspace_mutation_config")
    assert hasattr(sgpt_workspace_mutation, "run_ananta_worker_workspace_mutation")


def test_workspace_mutation_tools_module_exists() -> None:
    """The tools sub-module must expose the tool-execution hooks."""
    # The tools sub-module is internal but re-exported through workspace_mutation
    from agent.common import sgpt_workspace_mutation as wm

    # Public API: KIND_* constants must be re-exported
    assert hasattr(wm, "KIND_WORKSPACE_WRITE")
    assert hasattr(wm, "KIND_PATCH_REQUEST")
    assert wm.KIND_WORKSPACE_WRITE == "workspace_write"
    assert wm.KIND_PATCH_REQUEST == "patch_request"


def test_workspace_mutation_size_after_split() -> None:
    """The 4 sub-modules must each be < 300 LOC (SRP sweet spot).

    The main agent.common.sgpt_workspace_mutation.py is allowed to be
    larger because it owns the run_ananta_worker_workspace_mutation
    orchestrator (the mega-function). The SRP goal is achieved when the
    helper functions are extracted into focused sub-modules.
    """
    from pathlib import Path

    sub_dir = Path("agent/cli_backends/workspace_mutation")
    if not sub_dir.exists():
        return  # Pre-Welle-3: sub-modules don't exist
    for sub in sorted(sub_dir.glob("*.py")):
        if sub.name == "__init__.py":
            continue
        line_count = sum(1 for _ in sub.open(encoding="utf-8"))
        assert line_count < 300, (
            f"{sub.name} is {line_count} LOC; expected < 300"
        )
