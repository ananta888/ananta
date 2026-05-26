"""T06.05: Negativer E2E-Flow für Security Boundary bei Goal-Artefakten."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.config import settings
from scripts.e2e.e2e_artifacts import build_report, compact_summary, make_flow_entry, new_run_id, write_report, write_text_artifact


def run_flow(*, data_root: Path) -> dict[str, object]:
    settings.data_dir = str(data_root)
    service = GoalArtifactService()
    goal_id = "goal-e2e-security"

    service.create_grant(
        goal_id=goal_id,
        grant={
            "schema": "source_artifact_grant.v1",
            "grant_id": "grant-sec-1",
            "goal_id": goal_id,
            "artifact_ref": "sources:keycloak:snap_1",
            "granted_by": "operator",
            "granted_at": "2026-05-26T00:00:00Z",
            "allowed_usages": ["read", "use_as_context"],
            "data_boundary": "project_private",
            "sensitivity": "internal",
            "policy_decision_ref": "policy-sec-1",
        },
    )

    tracking = service.validate_and_record_context_usages(
        goal_id=goal_id,
        artifact_refs=["sources:keycloak:snap_1", "sources:wikipedia:snap_blocked"],
        task_id="task-sec-1",
        worker_id="worker-sec-1",
        context_hash="ctx-sec-1",
    )
    denied = list(tracking.get("denied_context_refs") or [])
    if denied != ["sources:wikipedia:snap_blocked"]:
        raise RuntimeError("expected_blocked_source_denied")

    service.record_output_artifact(
        goal_id=goal_id,
        output_artifact={
            "schema": "goal_output_artifact.v1",
            "output_artifact_id": "out-sec-1",
            "goal_id": goal_id,
            "task_id": "task-sec-1",
            "worker_id": "worker-sec-1",
            "artifact_type": "report",
            "created_at": "2026-05-26T00:00:00Z",
            "input_usage_refs": list(tracking.get("source_usage_refs") or []),
            "artifact_ref": "artifacts:report:sec-1",
            "content_hash": "c" * 64,
            "status": "created",
            "provenance_summary": "security boundary flow",
        },
    )
    graph = service.get_goal_graph(goal_id)
    output = next(item for item in list(graph.get("output_artifacts") or []) if item.get("output_artifact_id") == "out-sec-1")
    if "sources:wikipedia:snap_blocked" in json.dumps(output):
        raise RuntimeError("blocked_source_leaked_into_output")

    return {
        "goal_id": goal_id,
        "denied_context_refs": denied,
        "source_usage_refs": list(tracking.get("source_usage_refs") or []),
        "output_input_usage_refs": list(output.get("input_usage_refs") or []),
    }


def main() -> int:
    run_id = new_run_id("goal-artifact-security-boundary-e2e")
    data_root = _REPO / "data" / run_id
    data_root.mkdir(parents=True, exist_ok=True)
    result = run_flow(data_root=data_root)
    flow_log = write_text_artifact(run_id, "goal-artifact-security-boundary", "flow.log", json.dumps(result, indent=2))
    report = build_report(
        run_id,
        [make_flow_entry(flow_id="goal-artifact-security-boundary", status="passed", blocking=True, logs=[flow_log])],
    )
    report_ref = write_report(run_id, report)
    print(compact_summary(report))
    print(f"report={report_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
