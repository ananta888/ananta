"""Goal finalization once all of its tasks reached a terminal status.

A goal is completed only when every task finished successfully and, for
workspace-producing goals, the output directory carries the required
artifacts (for example the Fibonacci project evidence); otherwise it fails with
a diagnostic reason. The task repository is passed in by the caller.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.services.task_status_service import normalize_task_status

_TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}


def _resolve_goal_output_dir(raw_output_dir: str) -> Path:
    raw = str(raw_output_dir or "").strip()
    if not raw:
        return Path("")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    cwd = Path.cwd()
    direct = (cwd / candidate).resolve()
    workspace_relative = (cwd / "project-workspaces" / candidate).resolve()
    if direct.exists():
        return direct
    if workspace_relative.exists():
        return workspace_relative
    return workspace_relative


def _workspace_file_count(path: Path) -> int:
    if not str(path):
        return 0
    if not path.exists() or not path.is_dir():
        return 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
    return count


def _workspace_has_any(path: Path, patterns: list[str]) -> bool:
    if not str(path) or not path.exists() or not path.is_dir():
        return False
    for pattern in patterns:
        if any(path.glob(pattern)):
            return True
    return False


def _goal_requires_fibonacci_artifacts(goal: Any) -> bool:
    goal_text = str(getattr(goal, "goal", "") or "").lower()
    summary_text = str(getattr(goal, "summary", "") or "").lower()
    mode = str(getattr(goal, "mode", "") or "").strip().lower()
    if "fibonacci" not in goal_text and "fibonacci" not in summary_text:
        return False
    return mode == "new_software_project" or "fibonacci" in goal_text or "fibonacci" in summary_text


def _workspace_has_file_matching(path: Path, predicate) -> bool:
    if not str(path) or not path.exists() or not path.is_dir():
        return False
    for item in path.rglob("*"):
        if item.is_file() and predicate(item):
            return True
    return False


def _goal_has_required_fibonacci_evidence(resolved_output_dir: Path) -> tuple[bool, dict[str, bool]]:
    source_dir = resolved_output_dir / "src" / "fibonacci"
    tests_dir = resolved_output_dir / "tests"
    has_source_file = _workspace_has_file_matching(
        source_dir,
        lambda item: item.suffix == ".py",
    )
    has_pytest_style_test = _workspace_has_file_matching(
        tests_dir,
        lambda item: item.suffix == ".py" and item.name.startswith("test_"),
    )
    has_pytest_evidence = False
    if resolved_output_dir.exists() and resolved_output_dir.is_dir():
        for item in resolved_output_dir.rglob("*"):
            if not item.is_file():
                continue
            try:
                relative_item = item.relative_to(resolved_output_dir).as_posix().lower()
            except Exception:
                relative_item = item.name.lower()
            if "pytest" in item.name.lower() or "pytest" in relative_item:
                has_pytest_evidence = True
                break
    evidence = {
        "has_source_file": has_source_file,
        "has_pytest_style_test": has_pytest_style_test,
        "has_pytest_evidence": has_pytest_evidence,
    }
    return all(evidence.values()), evidence


def finalize_goal_if_all_tasks_terminal(goal_id: str, *, task_repository: Any) -> None:
    """Close a goal once every task is terminal, gated by workspace evidence."""
    try:
        from agent.repository import goal_repo

        goal_tasks = task_repository.get_by_goal_id(goal_id)
        if not goal_tasks:
            return
        statuses = {normalize_task_status(getattr(t, "status", None), default="todo") for t in goal_tasks}
        if not statuses.issubset(_TERMINAL_TASK_STATUSES):
            return
        goal = goal_repo.get_by_id(goal_id)
        if not goal or goal.status not in {"planned", "in_progress", "running"}:
            return
        tasks_by_id = {
            str(getattr(item, "id", "") or ""): item
            for item in goal_tasks
        }

        def is_successful_terminal(item: Any) -> bool:
            item_status = normalize_task_status(
                getattr(item, "status", None),
                default="todo",
            )
            if item_status in {"completed", "skipped"}:
                return True
            if (
                item_status != "cancelled"
                or str(
                    getattr(item, "derivation_reason", "") or ""
                )
                != "goal_task_recovery"
                or str(
                    getattr(item, "status_reason_code", "") or ""
                )
                != "recovery_parent_terminal"
            ):
                return False
            source = tasks_by_id.get(
                str(getattr(item, "source_task_id", "") or "")
            )
            return normalize_task_status(
                getattr(source, "status", None),
                default="todo",
            ) in {"completed", "skipped"}

        new_status = (
            "completed"
            if statuses and all(
                is_successful_terminal(item)
                for item in goal_tasks
            )
            else "failed"
        )
        current_preferences = dict(goal.execution_preferences or {})
        if new_status == "completed":
            raw_output_dir = str(current_preferences.get("output_dir") or "").strip()
            if not raw_output_dir:
                if _goal_requires_fibonacci_artifacts(goal):
                    new_status = "failed"
                    current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                    current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                    current_preferences["finalization_diagnostics"] = {
                        "output_dir": "",
                        "resolved_output_dir": "",
                        "workspace_file_count": 0,
                        "fibonacci_evidence": {
                            "has_source_file": False,
                            "has_pytest_style_test": False,
                            "has_pytest_evidence": False,
                            "output_dir_available": False,
                        },
                    }
                    goal.execution_preferences = current_preferences
            else:
                resolved_output_dir = _resolve_goal_output_dir(raw_output_dir)
                file_count = _workspace_file_count(resolved_output_dir)
                diagnostics = {
                    "output_dir": raw_output_dir,
                    "resolved_output_dir": str(resolved_output_dir),
                    "workspace_file_count": file_count,
                }
                requires_fibonacci_evidence = _goal_requires_fibonacci_artifacts(goal) or (resolved_output_dir / "src" / "fibonacci").exists()
                if requires_fibonacci_evidence:
                    if not resolved_output_dir.exists():
                        new_status = "failed"
                        current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                        current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                        diagnostics["fibonacci_evidence"] = {
                            "has_source_file": False,
                            "has_pytest_style_test": False,
                            "has_pytest_evidence": False,
                            "output_dir_available": False,
                        }
                    else:
                        has_required_evidence, fibonacci_evidence = _goal_has_required_fibonacci_evidence(resolved_output_dir)
                        diagnostics["fibonacci_evidence"] = fibonacci_evidence
                        if not has_required_evidence:
                            new_status = "failed"
                            current_preferences["last_status_reason"] = "missing_required_fibonacci_artifacts"
                            current_preferences["failure_classification"] = "missing_required_fibonacci_artifacts"
                current_preferences["finalization_diagnostics"] = diagnostics
                if file_count <= 0:
                    new_status = "failed"
                    current_preferences["last_status_reason"] = "no_workspace_artifact_created"
                    current_preferences["failure_classification"] = "no_workspace_artifact_created"
                goal.execution_preferences = current_preferences
        from agent.services.lifecycle_service import (
            get_goal_lifecycle_service,
        )

        get_goal_lifecycle_service().transition_goal(
            goal,
            target_status=new_status,
            reason=str(
                current_preferences.get("last_status_reason") or ""
            )
            or None,
        )
        logging.info("Goal %s finalized as %s (all %d tasks terminal)", goal_id, new_status, len(goal_tasks))
    except Exception as exc:
        logging.warning("_maybe_finalize_goal(%s) error: %s", goal_id, exc)
