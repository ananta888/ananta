from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.planning_track_pipeline_service import validate_planning_track_with_details, validate_summary_consistency


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("todo_file_must_be_json_object")
    return payload


def _is_track_payload(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("tasks"), list) and isinstance(payload.get("milestones"), list)


def _has_legacy_epics(payload: dict[str, Any]) -> bool:
    epics = list(payload.get("epics") or [])
    return bool(epics) and any(isinstance(item, dict) and isinstance(item.get("tasks"), list) for item in epics)


def _convert_legacy_epics_preview(payload: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(payload or {})
    tasks: list[dict[str, Any]] = []
    epics = [dict(item) for item in list(candidate.get("epics") or []) if isinstance(item, dict)]
    for epic in epics:
        epic_id = str(epic.get("id") or "").strip()
        for index, task in enumerate(list(epic.get("tasks") or [])):
            if not isinstance(task, dict):
                continue
            item = dict(task)
            task_id = str(item.get("id") or "").strip() or f"{epic_id or 'E'}-T{index + 1:02d}"
            item["id"] = task_id
            item.setdefault("status", "todo")
            item.setdefault("priority", "P2")
            item.setdefault("risk", "medium")
            item.setdefault("type", "migration")
            acceptance = [str(v).strip() for v in list(item.get("acceptance_criteria") or []) if str(v).strip()]
            if not acceptance:
                acceptance = [f"Task {task_id} converted from legacy epics structure."]
            item["acceptance_criteria"] = acceptance
            if epic_id and not str(item.get("milestone_id") or "").strip():
                item["milestone_id"] = epic_id
            tasks.append(item)
    candidate["tasks"] = tasks
    candidate.pop("epics", None)
    milestones = [dict(item) for item in list(candidate.get("milestones") or []) if isinstance(item, dict)]
    if not milestones and epics:
        candidate["milestones"] = [
            {
                "id": str(epic.get("id") or f"M{idx + 1:02d}"),
                "title": str(epic.get("title") or epic.get("id") or f"Epic {idx + 1}"),
                "task_ids": [str(task.get("id") or "").strip() for task in tasks if str(task.get("milestone_id") or "").strip() == str(epic.get("id") or "").strip()],
                "status": "todo",
            }
            for idx, epic in enumerate(epics)
        ]
    return candidate


def doctor_track_payload(payload: dict[str, Any]) -> dict[str, Any]:
    schema_issues = validate_planning_track_with_details(payload)
    summary = validate_summary_consistency(payload, repair_mode=False)
    summary_issues = [dict(item) for item in list(summary.get("issues") or []) if isinstance(item, dict)]
    all_issues = [*schema_issues, *summary_issues]
    return {
        "schema": "planning_summary_doctor.v1",
        "format": "planning_track",
        "valid": not all_issues,
        "issues": all_issues,
        "summary_recalculation_status": str(summary.get("summary_recalculation_status") or "not_needed"),
        "repaired_fields": [str(item) for item in list(summary.get("repaired_fields") or []) if str(item).strip()],
        "old_summary_hash": str(summary.get("old_summary_hash") or ""),
        "new_summary_hash": str(summary.get("new_summary_hash") or ""),
    }


def doctor_file(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    payload = _load_json(target)
    if _has_legacy_epics(payload):
        preview = _convert_legacy_epics_preview(payload)
        return {
            "schema": "planning_summary_doctor.v1",
            "format": "legacy_epics",
            "path": str(target),
            "valid": False,
            "issues": [
                {
                    "path": "epics",
                    "reason_code": "legacy_epics_structure_detected",
                    "human_message": "Legacy epics.tasks structure detected; migrate to flat tasks[].",
                }
            ],
            "convert_preview": {
                "tasks_count": len(list(preview.get("tasks") or [])),
                "milestones_count": len(list(preview.get("milestones") or [])),
                "sample_task_ids": [str(item.get("id") or "") for item in list(preview.get("tasks") or [])[:5]],
            },
        }
    if not _is_track_payload(payload):
        return {
            "schema": "planning_summary_doctor.v1",
            "format": "unsupported",
            "path": str(target),
            "valid": False,
            "issues": [
                {
                    "path": "$",
                    "reason_code": "unsupported_todo_format",
                    "human_message": "File is not a planning-track todo format with flat tasks[].",
                }
            ],
        }
    result = doctor_track_payload(payload)
    result["path"] = str(target)
    return result


def fix_file(path: str | Path, *, write: bool = True) -> dict[str, Any]:
    target = Path(path).resolve()
    payload = _load_json(target)
    if _has_legacy_epics(payload):
        preview_payload = _convert_legacy_epics_preview(payload)
        repaired = validate_summary_consistency(preview_payload, repair_mode=True)
        repaired_payload = dict(repaired.get("repaired_payload") or preview_payload)
        changed_fields = [str(item) for item in list(repaired.get("repaired_fields") or []) if str(item).strip()]
        changed = True
        if write:
            target.write_text(json.dumps(repaired_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = doctor_track_payload(repaired_payload)
        return {
            **status,
            "path": str(target),
            "format": "planning_track",
            "changed": changed,
            "write": bool(write),
            "repaired_fields": sorted(set(changed_fields + ["tasks"])),
            "payload": repaired_payload,
        }
    if not _is_track_payload(payload):
        return {
            "schema": "planning_summary_doctor.v1",
            "format": "unsupported",
            "path": str(target),
            "changed": False,
            "valid": False,
            "issues": [
                {
                    "path": "$",
                    "reason_code": "unsupported_todo_format",
                    "human_message": "File is not a planning-track todo format with flat tasks[].",
                }
            ],
        }
    repaired = validate_summary_consistency(payload, repair_mode=True)
    repaired_payload = dict(repaired.get("repaired_payload") or payload)
    changed_fields = [str(item) for item in list(repaired.get("repaired_fields") or []) if str(item).strip()]
    changed = bool(changed_fields)
    if write and changed:
        target.write_text(json.dumps(repaired_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = doctor_track_payload(repaired_payload)
    return {
        **status,
        "path": str(target),
        "changed": changed,
        "write": bool(write),
        "repaired_fields": changed_fields,
        "payload": repaired_payload,
    }


def migrate_track_todos(*, repo_root: str | Path, dry_run: bool = True, convert_epics: bool = False) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    todos_dir = root / "todos"
    files: list[Path] = []
    for candidate in sorted(todos_dir.rglob("*.json")):
        rel = candidate.relative_to(todos_dir)
        if rel.parts and rel.parts[0] in {"archive", "kritis"}:
            continue
        files.append(candidate)
    results: list[dict[str, Any]] = []
    for file_path in files:
        payload = _load_json(file_path)
        has_legacy_epics = _has_legacy_epics(payload)
        if has_legacy_epics and not bool(convert_epics):
            results.append(
                {
                    "path": str(file_path),
                    "changed": False,
                    "valid": False,
                    "legacy_epics_detected": True,
                    "repaired_fields": [],
                    "warning": "legacy_epics_detected_use_convert_epics",
                }
            )
            continue
        if not _is_track_payload(payload) and not has_legacy_epics:
            continue
        fixed = fix_file(file_path, write=not dry_run)
        results.append(
            {
                "path": str(file_path),
                "changed": bool(fixed.get("changed")),
                "valid": bool(fixed.get("valid")),
                "repaired_fields": list(fixed.get("repaired_fields") or []),
                "legacy_epics_detected": has_legacy_epics,
            }
        )
    return {
        "schema": "planning_summary_migration_report.v1",
        "repo_root": str(root),
        "dry_run": bool(dry_run),
        "convert_epics": bool(convert_epics),
        "scanned": len(files),
        "track_files": len(results),
        "changed": len([item for item in results if bool(item.get("changed"))]),
        "results": results,
    }
