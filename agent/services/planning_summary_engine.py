from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class PlanningSummaryEngine:
    generator = "planning-summary-engine"
    generator_version = "1.0"
    weighting_model_ref = "planning-weighting-default-v1"

    def recompute(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        candidate = dict(payload or {})
        tasks = [dict(item) for item in list(candidate.get("tasks") or []) if isinstance(item, dict)]
        raw_milestones = [dict(item) for item in list(candidate.get("milestones") or []) if isinstance(item, dict)]
        critical_path_ids = [str(item).strip() for item in list(candidate.get("critical_path_tasks") or []) if str(item).strip()]
        critical_path_set = set(critical_path_ids)

        normalized_tasks: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        for index, task in enumerate(tasks):
            normalized, task_issues = self._normalize_task_progress(task, task_index=index)
            normalized_tasks.append(normalized)
            issues.extend(task_issues)

        milestones, milestone_progress_summary = self._derive_milestones(
            tasks=normalized_tasks,
            milestones=raw_milestones,
            critical_path_set=critical_path_set,
        )
        candidate["tasks"] = normalized_tasks
        candidate["milestones"] = milestones
        candidate["tasks_status_summary"] = self._compute_tasks_status_summary(
            tasks=normalized_tasks,
            milestones=milestones,
            critical_path_ids=critical_path_ids,
            priority_scale=[str(item).strip() for item in list(candidate.get("priority_scale") or []) if str(item).strip()],
            risk_scale=[str(item).strip() for item in list(candidate.get("risk_scale") or []) if str(item).strip()],
        )
        candidate["tasks_type_summary"] = self._compute_tasks_type_summary(normalized_tasks)
        candidate["weighted_progress_summary"] = self._compute_weighted_progress_summary(
            tasks=normalized_tasks,
            critical_path_set=critical_path_set,
        )
        candidate["milestone_progress_summary"] = milestone_progress_summary
        candidate["progress_summary"] = self._compute_progress_summary(
            tasks_status_summary=dict(candidate.get("tasks_status_summary") or {}),
            weighted_progress_summary=dict(candidate.get("weighted_progress_summary") or {}),
        )
        candidate["derived_summary_metadata"] = self._compute_derived_summary_metadata(
            tasks=normalized_tasks,
            milestones=milestones,
            critical_path_ids=critical_path_ids,
        )
        return candidate, issues

    def _normalize_task_progress(self, task: dict[str, Any], *, task_index: int) -> tuple[dict[str, Any], list[dict[str, str]]]:
        normalized = dict(task or {})
        status = str(normalized.get("status") or "").strip()
        existing = _to_float(normalized.get("progress_percent"))
        issues: list[dict[str, str]] = []
        path = f"tasks/{task_index}/progress_percent"

        if status == "done":
            if existing is not None and existing != 100.0:
                issues.append(
                    {
                        "path": path,
                        "reason_code": "progress_status_mismatch",
                        "human_message": "done tasks must have progress_percent=100.",
                    }
                )
            normalized["progress_percent"] = 100.0
            return normalized, issues

        if status == "todo":
            if existing is not None and existing != 0.0:
                issues.append(
                    {
                        "path": path,
                        "reason_code": "progress_status_mismatch",
                        "human_message": "todo tasks must have progress_percent=0.",
                    }
                )
            normalized["progress_percent"] = 0.0
            return normalized, issues

        if status in {"in_progress", "partial"}:
            if existing is None:
                normalized["progress_percent"] = 50.0
                return normalized, issues
            clamped = max(1.0, min(99.0, float(existing)))
            if clamped != existing:
                issues.append(
                    {
                        "path": path,
                        "reason_code": "progress_status_mismatch",
                        "human_message": f"{status} tasks must have progress_percent in range 1..99.",
                    }
                )
            normalized["progress_percent"] = clamped
            return normalized, issues

        if status == "blocked":
            if existing is None:
                normalized["progress_percent"] = 0.0
                return normalized, issues
            clamped = max(0.0, min(100.0, float(existing)))
            if clamped != existing:
                issues.append(
                    {
                        "path": path,
                        "reason_code": "progress_out_of_range",
                        "human_message": "blocked tasks must have progress_percent in range 0..100.",
                    }
                )
            normalized["progress_percent"] = clamped
            return normalized, issues

        # Unknown status: normalize to bounded value but keep status untouched.
        normalized["progress_percent"] = max(0.0, min(100.0, float(existing if existing is not None else 0.0)))
        return normalized, issues

    def _compute_tasks_status_summary(
        self,
        *,
        tasks: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        critical_path_ids: list[str],
        priority_scale: list[str],
        risk_scale: list[str],
    ) -> dict[str, Any]:
        by_status = {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}
        by_priority: dict[str, int] = {token: 0 for token in priority_scale}
        by_risk: dict[str, int] = {token: 0 for token in risk_scale}
        for task in tasks:
            status = str(task.get("status") or "").strip()
            if status in by_status:
                by_status[status] = int(by_status[status]) + 1
            priority = str(task.get("priority") or "").strip()
            risk = str(task.get("risk") or "").strip()
            if priority:
                by_priority[priority] = int(by_priority.get(priority, 0)) + 1
            if risk:
                by_risk[risk] = int(by_risk.get(risk, 0)) + 1

        total = len(tasks)
        done = int(by_status["done"])
        progress = round((done / total) * 100, 1) if total else 0.0
        task_by_id = {str(item.get("id") or "").strip(): item for item in tasks}
        critical_done = sum(
            1
            for task_id in critical_path_ids
            if isinstance(task_by_id.get(task_id), dict) and str(task_by_id[task_id].get("status") or "").strip() == "done"
        )

        milestone_summary = {"total": len(milestones), "todo": 0, "in_progress": 0, "blocked": 0, "done": 0}
        for milestone in milestones:
            status = str(milestone.get("status") or "").strip()
            if status in milestone_summary:
                milestone_summary[status] = int(milestone_summary[status]) + 1

        return {
            "total": total,
            "by_status": by_status,
            "progress_percent_done": progress,
            "by_priority": by_priority,
            "by_risk": by_risk,
            "critical_path": {
                "total": len(critical_path_ids),
                "done": critical_done,
                "remaining": max(0, len(critical_path_ids) - critical_done),
            },
            "milestones": milestone_summary,
        }

    def _compute_tasks_type_summary(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        by_type: dict[str, dict[str, Any]] = {}
        for task in tasks:
            type_key = str(task.get("type") or "").strip() or "unspecified"
            status = str(task.get("status") or "").strip()
            progress = float(task.get("progress_percent") or 0.0)
            bucket = by_type.setdefault(
                type_key,
                {
                    "total": 0,
                    "by_status": {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0},
                    "done": 0,
                    "partial": 0,
                    "blocked": 0,
                    "_progress_sum": 0.0,
                },
            )
            bucket["total"] = int(bucket["total"]) + 1
            if status in bucket["by_status"]:
                bucket["by_status"][status] = int(bucket["by_status"][status]) + 1
            if status == "done":
                bucket["done"] = int(bucket["done"]) + 1
            if status == "partial":
                bucket["partial"] = int(bucket["partial"]) + 1
            if status == "blocked":
                bucket["blocked"] = int(bucket["blocked"]) + 1
            bucket["_progress_sum"] = float(bucket["_progress_sum"]) + progress

        finalized: dict[str, Any] = {}
        for key, value in by_type.items():
            total = int(value.get("total") or 0)
            finalized[key] = {
                "total": total,
                "by_status": dict(value.get("by_status") or {}),
                "done": int(value.get("done") or 0),
                "partial": int(value.get("partial") or 0),
                "blocked": int(value.get("blocked") or 0),
                "progress_percent": round((float(value.get("_progress_sum") or 0.0) / total), 1) if total else 0.0,
            }
        return {"total": len(tasks), "by_type": finalized}

    def _task_weight(self, task: dict[str, Any], *, critical_path_set: set[str]) -> float:
        weight = 1.0
        if str(task.get("priority") or "").strip() == "P1":
            weight += 0.5
        if str(task.get("risk") or "").strip() == "high":
            weight += 0.5
        task_id = str(task.get("id") or "").strip()
        if task_id and task_id in critical_path_set:
            weight += 0.5
        if str(task.get("type") or "").strip() in {"test", "e2e"}:
            weight += 0.25
        return weight

    def _compute_weighted_progress_summary(self, *, tasks: list[dict[str, Any]], critical_path_set: set[str]) -> dict[str, Any]:
        total_weight = 0.0
        done_weight = 0.0
        blocked_weight = 0.0
        for task in tasks:
            weight = self._task_weight(task, critical_path_set=critical_path_set)
            progress = max(0.0, min(100.0, float(task.get("progress_percent") or 0.0))) / 100.0
            total_weight += weight
            done_weight += weight * progress
            if str(task.get("status") or "").strip() == "blocked":
                blocked_weight += weight
        remaining_weight = max(0.0, total_weight - done_weight)
        weighted_percent = round((done_weight / total_weight) * 100, 1) if total_weight else 0.0
        return {
            "weighting_model_ref": self.weighting_model_ref,
            "total_weight": round(total_weight, 3),
            "done_weight": round(done_weight, 3),
            "remaining_weight": round(remaining_weight, 3),
            "blocked_weight": round(blocked_weight, 3),
            "weighted_percent": weighted_percent,
        }

    def _compute_milestone_progress_summary(
        self,
        *,
        tasks: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        critical_path_set: set[str],
    ) -> dict[str, Any]:
        task_by_id = {str(item.get("id") or "").strip(): item for item in tasks}
        summaries: dict[str, dict[str, Any]] = {}
        for milestone in milestones:
            milestone_id = str(milestone.get("id") or "").strip()
            if not milestone_id:
                continue
            milestone_tasks = [
                task_by_id[task_id]
                for task_id in [str(item).strip() for item in list(milestone.get("task_ids") or []) if str(item).strip()]
                if task_id in task_by_id
            ]
            if not milestone_tasks:
                summaries[milestone_id] = {
                    "total_tasks": 0,
                    "done": 0,
                    "blocked": 0,
                    "count_based_percent": 0.0,
                    "weighted_percent": 0.0,
                }
                continue
            done = sum(1 for task in milestone_tasks if str(task.get("status") or "").strip() == "done")
            blocked = sum(1 for task in milestone_tasks if str(task.get("status") or "").strip() == "blocked")
            count_based_percent = round((done / len(milestone_tasks)) * 100, 1)
            weighted = self._compute_weighted_progress_summary(tasks=milestone_tasks, critical_path_set=critical_path_set)
            summaries[milestone_id] = {
                "total_tasks": len(milestone_tasks),
                "done": done,
                "blocked": blocked,
                "count_based_percent": count_based_percent,
                "weighted_percent": float(weighted.get("weighted_percent") or 0.0),
            }
        return {"milestones": summaries}

    def _derive_milestones(
        self,
        *,
        tasks: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        critical_path_set: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        milestone_progress_summary = self._compute_milestone_progress_summary(
            tasks=tasks,
            milestones=milestones,
            critical_path_set=critical_path_set,
        )
        progress_by_milestone = dict(milestone_progress_summary.get("milestones") or {})
        task_by_id = {str(item.get("id") or "").strip(): item for item in tasks}
        normalized_milestones: list[dict[str, Any]] = []
        for milestone in milestones:
            normalized = dict(milestone or {})
            milestone_id = str(normalized.get("id") or "").strip()
            scoped_task_ids = [str(item).strip() for item in list(normalized.get("task_ids") or []) if str(item).strip()]
            scoped_tasks = [task_by_id[task_id] for task_id in scoped_task_ids if task_id in task_by_id]
            if not scoped_tasks:
                normalized["status"] = "todo"
                normalized_milestones.append(normalized)
                continue
            status_counts = {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}
            for task in scoped_tasks:
                status = str(task.get("status") or "").strip()
                if status in status_counts:
                    status_counts[status] = int(status_counts[status]) + 1
            total = len(scoped_tasks)
            if status_counts["done"] == total:
                derived_status = "done"
            elif status_counts["in_progress"] > 0 or status_counts["partial"] > 0 or status_counts["done"] > 0:
                derived_status = "in_progress"
            elif status_counts["blocked"] > 0 and status_counts["todo"] == 0:
                derived_status = "blocked"
            else:
                derived_status = "todo"
            normalized["status"] = derived_status
            if milestone_id and isinstance(progress_by_milestone.get(milestone_id), dict):
                progress_by_milestone[milestone_id]["status"] = derived_status
            normalized_milestones.append(normalized)
        return normalized_milestones, {"milestones": progress_by_milestone}

    def _compute_progress_summary(self, *, tasks_status_summary: dict[str, Any], weighted_progress_summary: dict[str, Any]) -> dict[str, Any]:
        by_status = dict(tasks_status_summary.get("by_status") or {})
        total = int(tasks_status_summary.get("total") or 0)
        done = int(by_status.get("done") or 0)
        blocked = int(by_status.get("blocked") or 0)
        in_progress = int(by_status.get("in_progress") or 0)
        partial = int(by_status.get("partial") or 0)
        todo = int(by_status.get("todo") or 0)

        if total > 0 and done == total:
            state = "done"
        elif in_progress > 0 or partial > 0:
            state = "in_progress"
        elif blocked > 0 and done == 0 and in_progress == 0 and partial == 0 and todo == 0:
            state = "blocked"
        else:
            state = "todo"
        return {
            "state": state,
            "todo_remaining": todo,
            "in_progress": in_progress,
            "partial": partial,
            "blocked": blocked,
            "done": done,
            "count_based_percent": round((done / total) * 100, 1) if total else 0.0,
            "weighted_percent": float(weighted_progress_summary.get("weighted_percent") or 0.0),
        }

    def _compute_derived_summary_metadata(
        self,
        *,
        tasks: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        critical_path_ids: list[str],
    ) -> dict[str, Any]:
        source_hash = _stable_hash(
            {
                "tasks": tasks,
                "milestones": milestones,
                "critical_path_tasks": critical_path_ids,
            }
        )
        return {
            "mode": "derived_cache",
            "source_of_truth": "tasks",
            "generated_at": _now_iso(),
            "generator": self.generator,
            "generator_version": self.generator_version,
            "source_hash": source_hash,
            "is_stale": False,
        }
