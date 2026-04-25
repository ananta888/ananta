from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "data" / "state_ownership_matrix.json"
CORE_STATE_TYPES = {
    "goal",
    "plan",
    "task",
    "execution",
    "approval",
    "artifact",
    "audit",
    "verification",
    "repair",
    "client_ui_state",
}


def _load_payload() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _find_state(payload: dict[str, Any], state_type: str) -> dict[str, Any]:
    for state in payload.get("states", []):
        if state.get("state_type") == state_type:
            return state
    raise AssertionError(f"state_type not found: {state_type}")


def _validate_state_ownership(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    states = list(payload.get("states") or [])

    if not states:
        return ["states must contain at least one entry"]

    seen_state_types: set[str] = set()
    for index, state in enumerate(states):
        state_type = state.get("state_type")
        if not isinstance(state_type, str) or not state_type:
            problems.append(f"states[{index}].state_type must be a non-empty string")
            continue
        if state_type in seen_state_types:
            problems.append(f"duplicate state_type entry: {state_type}")
        seen_state_types.add(state_type)

        owner = state.get("owner")
        if isinstance(owner, list):
            owner_values = [value for value in owner if isinstance(value, str) and value]
            if len(owner_values) != 1:
                problems.append(f"{state_type} must declare exactly one owner")
                owner = None
            else:
                owner = owner_values[0]
        if not isinstance(owner, str) or not owner:
            problems.append(f"{state_type} owner must be a non-empty string")

        mutable = bool(state.get("mutable", True))
        writers = state.get("allowed_writers")
        if not isinstance(writers, list):
            problems.append(f"{state_type} allowed_writers must be an array")
            writers = []
        if mutable and len(writers) == 0:
            problems.append(f"{state_type} mutable state requires non-empty allowed_writers")
        if len(set(writers)) != len(writers):
            problems.append(f"{state_type} allowed_writers must not contain duplicates")

        server_owned = bool(state.get("server_owned", True))
        if server_owned and owner == "client":
            problems.append(f"{state_type} is server-owned but has client owner")
        if server_owned and "client" in writers:
            problems.append(f"{state_type} is server-owned but allows client writer")

        if state_type == "audit":
            if not bool(state.get("append_only", False)):
                problems.append("audit must be append_only")
            if mutable:
                problems.append("audit must not be mutable")

    unknown_state_types = sorted(seen_state_types - CORE_STATE_TYPES)
    if unknown_state_types:
        problems.append(f"unknown state types present: {unknown_state_types}")

    missing_state_types = sorted(CORE_STATE_TYPES - seen_state_types)
    if missing_state_types:
        problems.append(f"missing core state types: {missing_state_types}")

    return problems


def test_state_ownership_matrix_is_valid() -> None:
    payload = _load_payload()
    assert _validate_state_ownership(payload) == []


def test_state_ownership_matrix_rejects_unknown_state_type() -> None:
    payload = deepcopy(_load_payload())
    payload["states"].append(
        {
            "state_type": "shadow_state",
            "owner": "hub",
            "server_owned": True,
            "mutable": True,
            "allowed_writers": ["hub"],
        }
    )

    problems = _validate_state_ownership(payload)
    assert any(problem.startswith("unknown state types present:") for problem in problems)


def test_state_ownership_matrix_rejects_duplicate_owner_declaration() -> None:
    payload = deepcopy(_load_payload())
    _find_state(payload, "goal")["owner"] = ["hub", "hub"]

    problems = _validate_state_ownership(payload)
    assert "goal must declare exactly one owner" in problems


def test_state_ownership_matrix_rejects_client_owned_server_state() -> None:
    payload = deepcopy(_load_payload())
    _find_state(payload, "plan")["owner"] = "client"

    problems = _validate_state_ownership(payload)
    assert "plan is server-owned but has client owner" in problems


def test_state_ownership_matrix_requires_writers_for_mutable_state() -> None:
    payload = deepcopy(_load_payload())
    task_state = _find_state(payload, "task")
    task_state["mutable"] = True
    task_state["allowed_writers"] = []

    problems = _validate_state_ownership(payload)
    assert "task mutable state requires non-empty allowed_writers" in problems
