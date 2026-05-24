from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.planning_contract import PlanningContract


_STABLE_ERROR_CODES = {
    "no_tasks",
    "too_few_tasks",
    "missing_required_task_kind",
    "missing_required_artifact",
    "invalid_task_payload",
}


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    error_codes: tuple[str, ...]
    warnings: tuple[str, ...]
    missing_task_kinds: tuple[str, ...]
    contract_id: str
    task_count: int
    human_summary: str


class PlanningValidationService:
    @staticmethod
    def _normalize_task_kind(task: dict[str, Any]) -> str:
        raw = str(task.get("task_kind") or task.get("task_type") or "").strip().lower()
        if raw in {"implementation", "code", "develop"}:
            return "coding"
        if raw in {"tests", "test"}:
            return "testing"
        if raw in {"documentation", "doc"}:
            return "review"
        return raw

    @staticmethod
    def _validate_task_payload(task: dict[str, Any]) -> bool:
        title = str(task.get("title") or "").strip()
        description = str(task.get("description") or "").strip()
        return bool(title or description)

    def validate_subtasks(self, *, subtasks: list[dict[str, Any]], contract: PlanningContract) -> PlanValidationResult:
        tasks = [dict(item) for item in list(subtasks or []) if isinstance(item, dict)]
        count = len(tasks)
        error_codes: list[str] = []
        missing_task_kinds: list[str] = []

        if count == 0:
            error_codes.append("no_tasks")
        if count < int(contract.min_tasks):
            error_codes.append("too_few_tasks")

        invalid_payload_found = False
        observed_kinds: set[str] = set()
        for task in tasks:
            if not self._validate_task_payload(task):
                invalid_payload_found = True
            kind = self._normalize_task_kind(task)
            if kind:
                observed_kinds.add(kind)

        if invalid_payload_found:
            error_codes.append("invalid_task_payload")

        for required_kind in contract.required_task_kinds:
            if required_kind not in observed_kinds:
                missing_task_kinds.append(required_kind)
        if missing_task_kinds:
            error_codes.append("missing_required_task_kind")

        if contract.required_artifacts:
            error_codes.append("missing_required_artifact")

        stable = [code for code in error_codes if code in _STABLE_ERROR_CODES]
        unique_codes = tuple(dict.fromkeys(stable))
        ok = not unique_codes
        if ok:
            summary = f"Plan valid by contract {contract.contract_id}"
        else:
            summary = f"Plan failed contract {contract.contract_id}: {', '.join(unique_codes)}"

        return PlanValidationResult(
            ok=ok,
            error_codes=unique_codes,
            warnings=(),
            missing_task_kinds=tuple(missing_task_kinds),
            contract_id=contract.contract_id,
            task_count=count,
            human_summary=summary,
        )


_SERVICE = PlanningValidationService()


def get_planning_validation_service() -> PlanningValidationService:
    return _SERVICE
