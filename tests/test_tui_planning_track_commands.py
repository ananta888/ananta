from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from agent.services.planning_track_pipeline_service import compute_tasks_status_summary
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def test_plan_track_runs_with_from_goal_and_renders_status(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    result = execute_command(":plan track --from-goal goal-plan-1", _state())
    assert result.handled is True
    payload = json.loads(result.message)
    assert payload["planning_track_mode"] is True
    assert payload["goal_id"] == "goal-plan-1"
    assert payload["planning_status"] == "valid"
    assert payload["planning_lifecycle"][:2] == ["pending", "validating"]
    assert payload["track_rows"]
    assert payload["selected_track"]["milestones"]
    assert payload["selected_track"]["tasks_filtered"]


def test_plan_track_records_reason_codes_for_invalid_output(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    state = _state().with_updates(header_logo_game={"active_goal_id": "goal-plan-2", "planner_mock_output": "just prose"})
    result = execute_command(":plan track", state)
    assert result.handled is True
    payload = json.loads(result.message)
    assert payload["planning_status"] in {"failed", "degraded"}
    issues = list(payload.get("status_issues") or [])
    assert any(str(item.get("reason_code") or "") == "non_json_output" for item in issues if isinstance(item, dict))
    assert "non_json_output" in result.state.status_message


def test_plan_track_adopt_and_reject_flow(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    created = execute_command(":plan track --from-goal goal-plan-3", _state())
    payload = json.loads(created.message)
    output_id = str(payload["selected_output_id"])

    adopted = execute_command(f":plan track adopt {output_id}", created.state)
    assert adopted.handled is True
    adopted_payload = json.loads(adopted.message)
    assert adopted_payload["active_output_id"] == output_id
    assert adopted_payload["task_mapping"]

    rejected = execute_command(f":plan track reject {output_id}", adopted.state)
    assert rejected.handled is True
    rejected_payload = json.loads(rejected.message)
    assert output_id in list(rejected_payload.get("rejected_output_ids") or [])
    assert rejected_payload["active_output_id"] == ""


def test_plan_track_execute_next_starts_internal_task(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings
    from agent.repository import task_repo

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    created = execute_command(":plan track --from-goal goal-plan-6", _state())
    payload = json.loads(created.message)
    output_id = str(payload["selected_output_id"])
    adopted = execute_command(f":plan track adopt {output_id}", created.state)
    assert adopted.handled is True
    executed = execute_command(":plan track execute-next", adopted.state)
    assert executed.handled is True
    executed_payload = json.loads(executed.message)
    mapping = dict(executed_payload.get("task_mapping") or {})
    assert mapping
    first_internal_id = next(iter(mapping.values()))
    task = task_repo.get_by_id(first_internal_id)
    assert task is not None
    assert str(task.status) == "in_progress"


def test_plan_track_blocks_adopt_for_invalid_candidate(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    state = _state().with_updates(header_logo_game={"active_goal_id": "goal-plan-4", "planner_mock_output": "prose invalid"})
    created = execute_command(":plan track", state)
    payload = json.loads(created.message)
    output_id = str(payload["selected_output_id"])
    adopted = execute_command(f":plan track adopt {output_id}", created.state)
    assert adopted.handled is False
    assert "blocked" in adopted.message


def test_plan_track_diff_shows_new_changed_removed_tasks(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    first = execute_command(":plan track --from-goal goal-plan-5", _state())
    first_payload = json.loads(first.message)
    first_output_id = str(first_payload["selected_output_id"])

    second_track = deepcopy(first_payload["selected_track"])
    second_track.pop("tasks_filtered", None)
    second_track["tasks"][0]["title"] = "Analyse Ziel und Randbedingungen"
    second_track["tasks"].append(
        {
            "id": "T99",
            "title": "Neue Task",
            "status": "todo",
            "priority": "P2",
            "risk": "low",
            "type": "docs",
            "acceptance_criteria": ["Neue Task ist im Plan enthalten."],
        }
    )
    second_track["tasks_status_summary"] = compute_tasks_status_summary(second_track)
    with_mock = first.state.with_updates(
        header_logo_game={**dict(first.state.header_logo_game or {}), "planner_mock_output": json.dumps(second_track)}
    )
    second = execute_command(":plan track", with_mock)
    second_payload = json.loads(second.message)
    second_output_id = str(second_payload["selected_output_id"])

    diffed = execute_command(f":plan track diff {first_output_id} {second_output_id}", second.state)
    assert diffed.handled is True
    diff_payload = json.loads(diffed.message)
    plan_diff = dict(diff_payload.get("plan_diff") or {})
    assert plan_diff["left_output_id"] == first_output_id
    assert plan_diff["right_output_id"] == second_output_id
    assert len(list(plan_diff.get("new_tasks") or [])) == 1
    assert len(list(plan_diff.get("changed_tasks") or [])) >= 1
