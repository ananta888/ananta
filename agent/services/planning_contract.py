from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanningContract:
    contract_id: str
    required_task_kinds: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    required_commands: tuple[str, ...]
    required_verification_steps: tuple[str, ...]
    min_tasks: int


_DEFAULT_SOFTWARE_CONTRACT = PlanningContract(
    contract_id="software-default-v1",
    required_task_kinds=("analysis", "coding", "testing", "review"),
    required_artifacts=(),
    required_commands=(),
    required_verification_steps=("tests",),
    min_tasks=4,
)

_GENERIC_CONTRACT = PlanningContract(
    contract_id="generic-default-v1",
    required_task_kinds=("coding",),
    required_artifacts=(),
    required_commands=(),
    required_verification_steps=(),
    min_tasks=1,
)


def _normalize_required_task_kinds(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return fallback
    normalized: list[str] = []
    for entry in value:
        token = str(entry or "").strip().lower()
        if token and token not in normalized:
            normalized.append(token)
    return tuple(normalized) if normalized else fallback


def resolve_planning_contract(*, mode: str, planning_policy: dict[str, Any] | None) -> PlanningContract:
    policy = dict(planning_policy or {})
    contracts = policy.get("contracts") if isinstance(policy.get("contracts"), dict) else {}
    mode_key = str(mode or "generic").strip().lower()
    contract_cfg = contracts.get(mode_key) if isinstance(contracts.get(mode_key), dict) else {}

    base = _DEFAULT_SOFTWARE_CONTRACT if mode_key == "new_software_project" else _GENERIC_CONTRACT
    required_task_kinds = _normalize_required_task_kinds(contract_cfg.get("required_task_kinds"), base.required_task_kinds)
    required_artifacts = tuple(str(x).strip() for x in list(contract_cfg.get("required_artifacts") or base.required_artifacts) if str(x).strip())
    required_commands = tuple(str(x).strip() for x in list(contract_cfg.get("required_commands") or base.required_commands) if str(x).strip())
    required_verification_steps = tuple(
        str(x).strip() for x in list(contract_cfg.get("required_verification_steps") or base.required_verification_steps) if str(x).strip()
    )
    min_tasks = max(1, int(contract_cfg.get("min_tasks") or base.min_tasks))
    contract_id = str(contract_cfg.get("contract_id") or base.contract_id).strip() or base.contract_id

    return PlanningContract(
        contract_id=contract_id,
        required_task_kinds=required_task_kinds,
        required_artifacts=required_artifacts,
        required_commands=required_commands,
        required_verification_steps=required_verification_steps,
        min_tasks=min_tasks,
    )
