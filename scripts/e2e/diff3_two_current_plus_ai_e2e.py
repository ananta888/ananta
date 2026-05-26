"""T06.04: Deterministischer E2E-Flow für zwei Current-Diff Panels plus AI-Panel."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.config import settings
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from scripts.e2e.e2e_artifacts import build_report, compact_summary, make_flow_entry, new_run_id, write_report, write_text_artifact


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "e2e@example.local"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "e2e"], cwd=str(repo), check=True, capture_output=True)
    (repo / "demo.txt").write_text("line-1\nline-2\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "demo.txt").write_text("line-1\nline-2 changed\n", encoding="utf-8")


def run_flow(*, data_root: Path) -> dict[str, object]:
    settings.data_dir = str(data_root / "data")
    workspace = data_root / "workspace"
    _init_repo(workspace)
    goal_id = "goal-diff3-e2e"

    old_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        state = execute_command(f":goal use {goal_id}", _state()).state
        state = execute_command(":diff3", state).state
        state = execute_command(":diff3 panel A current", state).state
        state = execute_command(":diff3 panel B current --mode summary", state).state
        state = execute_command(":diff3 panel C ai review", state).state
        ai_run = execute_command(":diff3 ai run review", state)
        if not ai_run.handled:
            raise RuntimeError("diff3_ai_run_failed")
        payload = json.loads(ai_run.message)
        if str(payload.get("status") or "") not in {"success", "degraded"}:
            raise RuntimeError("diff3_ai_run_invalid_status")
        output_artifact_id = str(payload.get("output_artifact_id") or "")
        provenance_payload = {}
        if output_artifact_id:
            provenance = execute_command(f":artifact provenance {output_artifact_id}", ai_run.state)
            if provenance.handled:
                provenance_payload = json.loads(provenance.message)
        return {
            "goal_id": goal_id,
            "status": payload.get("status"),
            "summary": dict(payload.get("response") or {}).get("summary"),
            "output_artifact_id": output_artifact_id,
            "provenance_id": payload.get("provenance_id"),
            "context_envelope": payload.get("context_envelope"),
            "provenance": provenance_payload,
        }
    finally:
        os.chdir(old_cwd)


def main() -> int:
    run_id = new_run_id("diff3-two-current-plus-ai-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "diff3-two-current-plus-ai", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="diff3-two-current-plus-ai", status="passed", blocking=True, logs=[flow_log])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

