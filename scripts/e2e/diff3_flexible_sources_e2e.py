"""T06.05: Deterministischer E2E-Flow für flexible Diff3-Quellen."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.config import settings
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from scripts.e2e.e2e_artifacts import build_report, compact_summary, make_flow_entry, new_run_id, write_report, write_text_artifact


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.local"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "e2e"], cwd=str(repo), check=True, capture_output=True)
    (repo / "demo.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "demo.txt").write_text("base changed\n", encoding="utf-8")


def run_flow(*, data_root: Path) -> dict[str, object]:
    settings.data_dir = str(data_root / "data")
    workspace = data_root / "workspace"
    _init_repo(workspace)
    goal_id = "goal-diff3-flex"
    output_file = workspace / "output.txt"
    output_file.write_text("artifact output v1\n", encoding="utf-8")
    service = GoalArtifactService()
    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-flex-1",
            "goal_id": goal_id,
            "task_id": "task-flex-1",
            "worker_id": "worker-flex-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": [],
            "artifact_ref": f"file:{output_file}",
            "content_hash": "a" * 64,
            "status": "created",
            "provenance_summary": "flex source",
        },
    )

    old_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        state = execute_command(f":goal use {goal_id}", _state()).state
        state = execute_command(":diff3", state).state
        state = execute_command(":diff3 panel A current", state).state
        state = execute_command(":diff3 panel B output out-flex-1", state).state
        state = execute_command(":diff3 panel C ai explain", state).state
        ai_run = execute_command(":diff3 ai run explain", state)
        if not ai_run.handled:
            raise RuntimeError("diff3_ai_run_failed")
        result = json.loads(ai_run.message)
        payload = ai_run.state.section_payloads["artifacts"]
        rendered = render_operator_shell(ai_run.state, width=176, height=34)
        if "Current Diff" not in rendered or "Output out-flex-1" not in rendered:
            raise RuntimeError("panel_headers_missing")
        envelope = dict(result.get("context_envelope") or {})
        return {
            "goal_id": goal_id,
            "status": result.get("status"),
            "output_artifact_id": result.get("output_artifact_id"),
            "panel_summaries": payload.get("panel_summaries"),
            "denied_context_refs": envelope.get("denied_context_refs"),
            "source_usage_refs": result.get("source_usage_refs"),
        }
    finally:
        os.chdir(old_cwd)


def main() -> int:
    run_id = new_run_id("diff3-flexible-sources-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "diff3-flexible-sources", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="diff3-flexible-sources", status="passed", blocking=True, logs=[flow_log])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

