#!/usr/bin/env python3
"""Prepare or execute the enterprise-organization convergence gate.

The default mode is deliberately non-executing. Complex suites require both
``--execute-full`` and an explicit environment approval so implementation work
cannot accidentally start integration, browser, or performance tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TODO = ROOT / "todos/todo.enterprise-agentic-scrum-organization-blueprints.json"
DEFAULT_PROFILE = ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/enterprise-agentic-scrum-organization-release.json"
REPORT_SCHEMA = "ananta.enterprise-organization.release-result.v1"
PROFILE_SCHEMA = "ananta.enterprise-organization.release-profile.v1"
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
ALLOWED_TIERS = {"static", "complex", "full_e2e"}


class GateConfigurationError(ValueError):
    """Raised when the immutable release-gate inputs are malformed."""


def _read_json(path: Path, *, maximum_bytes: int = 4_000_000) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GateConfigurationError(f"unreadable_json:{path}") from exc
    if len(raw) > maximum_bytes:
        raise GateConfigurationError(f"oversized_json:{path}")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateConfigurationError(f"invalid_json:{path}") from exc
    if not isinstance(payload, dict):
        raise GateConfigurationError(f"json_object_required:{path}")
    return payload


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_rows(todo: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    categories = todo.get("categories")
    if not isinstance(categories, list):
        raise GateConfigurationError("todo_categories_invalid")
    rows: list[Mapping[str, Any]] = []
    for category in categories:
        if not isinstance(category, Mapping) or not isinstance(category.get("items"), list):
            raise GateConfigurationError("todo_category_items_invalid")
        for item in category["items"]:
            if not isinstance(item, Mapping):
                raise GateConfigurationError("todo_task_invalid")
            rows.append(item)
    return rows


def evaluate_task_graph(
    todo: Mapping[str, Any],
    *,
    release_task_id: str,
) -> dict[str, Any]:
    """Return a deterministic, fail-closed assessment of the task DAG."""

    rows = _task_rows(todo)
    ids = [row.get("id") for row in rows]
    reasons: set[str] = set()
    if any(not isinstance(task_id, str) or not task_id for task_id in ids):
        reasons.add("task_id_invalid")
    valid_ids = [task_id for task_id in ids if isinstance(task_id, str) and task_id]
    if len(valid_ids) != len(set(valid_ids)):
        reasons.add("task_id_duplicate")
    row_by_id = {str(row["id"]): row for row in rows if isinstance(row.get("id"), str) and row.get("id")}
    if release_task_id not in row_by_id:
        reasons.add("release_task_missing")

    dependencies: dict[str, tuple[str, ...]] = {}
    dependents: dict[str, set[str]] = {task_id: set() for task_id in row_by_id}
    edge_count = 0
    for task_id, row in row_by_id.items():
        raw = row.get("depends_on", [])
        if (
            not isinstance(raw, list)
            or any(not isinstance(dep, str) or not dep for dep in raw)
            or len(raw) != len(set(raw))
        ):
            reasons.add("task_dependencies_invalid")
            raw = []
        dependencies[task_id] = tuple(raw)
        edge_count += len(raw)
        for dependency in raw:
            if dependency not in row_by_id:
                reasons.add("task_dependency_unknown")
                continue
            dependents[dependency].add(task_id)

    indegree = {task_id: len(dependencies.get(task_id, ())) for task_id in row_by_id}
    queue = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    visited: list[str] = []
    while queue:
        task_id = queue.popleft()
        visited.append(task_id)
        for dependent in sorted(dependents[task_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if len(visited) != len(row_by_id):
        reasons.add("task_graph_cycle")

    leaves = sorted(task_id for task_id, children in dependents.items() if not children)
    if leaves != [release_task_id]:
        reasons.add("release_task_not_only_leaf")

    ancestors: set[str] = set()
    pending = list(dependencies.get(release_task_id, ()))
    while pending:
        task_id = pending.pop()
        if task_id in ancestors or task_id not in row_by_id:
            continue
        ancestors.add(task_id)
        pending.extend(dependencies.get(task_id, ()))
    expected_ancestors = set(row_by_id) - {release_task_id}
    if ancestors != expected_ancestors:
        reasons.add("release_task_missing_transitive_predecessors")

    incomplete = sorted(
        task_id for task_id, row in row_by_id.items() if task_id != release_task_id and row.get("status") != "completed"
    )
    if incomplete:
        reasons.add("release_predecessors_incomplete")

    declared = todo.get("meta", {}).get("dag_summary") if isinstance(todo.get("meta"), Mapping) else None
    actual_summary = {
        "nodes": len(row_by_id),
        "edges": edge_count,
        "roots": sorted(task_id for task_id, deps in dependencies.items() if not deps),
        "leaves": leaves,
    }
    if isinstance(declared, Mapping):
        for key in ("nodes", "edges", "roots", "leaves"):
            if declared.get(key) != actual_summary[key]:
                reasons.add("todo_dag_summary_mismatch")
                break
    else:
        reasons.add("todo_dag_summary_missing")

    return {
        "status": "passed" if not reasons else "failed",
        "reason_codes": sorted(reasons),
        "summary": actual_summary,
        "transitive_predecessor_count": len(ancestors),
        "expected_transitive_predecessor_count": len(expected_ancestors),
        "incomplete_predecessor_ids": incomplete,
    }


def _validate_profile(profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if profile.get("schema") != PROFILE_SCHEMA or profile.get("profile_version") != 1:
        raise GateConfigurationError("profile_schema_invalid")
    release_task_id = profile.get("release_task_id")
    approval_env = profile.get("complex_execution_approval_env")
    if not isinstance(release_task_id, str) or not release_task_id:
        raise GateConfigurationError("profile_release_task_invalid")
    if not isinstance(approval_env, str) or SAFE_ENV_NAME.fullmatch(approval_env) is None:
        raise GateConfigurationError("profile_approval_env_invalid")
    suites = profile.get("suites")
    if not isinstance(suites, list) or not suites:
        raise GateConfigurationError("profile_suites_invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    full_e2e_count = 0
    for raw in suites:
        if not isinstance(raw, Mapping):
            raise GateConfigurationError("profile_suite_invalid")
        suite_id = raw.get("id")
        tier = raw.get("tier")
        cwd = raw.get("cwd")
        timeout = raw.get("timeout_seconds")
        command = raw.get("command")
        if not isinstance(suite_id, str) or not suite_id or suite_id in seen:
            raise GateConfigurationError("profile_suite_id_invalid")
        if tier not in ALLOWED_TIERS:
            raise GateConfigurationError("profile_suite_tier_invalid")
        if not isinstance(cwd, str) or cwd.startswith("/") or ".." in Path(cwd).parts:
            raise GateConfigurationError("profile_suite_cwd_invalid")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            raise GateConfigurationError("profile_suite_timeout_invalid")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
        ):
            raise GateConfigurationError("profile_suite_command_invalid")
        resolved_cwd = (ROOT / cwd).resolve()
        try:
            resolved_cwd.relative_to(ROOT)
        except ValueError as exc:
            raise GateConfigurationError("profile_suite_cwd_escapes_root") from exc
        if not resolved_cwd.is_dir():
            raise GateConfigurationError("profile_suite_cwd_missing")
        seen.add(suite_id)
        full_e2e_count += int(tier == "full_e2e")
        normalized.append(
            {
                "id": suite_id,
                "tier": tier,
                "cwd": cwd,
                "timeout_seconds": timeout,
                "command": list(command),
            }
        )
    if full_e2e_count != 1:
        raise GateConfigurationError("profile_requires_exactly_one_full_e2e")
    return tuple(normalized)


def _hash_inputs(todo_path: Path, profile_path: Path) -> list[dict[str, str]]:
    paths = [todo_path, profile_path, ROOT / "todos/todo.schema.json", ROOT / "todos/todo.track.schema.json"]
    for pattern in (
        "schemas/blueprints/organization*.json",
        "schemas/blueprints/team_handoff*.json",
        "schemas/planning/*.json",
        "schemas/policies/*.json",
        "schemas/worker/task_followup_proposal*.json",
        "config/blueprints/standard/blueprints.d/*.json",
        "config/blueprints/standard/templates.d/*.json",
        "config/blueprints/standard/workflows.d/*.json",
        "config/blueprints/standard/policies.d/*.json",
        "config/blueprints/standard/organizations.d/*.json",
    ):
        paths.extend(sorted(ROOT.glob(pattern)))
    unique = sorted({path.resolve() for path in paths if path.is_file()})
    return [{"path": str(path.relative_to(ROOT)), "sha256": _file_sha256(path)} for path in unique]


def _run_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(suite["command"]),
            cwd=ROOT / str(suite["cwd"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=int(suite["timeout_seconds"]),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            **suite,
            "status": "failed",
            "reason_code": "command_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "command_unavailable",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    return {
        **suite,
        "status": "passed" if completed.returncode == 0 else "failed",
        "reason_code": None if completed.returncode == 0 else "command_failed",
        "exit_code": completed.returncode,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


def _not_run_suite(suite: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {**suite, "status": "not_run", "reason_code": reason}


def build_report(
    *,
    todo: Mapping[str, Any],
    profile: Mapping[str, Any],
    todo_path: Path,
    profile_path: Path,
    mode: str,
) -> tuple[dict[str, Any], bool]:
    suites = _validate_profile(profile)
    release_task_id = str(profile["release_task_id"])
    graph = evaluate_task_graph(todo, release_task_id=release_task_id)
    approval_env = str(profile["complex_execution_approval_env"])
    approved = os.environ.get(approval_env) == "1"
    if mode == "full" and not approved:
        raise GateConfigurationError(f"complex_execution_not_approved:{approval_env}=1")

    results: list[dict[str, Any]] = []
    for suite in suites:
        should_run = mode == "full" or (mode == "static" and suite["tier"] == "static")
        if should_run:
            results.append(_run_suite(suite))
        else:
            reason = "complex_tests_awaiting_approval" if suite["tier"] != "static" else "prepare_only"
            results.append(_not_run_suite(suite, reason))

    executed = [row for row in results if row["status"] != "not_run"]
    all_required_executed = len(executed) == len(results)
    all_executed_passed = bool(executed) and all(row["status"] == "passed" for row in executed)
    passed = mode == "full" and graph["status"] == "passed" and all_required_executed and all_executed_passed
    reason_codes: set[str] = set(graph["reason_codes"])
    if mode != "full":
        reason_codes.add("complex_tests_deferred_pending_user_approval")
    if any(row["status"] == "failed" for row in results):
        reason_codes.add("suite_failed")
    if not all_required_executed:
        reason_codes.add("required_suites_not_executed")

    input_hashes = _hash_inputs(todo_path, profile_path)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "gate_id": release_task_id,
        "status": "passed" if passed else ("failed" if mode == "full" else "deferred"),
        "mode": mode,
        "produced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "reason_codes": sorted(reason_codes),
        "task_graph": graph,
        "execution_policy": {
            "complex_tests_require_explicit_approval": True,
            "approval_environment_variable": approval_env,
            "approval_observed": approved,
        },
        "suites": results,
        "input_hashes": input_hashes,
        "input_set_sha256": _canonical_sha256(input_hashes),
        "known_residual_risks": []
        if passed
        else [
            (
                "Complex integration, browser, accessibility, and performance evidence "
                "is not accepted until the full gate runs with explicit approval."
            ),
            "A dirty working tree is implementation state, not immutable release evidence.",
        ],
        "evidence_policy": {
            "source_and_run_identifiers": (
                "Only assignment-provided allowlisted identifiers are valid; this report asserts none."
            ),
            "stdout_and_stderr": "Stored as SHA-256 digests only to avoid leaking secrets.",
        },
    }
    return report, passed


def _atomic_write(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute-static", action="store_true", help="run only cheap static suites")
    mode.add_argument(
        "--execute-full", action="store_true", help="run every suite; explicit environment approval required"
    )
    parser.add_argument("--todo", type=Path, default=DEFAULT_TODO)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mode = "full" if args.execute_full else "static" if args.execute_static else "prepare"
    try:
        todo = _read_json(args.todo)
        profile = _read_json(args.profile)
        report, passed = build_report(
            todo=todo,
            profile=profile,
            todo_path=args.todo,
            profile_path=args.profile,
            mode=mode,
        )
        _atomic_write(args.output, report)
    except GateConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"enterprise organization release gate: {report['status']}")
    print(f"report: {args.output}")
    return 0 if mode != "full" or passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
