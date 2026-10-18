"""Read guidance for the required source-analysis gate.

The gate observes source-file ranges only: documents and guessed file names
cannot unlock a mutation. This module owns the two loop-facing pieces that keep
a run satisfiable — the source-first ordering of CodeCompass materialization and
the ``action_required`` explanation attached to a read result — so the read
result annotation stays testable without the policy/execution layer around it.
"""
from __future__ import annotations

import pathlib
from typing import Any

from agent.cli_backends.workspace_mutation.analysis import is_source_reference
from agent.cli_backends.workspace_mutation.session import MutationSession

_MAX_ACTIONABLE_EXISTING_PATHS = 8


def prioritize_source_refs(refs: list[Any]) -> list[Any]:
    """Put observable source files ahead of documents in a CodeCompass bundle.

    Location refs arrive ranked by retrieval score, which favours documents and
    reports. Materializing only those leaves the required analysis gate without
    a single observed source range, so source refs keep their relative order and
    move in front of the non-source refs within the same materialization budget.
    """
    source_refs: list[Any] = []
    other_refs: list[Any] = []
    for ref in refs:
        target = source_refs if is_source_reference(str((ref or {}).get("path") or "")) else other_refs
        target.append(ref)
    return source_refs + other_refs


def _existing_paths(workspace: pathlib.Path, directory: pathlib.Path) -> list[str]:
    """Bounded workspace-relative file list, source files first."""
    try:
        entries = sorted(entry for entry in directory.iterdir() if entry.is_file())
        relative = [str(entry.relative_to(workspace)) for entry in entries]
    except (OSError, ValueError):
        return []
    source_paths = [path for path in relative if is_source_reference(path)]
    return (source_paths + [path for path in relative if path not in source_paths])[:_MAX_ACTIONABLE_EXISTING_PATHS]


def annotate_analysis_read(session: MutationSession, result: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Explain why a read cannot unlock the analysis gate and name a real source file.

    The gate observes source-file ranges only. A missing guessed path or a
    successful document read used to come back bare, so the model spent its
    iterations re-reading documents and inventing file names instead of acting.
    """
    if session.cfg.get("require_code_analysis") is not True or not session.analysis_gate.pending:
        return
    path = str(arguments.get("path") or "")
    ok = str(result.get("status") or "") == "ok"
    if ok and is_source_reference(path):
        return
    data = dict(result.get("data") or {})
    if ok:
        data["action_required"] = "analysis_requires_a_source_file_range"
    else:
        existing = _existing_paths(session.workspace, (session.workspace / path).parent)
        source_paths = [candidate for candidate in existing if is_source_reference(candidate)]
        if source_paths:
            data["action_required"] = "read_one_of_the_existing_source_paths_instead"
            data["existing_paths"] = source_paths
        elif existing:
            data["action_required"] = "analysis_requires_a_source_file_range"
        else:
            data["action_required"] = "path_does_not_exist_use_repo_list_files"
    result["data"] = data
