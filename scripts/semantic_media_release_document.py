"""Release document assembly and source/config projections of the semantic-media program release gate.

The release gate fingerprints its own implementation through
``_RELEASE_CORE_SOURCE_PATHS``; every module of the gate is listed there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import jsonschema

from agent.services.semantic_media_program_evidence import (
    ProgramEvidenceError,
    assert_content_free,
    canonical_sha256,
    source_hash,
)
from agent.services.semantic_media_rollout_policy import ROLLOUT_STAGES
from scripts.semantic_media_release_paths import ROOT, SCHEMA, TODO
from scripts.semantic_media_release_gate_catalog import (
    QA_GATES,
    task_gate_requirements,
)


_RELEASE_CORE_SOURCE_PATHS = (
    "agent/services/semantic_media_program_evidence.py",
    "agent/models/semantic_media_content_policy.py",
    "agent/services/semantic_media_rollout_policy.py",
    "docs/operations/semantic-media-rollout.md",
    "schemas/release/semantic_media_program_evidence.v1.json",
    "scripts/run_semantic_media_program_release_gate.py",
    "scripts/semantic_media_release_document.py",
    "scripts/semantic_media_release_gate_catalog.py",
    "scripts/semantic_media_release_paths.py",
    "todos/archiv/todo.ai-snake-semantic-media-speech-program.json",
)
_CONFIG_PREFIXES = (".github/workflows/", "config/", "docker/", "schemas/")
_CONFIG_NAMES = frozenset({".env.example", "pyproject.toml", "playwright.config.ts"})
_CONFIG_SUFFIXES = (
    ".ini",
    ".json",
    ".lock",
    ".toml",
    ".yaml",
    ".yml",
)
_MAX_PROGRAM_SOURCE_FILES = 4_096
_IGNORED_SOURCE_PARTS = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})


def _result(gate_id: str, status: str, reason_codes: Sequence[str], evidence: Any) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "evidence_sha256": canonical_sha256(evidence),
    }


def _unverified(gate_id: str, reason: str) -> dict[str, Any]:
    return _result(gate_id, "unverified", (reason,), {"gate_id": gate_id, "verified_runs": 0})


def build_release_document(
    *,
    gates: Sequence[Mapping[str, Any]],
    stage: str,
    todo_document: Mapping[str, Any] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    if stage not in ROLLOUT_STAGES:
        raise ProgramEvidenceError("release_stage_invalid")
    by_id = {str(row["id"]): dict(row) for row in gates}
    if len(by_id) != len(gates):
        raise ProgramEvidenceError("release_gate_duplicate")
    todo = dict(todo_document) if todo_document is not None else json.loads(TODO.read_text(encoding="utf-8"))
    milestones, task_rows = _todo_inventory(todo)
    task_results: dict[str, dict[str, Any]] = {}
    task_milestones: dict[str, str] = {}
    task_statuses: dict[str, str] = {}
    for task_id, milestone_id, declared_status in task_rows:
        task_milestones[task_id] = milestone_id
        task_statuses[task_id] = declared_status
        if task_id == "ASMP-QA-012":
            continue
        task_results[task_id] = _task_result(
            task_id,
            declared_status=declared_status,
            requirements=task_gate_requirements(task_id),
            by_id=by_id,
        )

    milestone_results: list[dict[str, Any]] = []
    for milestone in milestones:
        if milestone == "ASMP-M11":
            continue
        rows = tuple(result for task_id, result in task_results.items() if task_milestones.get(task_id) == milestone)
        row = _aggregate_rows(milestone, rows) if rows else _unverified(milestone, "milestone_task_evidence_missing")
        milestone_results.append(row)

    qa_results = {task_id: task_results[task_id] for task_id in QA_GATES if task_id in task_results}
    prerequisites_verified = all(row["status"] != "unverified" for row in milestone_results) and all(
        row["status"] != "unverified" for row in qa_results.values()
    )
    qa12_gate = _result(
        "ASMP-QA-012",
        "passed" if prerequisites_verified else "unverified",
        () if prerequisites_verified else ("release_prerequisite_evidence_incomplete",),
        {"prerequisites_verified": prerequisites_verified, "stage": stage},
    )
    qa12 = _apply_declared_task_status(
        qa12_gate,
        declared_status=task_statuses.get("ASMP-QA-012", "todo"),
    )
    qa_results["ASMP-QA-012"] = qa12
    task_results["ASMP-QA-012"] = qa12
    m11 = _aggregate_rows("ASMP-M11", tuple(qa_results.values()))
    milestone_results.append(m11)

    tasks = [task_results[task_id] for task_id, _milestone, _status in task_rows]
    decision = (
        "go"
        if qa12["status"] == "passed"
        and all(row["status"] == "passed" for row in gates)
        and all(row["status"] == "passed" for row in milestone_results)
        and all(row["status"] == "passed" for row in tasks)
        else "no_go"
    )
    reasons = sorted(
        {
            reason
            for row in (*gates, *milestone_results, *tasks)
            if row["status"] != "passed"
            for reason in row["reason_codes"]
        }
    )[:128]
    source_projection = program_source_projection(todo, root=root)
    config_projection = program_config_projection(source_projection)
    source_digest = canonical_sha256(
        {
            "files_sha256": source_hash(root, source_projection),
            # Bind the exact in-memory acceptance state used for this decision,
            # including tests that deliberately supply a projected document.
            "todo_sha256": canonical_sha256(todo),
        }
    )
    document = {
        "schema": "ananta.semantic-media-program-release-evidence.v1",
        "decision": decision,
        "rollout_stage": stage,
        "ordinary_call_action": "preserve",
        "source_sha256": source_digest,
        "config_sha256": canonical_sha256(
            {
                "stage": stage,
                "gate_ids": sorted(by_id),
                "files_sha256": source_hash(root, config_projection),
            }
        ),
        "gates": sorted((dict(row) for row in gates), key=lambda row: row["id"]),
        "milestones": sorted(milestone_results, key=lambda row: row["id"]),
        "tasks": sorted(tasks, key=lambda row: row["id"]),
        "reason_codes": reasons,
    }
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(document)
    assert_content_free(document)
    return document


def program_source_projection(todo: Mapping[str, Any], *, root: Path = ROOT) -> tuple[str, ...]:
    """Resolve the reviewed program surface from task-owned paths.

    Release output artifacts are evidence inputs/outputs and are deliberately
    excluded from the source projection. Missing source declarations and empty
    globs fail closed instead of silently shrinking the reviewed surface.
    """

    declared: set[str] = set(_RELEASE_CORE_SOURCE_PATHS)
    tasks = todo.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ProgramEvidenceError("program_task_inventory_missing")
    for task in tasks:
        if not isinstance(task, Mapping):
            raise ProgramEvidenceError("program_task_inventory_invalid")
        affected = task.get("affected_files")
        if not isinstance(affected, list) or not affected:
            raise ProgramEvidenceError("program_task_source_projection_missing")
        for raw in affected:
            relative = str(raw or "")
            candidate = Path(relative)
            if not relative or candidate.is_absolute() or ".." in candidate.parts:
                raise ProgramEvidenceError("program_source_path_unsafe")
            if candidate.parts and candidate.parts[0] == "artifacts":
                continue
            if any(character in relative for character in "*?["):
                matches = tuple(
                    path.relative_to(root).as_posix() for path in sorted(root.glob(relative)) if path.is_file()
                )
                if not matches:
                    raise ProgramEvidenceError("program_source_glob_empty")
                declared.update(matches)
            else:
                resolved = root / candidate
                if resolved.is_dir():
                    matches = tuple(
                        path.relative_to(root).as_posix()
                        for path in sorted(resolved.rglob("*"))
                        if path.is_file() and not (_IGNORED_SOURCE_PARTS & set(path.parts))
                    )
                    if not matches:
                        raise ProgramEvidenceError("program_source_directory_empty")
                    declared.update(matches)
                else:
                    declared.add(candidate.as_posix())
    projection = tuple(sorted(declared))
    if len(projection) > _MAX_PROGRAM_SOURCE_FILES:
        raise ProgramEvidenceError("program_source_projection_too_large")
    # source_hash performs the final path and existence validation.
    source_hash(root, projection)
    return projection


def program_config_projection(source_projection: Sequence[str]) -> tuple[str, ...]:
    """Select deploy/runtime policy inputs from the complete source surface."""

    selected = tuple(
        sorted(
            path
            for path in set(source_projection)
            if path in _CONFIG_NAMES
            or path.startswith(_CONFIG_PREFIXES)
            or Path(path).name.startswith("docker-compose")
            or Path(path).name.startswith("compose.")
            or Path(path).name.startswith("requirements.")
            or Path(path).name in {"package.json", "package-lock.json"}
            or path.endswith(_CONFIG_SUFFIXES)
        )
    )
    if not selected:
        raise ProgramEvidenceError("program_config_projection_missing")
    return selected


def _aggregate(identifier: str, requirements: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    rows = tuple(
        by_id.get(requirement, _unverified(requirement, "required_gate_missing")) for requirement in requirements
    )
    return _aggregate_rows(identifier, rows)


def _aggregate_rows(identifier: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if rows and all(row["status"] == "passed" for row in rows):
        status = "passed"
        reasons: tuple[str, ...] = ()
    elif any(row["status"] == "failed" for row in rows):
        status = "failed"
        reasons = tuple(sorted({reason for row in rows for reason in row["reason_codes"]}))
    else:
        status = "unverified"
        reasons = tuple(sorted({reason for row in rows for reason in row["reason_codes"]}))
    return _result(identifier, status, reasons, [dict(row) for row in rows])


def _task_result(
    task_id: str,
    *,
    declared_status: str,
    requirements: Sequence[str],
    by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not requirements:
        gate_result = _unverified(task_id, "task_gate_mapping_missing")
    else:
        gate_result = _aggregate(task_id, requirements, by_id)
    return _apply_declared_task_status(
        gate_result,
        declared_status=declared_status,
    )


def _apply_declared_task_status(
    gate_result: Mapping[str, Any],
    *,
    declared_status: str,
) -> dict[str, Any]:
    if declared_status == "done":
        return dict(gate_result)
    gate_status = str(gate_result["status"])
    status = "failed" if gate_status == "failed" else "unverified"
    reasons = tuple(
        sorted(
            {
                *gate_result["reason_codes"],
                "task_acceptance_not_complete",
            }
        )
    )
    return _result(
        str(gate_result["id"]),
        status,
        reasons,
        {"declared_status": declared_status, "gate_result": dict(gate_result)},
    )


def _todo_inventory(todo: Any) -> tuple[list[str], list[tuple[str, str, str]]]:
    milestones: set[str] = set()
    tasks: dict[str, tuple[str, str]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            identifier = str(value.get("id") or "")
            if re.fullmatch(r"ASMP-M\d+", identifier):
                milestones.add(identifier)
            milestone_id = str(value.get("milestone_id") or "")
            if identifier.startswith("ASMP-") and milestone_id and not re.fullmatch(r"ASMP-M\d+", identifier):
                tasks[identifier] = (
                    milestone_id,
                    str(value.get("status") or "todo"),
                )
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(todo)
    return sorted(milestones), sorted(
        (task_id, milestone_id, status) for task_id, (milestone_id, status) in tasks.items()
    )
