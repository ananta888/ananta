"""T06.04: Deterministic E2E flow for planning track generation and execution handoff."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.config import settings
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from scripts.e2e.e2e_artifacts import build_report, compact_summary, make_flow_entry, new_run_id, write_report, write_text_artifact


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def run_flow(*, data_root: Path) -> dict[str, object]:
    settings.data_dir = str(data_root)
    goal_id = "goal-planning-track-e2e"
    service = GoalArtifactService()

    state = execute_command(f":goal use {goal_id}", _state()).state
    created = execute_command(":plan track", state)
    if not created.handled:
        raise RuntimeError("plan_track_run_failed")
    created_payload = json.loads(created.message)
    output_id = str(created_payload.get("selected_output_id") or "")
    if not output_id:
        raise RuntimeError("planning_track_output_missing")
    if str(created_payload.get("planning_status") or "") != "valid":
        raise RuntimeError("planning_track_not_valid")

    adopted = execute_command(f":plan track adopt {output_id}", created.state)
    if not adopted.handled:
        raise RuntimeError("plan_track_adopt_failed")
    adopted_payload = json.loads(adopted.message)
    if str(adopted_payload.get("active_output_id") or "") != output_id:
        raise RuntimeError("active_output_not_set")

    executed = execute_command(":plan track execute-next", adopted.state)
    if not executed.handled:
        raise RuntimeError("plan_track_execute_next_failed")
    executed_payload = json.loads(executed.message)
    mapping = dict(executed_payload.get("task_mapping") or {})
    if not mapping:
        raise RuntimeError("plan_task_mapping_missing")

    graph = service.get_goal_graph(goal_id)
    output_row = next(
        (
            dict(item)
            for item in list(graph.get("output_artifacts") or [])
            if isinstance(item, dict) and str(item.get("output_artifact_id") or "") == output_id
        ),
        {},
    )
    if str(output_row.get("artifact_type") or "") != "planning_track":
        raise RuntimeError("planning_track_artifact_missing")
    provenance_id = str(output_row.get("provenance_id") or "")
    if not provenance_id:
        raise RuntimeError("planning_track_provenance_missing")

    return {
        "goal_id": goal_id,
        "output_artifact_id": output_id,
        "active_output_id": adopted_payload.get("active_output_id"),
        "planning_status": created_payload.get("planning_status"),
        "provenance_id": provenance_id,
        "materialized_mapping_size": len(mapping),
        "selected_task_count": len(list(dict(executed_payload.get("selected_track") or {}).get("tasks") or [])),
    }


def main() -> int:
    run_id = new_run_id("planning-track-goal-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "planning-track-goal", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="planning-track-goal", status="passed", blocking=True, logs=[flow_log])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
