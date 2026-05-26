"""T04.03: Deterministischer E2E-Flow für Goal Artifact Execution Provenance."""
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
    service = GoalArtifactService()
    goal_id = "goal-e2e-provenance"
    output_id = "out-e2e-prov-1"
    provenance_id = "prov-e2e-1"

    state = execute_command(f":goal use {goal_id}", _state()).state
    service.upsert_execution_provenance(
        goal_id=goal_id,
        provenance={
            "schema": "execution_provenance.v1",
            "provenance_id": provenance_id,
            "goal_id": goal_id,
            "task_id": "task-e2e-prov-1",
            "execution_id": "exec-e2e-prov-1",
            "worker_id": "worker-e2e-prov-1",
            "worker_kind": "native",
            "runtime_target_ref": {"runtime_type": "python", "location": "local"},
            "model_ref": {"provider_id": "local", "model_id": "gpt-5.3-codex"},
            "config_refs": {
                "worker_config_ref": "cfg-worker-e2e",
                "runtime_config_ref": "cfg-runtime-e2e",
                "model_config_ref": "cfg-model-e2e",
                "policy_config_ref": "cfg-policy-e2e",
            },
            "prompt_refs": {
                "prompt_template_ref": "prompt:e2e/default",
                "prompt_template_version": "v1",
                "prompt_template_hash": "a" * 64,
                "prompt_variables_hash": "b" * 64,
                "final_prompt_hash": "c" * 64,
                "raw_prompt_stored": False,
                "reason_code": "raw_prompt_policy_default",
            },
            "input_usage_refs": [],
            "output_artifact_refs": [output_id],
            "created_at": "2026-05-26T00:00:00Z",
        },
    )
    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": output_id,
            "goal_id": goal_id,
            "task_id": "task-e2e-prov-1",
            "worker_id": "worker-e2e-prov-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:01:00Z",
            "input_usage_refs": [],
            "artifact_ref": "artifacts:report:e2e-provenance",
            "content_hash": "d" * 64,
            "status": "created",
            "provenance_summary": "provenance e2e",
            "provenance_id": provenance_id,
            "execution_id": "exec-e2e-prov-1",
        },
    )

    provenance_cmd = execute_command(f":artifact provenance {output_id}", state)
    prompt_cmd = execute_command(f":artifact prompt {output_id}", state)
    config_cmd = execute_command(f":artifact config {output_id}", state)
    if not provenance_cmd.handled or not prompt_cmd.handled or not config_cmd.handled:
        raise RuntimeError("command_execution_failed")

    provenance = json.loads(provenance_cmd.message)
    prompt = json.loads(prompt_cmd.message)
    config = json.loads(config_cmd.message)
    assert provenance["task_id"] == "task-e2e-prov-1"
    assert provenance["worker_id"] == "worker-e2e-prov-1"
    assert provenance["model_ref"]["model_id"] == "gpt-5.3-codex"
    assert prompt["prompt_template_ref"] == "prompt:e2e/default"
    assert prompt["final_prompt_hash"] == "c" * 64
    assert prompt["raw_prompt_status"] == "raw prompt not stored"
    assert config["runtime_config_ref"] == "cfg-runtime-e2e"
    assert config["model_config_ref"] == "cfg-model-e2e"

    return {
        "goal_id": goal_id,
        "output_artifact_id": output_id,
        "provenance_id": provenance_id,
        "provenance": provenance,
        "prompt": prompt,
        "config": config,
    }


def main() -> int:
    run_id = new_run_id("goal-artifact-execution-provenance-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "goal-artifact-execution-provenance", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="goal-artifact-execution-provenance", status="passed", blocking=True, logs=[flow_log])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
