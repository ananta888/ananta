"""T06.04: Deterministischer E2E-Flow für Goal Artifact Management."""
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
from client_surfaces.operator_tui.models import OperatorState, PanelState
from client_surfaces.operator_tui.renderer import render_operator_shell
from scripts.e2e.e2e_artifacts import (
    build_report,
    compact_summary,
    make_flow_entry,
    new_run_id,
    write_report,
    write_text_artifact,
)

ASSETS = _REPO / "assets"
CAST_FILE = ASSETS / "operator_tui_goal_artifacts.cast"


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def _frame(state: OperatorState, ts: float) -> list[object]:
    return [float(ts), "o", "\x1b[2J\x1b[H" + render_operator_shell(state, width=120, height=34)]


def run_flow(*, data_root: Path) -> dict[str, object]:
    settings.data_dir = str(data_root)
    service = GoalArtifactService()
    goal_id = "goal-e2e-flow"

    state = execute_command(f":goal use {goal_id}", _state()).state
    candidates = execute_command(":goal sources candidates", state)
    if not candidates.handled:
        raise RuntimeError("candidates_failed")
    candidate_payload = json.loads(candidates.message)

    granted = execute_command(":goal source grant sources:keycloak:snap_1 --usage use_as_context", state)
    if not granted.handled:
        raise RuntimeError("grant_failed")
    grant = json.loads(granted.message)
    grant_id = str(grant["grant_id"])

    tracking = service.validate_and_record_context_usages(
        goal_id=goal_id,
        artifact_refs=["sources:keycloak:snap_1"],
        task_id="task-e2e-1",
        worker_id="worker-e2e-1",
        context_hash="ctx-e2e-flow-1",
    )
    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-e2e-1",
            "goal_id": goal_id,
            "task_id": "task-e2e-1",
            "worker_id": "worker-e2e-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": tracking["source_usage_refs"],
            "artifact_ref": "artifacts:report:e2e-1",
            "content_hash": "d" * 64,
            "status": "created",
            "provenance_summary": "e2e flow output",
        },
    )

    artifacts_view = execute_command(":goal artifacts", granted.state)
    provenance = execute_command(":artifact provenance out-e2e-1", artifacts_view.state)
    if not provenance.handled:
        raise RuntimeError("provenance_failed")

    final_state = artifacts_view.state.with_updates(
        panel_states={"artifacts": PanelState.HEALTHY},
        section_payloads={"artifacts": json.loads(artifacts_view.message)},
    )
    rendered = render_operator_shell(final_state, width=120, height=34)
    if "Goal Artifacts:" not in rendered:
        raise RuntimeError("missing_goal_artifacts_view")

    ASSETS.mkdir(parents=True, exist_ok=True)
    cast = {
        "version": 2,
        "width": 120,
        "height": 34,
        "title": "Ananta Goal Artifact Management E2E",
        "env": {"TERM": "xterm-256color"},
    }
    events = [
        _frame(state, 0.0),
        _frame(granted.state, 1.2),
        _frame(artifacts_view.state, 2.6),
    ]
    lines = [json.dumps(cast, ensure_ascii=False)]
    lines.extend(json.dumps(event, ensure_ascii=False) for event in events)
    CAST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "goal_id": goal_id,
        "grant_id": grant_id,
        "candidates_count": len(candidate_payload.get("candidates") or []),
        "usage_refs": list(tracking.get("source_usage_refs") or []),
        "cast_file": str(CAST_FILE.relative_to(_REPO)),
        "provenance": json.loads(provenance.message),
    }


def main() -> int:
    run_id = new_run_id("goal-artifact-management-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "goal-artifact-management", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="goal-artifact-management", status="passed", blocking=True, logs=[flow_log], artifact_refs=[result["cast_file"]])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    print(f"cast={result['cast_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
