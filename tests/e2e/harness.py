from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.e2e.e2e_artifacts import (
    build_report,
    compact_summary,
    make_flow_entry,
    write_report,
    write_text_artifact,
)
from tests.e2e.mock_llm import MockLLM
from tests.e2e.mock_worker import MockWorker


@dataclass(frozen=True)
class FlowRunResult:
    run_id: str
    flow_id: str
    report_path: str
    report: dict[str, Any]
    flow_entry: dict[str, Any]
    snapshot_refs: dict[str, str] = field(default_factory=dict)
    goal_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None

    @property
    def summary_line(self) -> str:
        return compact_summary(self.report)


class E2EHarness:
    """Small in-process harness for deterministic E2E dogfood flows."""

    def __init__(
        self,
        *,
        artifact_root: Path | None = None,
        llm: MockLLM | None = None,
        worker: MockWorker | None = None,
    ) -> None:
        self.artifact_root = artifact_root
        self.llm = llm or MockLLM()
        self.worker = worker or MockWorker()

    def normalize_cli_snapshot(self, content: str) -> str:
        normalized = str(content)
        replacements = [
            (r"run_id:\s+\S+", "run_id: <RUN_ID>"),
            (r"goal_id:\s+\S+", "goal_id: <GOAL_ID>"),
            (r"task_id:\s+\S+", "task_id: <TASK_ID>"),
            (r"trace_id:\s+\S+", "trace_id: <TRACE_ID>"),
            (r"artifact:\s+\S+", "artifact: <ARTIFACT_REF>"),
        ]
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)
        compact_lines = [line.rstrip() for line in normalized.splitlines()]
        return "\n".join(compact_lines).strip() + "\n"

    def render_cli_health(self, *, run_id: str) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: health",
                "status: ok",
                "mode: mocked-e2e",
                f"run_id: {run_id}",
                "next step: python -m agent.cli_goals --list-modes",
                "",
            ]
        )

    def render_cli_goal_submit(self, *, goal_id: str, task_id: str, trace_id: str) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: goal submit",
                "status: success",
                f"goal_id: {goal_id}",
                f"task_id: {task_id}",
                f"trace_id: {trace_id}",
                "next step: python -m agent.cli_goals --goal-detail goal-id",
                "",
            ]
        )

    def render_cli_task_status(self, *, task_id: str) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: task status",
                "status: completed",
                f"task_id: {task_id}",
                "artifact_ready: yes",
                "next step: python -m agent.cli_goals --task-detail task-id",
                "",
            ]
        )

    def render_cli_artifact_show(self, *, artifact_ref: str) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: artifact show",
                "status: success",
                f"artifact: {artifact_ref}",
                "render: text",
                "next step: inspect artifact or open report.json",
                "",
            ]
        )

    def render_cli_backend_unreachable(self) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: health",
                "status: degraded",
                "reason: backend unreachable",
                "retryable: yes",
                "next step: verify backend URL and service health",
                "",
            ]
        )

    def render_cli_invalid_auth(self) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: goal submit",
                "status: denied",
                "reason: invalid auth or config",
                "retryable: yes",
                "next step: refresh token and re-run command",
                "",
            ]
        )

    def render_cli_approval_required(self) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: task execute",
                "status: approval_required",
                "policy: human_confirmation_gate",
                "executed: no",
                "next step: request approval then retry",
                "",
            ]
        )

    def render_cli_policy_denied(self) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: task execute",
                "status: denied",
                "policy: unsafe_action_block",
                "executed: no",
                "next step: revise request to a safe bounded action",
                "",
            ]
        )

    def _persist_flow(
        self,
        *,
        run_id: str,
        flow_id: str,
        status: str,
        blocking: bool,
        logs: list[str],
        snapshots: list[str],
        trace_bundle_refs: list[str],
        artifact_refs: list[str],
        notes: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        flow_entry = make_flow_entry(
            flow_id=flow_id,
            status=status,
            blocking=blocking,
            logs=logs,
            snapshots=snapshots,
            trace_bundle_refs=trace_bundle_refs,
            artifact_refs=artifact_refs,
            notes=notes,
        )
        report = build_report(run_id, [flow_entry])
        report_path = write_report(run_id, report, artifact_root=self.artifact_root)
        return flow_entry, report, report_path

    def run_core_golden_path(
        self,
        *,
        goal: str = "repair docker health",
        run_id: str = "e2e-core-run",
        flow_id: str = "core-golden-path",
    ) -> FlowRunResult:
        plan = self.llm.plan(goal)
        trace_id = f"trace-{plan.task_id}"
        worker_result = self.worker.execute(task_id=plan.task_id, prompt=plan.prompt)

        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps(
                {
                    "goal": goal,
                    "goal_id": plan.goal_id,
                    "task_id": plan.task_id,
                    "trace_id": trace_id,
                    "worker_status": worker_result.status,
                },
                sort_keys=True,
            ),
            artifact_root=self.artifact_root,
        )
        artifact_ref = write_text_artifact(
            run_id,
            flow_id,
            "artifact.txt",
            worker_result.artifact_body,
            artifact_root=self.artifact_root,
        )
        snapshot_ref = write_text_artifact(
            run_id,
            flow_id,
            "cli_goal_snapshot.txt",
            self.render_cli_goal_submit(goal_id=plan.goal_id, task_id=plan.task_id, trace_id=trace_id),
            artifact_root=self.artifact_root,
        )

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=True,
            logs=[log_ref],
            snapshots=[snapshot_ref],
            trace_bundle_refs=[trace_id],
            artifact_refs=[artifact_ref],
            notes=["core golden path via deterministic harness"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            goal_id=plan.goal_id,
            task_id=plan.task_id,
            trace_id=trace_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs={"goal_submit": snapshot_ref},
        )

    def run_cli_golden_path(
        self,
        *,
        goal: str = "repair docker health",
        run_id: str = "e2e-cli-golden",
        flow_id: str = "cli-golden-path",
    ) -> FlowRunResult:
        plan = self.llm.plan(goal)
        trace_id = f"trace-{plan.task_id}"
        worker_result = self.worker.execute(task_id=plan.task_id, prompt=plan.prompt)

        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps({"goal_id": plan.goal_id, "task_id": plan.task_id, "trace_id": trace_id}, sort_keys=True),
            artifact_root=self.artifact_root,
        )
        artifact_ref = write_text_artifact(
            run_id,
            flow_id,
            "artifact.txt",
            worker_result.artifact_body,
            artifact_root=self.artifact_root,
        )
        snapshot_refs = {
            "health": write_text_artifact(
                run_id,
                flow_id,
                "health.txt",
                self.render_cli_health(run_id=run_id),
                artifact_root=self.artifact_root,
            ),
            "goal_submit": write_text_artifact(
                run_id,
                flow_id,
                "goal_submit.txt",
                self.render_cli_goal_submit(goal_id=plan.goal_id, task_id=plan.task_id, trace_id=trace_id),
                artifact_root=self.artifact_root,
            ),
            "task_status": write_text_artifact(
                run_id,
                flow_id,
                "task_status.txt",
                self.render_cli_task_status(task_id=plan.task_id),
                artifact_root=self.artifact_root,
            ),
            "artifact_show": write_text_artifact(
                run_id,
                flow_id,
                "artifact_show.txt",
                self.render_cli_artifact_show(artifact_ref=artifact_ref),
                artifact_root=self.artifact_root,
            ),
        }

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=True,
            logs=[log_ref],
            snapshots=list(snapshot_refs.values()),
            trace_bundle_refs=[trace_id],
            artifact_refs=[artifact_ref],
            notes=["cli golden-path snapshot bundle"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            goal_id=plan.goal_id,
            task_id=plan.task_id,
            trace_id=trace_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs=snapshot_refs,
        )

    def run_cli_degraded_policy(
        self,
        *,
        run_id: str = "e2e-cli-degraded",
        flow_id: str = "cli-degraded-policy",
    ) -> FlowRunResult:
        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps(
                {
                    "states": [
                        "backend_unreachable",
                        "invalid_auth",
                        "approval_required",
                        "policy_denied",
                    ]
                },
                sort_keys=True,
            ),
            artifact_root=self.artifact_root,
        )
        snapshot_refs = {
            "backend_unreachable": write_text_artifact(
                run_id,
                flow_id,
                "backend_unreachable.txt",
                self.render_cli_backend_unreachable(),
                artifact_root=self.artifact_root,
            ),
            "invalid_auth": write_text_artifact(
                run_id,
                flow_id,
                "invalid_auth.txt",
                self.render_cli_invalid_auth(),
                artifact_root=self.artifact_root,
            ),
            "approval_required": write_text_artifact(
                run_id,
                flow_id,
                "approval_required.txt",
                self.render_cli_approval_required(),
                artifact_root=self.artifact_root,
            ),
            "policy_denied": write_text_artifact(
                run_id,
                flow_id,
                "policy_denied.txt",
                self.render_cli_policy_denied(),
                artifact_root=self.artifact_root,
            ),
        }

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=True,
            logs=[log_ref],
            snapshots=list(snapshot_refs.values()),
            trace_bundle_refs=["trace-policy-guardrails"],
            artifact_refs=[],
            notes=["degraded and policy-denied states are rendered as non-success"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs=snapshot_refs,
        )
