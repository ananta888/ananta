from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.e2e.e2e_artifacts import (
    build_report,
    compact_summary,
    make_flow_entry,
    redact_sensitive_text,
    write_binary_artifact,
    write_report,
    write_text_artifact,
)
from tests.e2e.mock_llm import MockLLM
from tests.e2e.mock_worker import MockWorker

_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAukB9pA8f6UAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class FlowRunResult:
    run_id: str
    flow_id: str
    report_path: str
    report: dict[str, Any]
    flow_entry: dict[str, Any]
    snapshot_refs: dict[str, str] = field(default_factory=dict)
    screenshot_refs: dict[str, str] = field(default_factory=dict)
    video_refs: dict[str, str] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
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
            (r"artifact:\s+(?:\S*[\\/]\S+)", "artifact: <ARTIFACT_REF>"),
            (r"screen_id:\s+\S+", "screen_id: <SCREEN_ID>"),
        ]
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)
        normalized = redact_sensitive_text(normalized)
        compact_lines = [line.rstrip() for line in normalized.splitlines()]
        return "\n".join(compact_lines).strip() + "\n"

    def normalize_terminal_snapshot(self, content: str) -> str:
        return self.normalize_cli_snapshot(content)

    def normalize_web_snapshot(self, content: str) -> str:
        normalized = re.sub(r"run_id:\s+\S+", "run_id: <RUN_ID>", str(content))
        normalized = redact_sensitive_text(normalized)
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

    def render_cli_approved_safe(self) -> str:
        return "\n".join(
            [
                "ananta cli",
                "command: task execute",
                "status: success",
                "policy: approved_safe_action",
                "executed: yes",
                "next step: review execution artifact",
                "",
            ]
        )

    def render_tui_screen(self, screen: str, *, task_id: str = "task-sample", run_id: str = "run-1") -> str:
        screen_id = f"tui-{screen}"
        lines = [
            "ananta tui",
            f"screen_id: {screen_id}",
            f"run_id: {run_id}",
        ]
        if screen == "health":
            lines.extend(["status: connected", "backend: healthy"])
        elif screen == "task_list":
            lines.extend(["tasks: 1", f"selected_task: {task_id}", "state: actionable"])
        elif screen == "artifact_view":
            lines.extend(["artifact: available", f"task_id: {task_id}", "render: text"])
        elif screen == "policy_denied":
            lines.extend(["status: denied", "policy: unsafe_action_block", "executed: no"])
        else:
            lines.extend(["status: degraded", "reason: backend unreachable"])
        lines.append("")
        return "\n".join(lines)

    def render_web_screen(self, screen: str, *, run_id: str = "run-1") -> str:
        lines = ["ananta web ui", f"screen: {screen}", f"run_id: {run_id}"]
        if screen == "dashboard":
            lines.extend(["status: healthy", "widget: goals=1"])
        elif screen == "goals_tasks":
            lines.extend(["goals: visible", "tasks: visible", "state: actionable"])
        elif screen == "artifact_view":
            lines.extend(["artifact: visible", "render: readable"])
        elif screen == "approval_required":
            lines.extend(["status: approval_required", "executed: no"])
        elif screen == "policy_denied":
            lines.extend(["status: denied", "policy: unsafe_action_block", "executed: no"])
        else:
            lines.extend(["status: degraded", "reason: backend unavailable"])
        lines.append("")
        return "\n".join(lines)

    def _persist_flow(
        self,
        *,
        run_id: str,
        flow_id: str,
        status: str,
        blocking: bool,
        logs: list[str],
        snapshots: list[str],
        screenshots: list[str] | None = None,
        videos: list[str] | None = None,
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
            screenshots=screenshots,
            videos=videos,
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
            artifact_refs=[artifact_ref],
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
            artifact_refs=[artifact_ref],
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
                {"states": ["backend_unreachable", "invalid_auth", "approval_required", "policy_denied"]},
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

    def run_tui_scripted_smoke(
        self,
        *,
        run_id: str = "e2e-tui-smoke",
        flow_id: str = "tui-scripted-smoke",
    ) -> FlowRunResult:
        task_id = "task-tui-smoke"
        screens = ("health", "task_list", "artifact_view", "degraded")
        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps({"screens": list(screens), "task_id": task_id}, sort_keys=True),
            artifact_root=self.artifact_root,
        )
        snapshot_refs: dict[str, str] = {}
        screenshot_refs: dict[str, str] = {}
        for screen in screens:
            snapshot_refs[screen] = write_text_artifact(
                run_id,
                flow_id,
                f"{screen}.txt",
                self.render_tui_screen(screen, task_id=task_id, run_id=run_id),
                artifact_root=self.artifact_root,
            )
            screenshot_refs[screen] = write_binary_artifact(
                run_id,
                flow_id,
                f"screenshot-{screen}.png",
                _PLACEHOLDER_PNG,
                artifact_root=self.artifact_root,
            )

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=True,
            logs=[log_ref],
            snapshots=list(snapshot_refs.values()),
            screenshots=list(screenshot_refs.values()),
            trace_bundle_refs=["trace-tui-scripted-smoke"],
            artifact_refs=[],
            notes=["headless scripted TUI smoke with deterministic screens"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs=snapshot_refs,
            screenshot_refs=screenshot_refs,
        )

    def run_web_ui_screenshots(
        self,
        *,
        run_id: str = "e2e-web-screenshots",
        flow_id: str = "web-ui-screenshots",
        web_available: bool = True,
    ) -> FlowRunResult:
        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps({"web_available": web_available}, sort_keys=True),
            artifact_root=self.artifact_root,
        )
        if not web_available:
            advisory_snapshot = write_text_artifact(
                run_id,
                flow_id,
                "web_unavailable.txt",
                "ananta web ui\nstatus: advisory\nreason: web ui unavailable in current environment\n",
                artifact_root=self.artifact_root,
            )
            flow_entry, report, report_path = self._persist_flow(
                run_id=run_id,
                flow_id=flow_id,
                status="advisory",
                blocking=False,
                logs=[log_ref],
                snapshots=[advisory_snapshot],
                trace_bundle_refs=["trace-web-ui-advisory-skip"],
                artifact_refs=[],
                notes=["web ui unavailable, captured advisory evidence instead of blocking failure"],
            )
            return FlowRunResult(
                run_id=run_id,
                flow_id=flow_id,
                report_path=report_path,
                report=report,
                flow_entry=flow_entry,
                snapshot_refs={"web_unavailable": advisory_snapshot},
            )

        screens = ("dashboard", "goals_tasks", "artifact_view", "degraded")
        snapshot_refs: dict[str, str] = {}
        screenshot_refs: dict[str, str] = {}
        for screen in screens:
            snapshot_refs[screen] = write_text_artifact(
                run_id,
                flow_id,
                f"{screen}.txt",
                self.render_web_screen(screen, run_id=run_id),
                artifact_root=self.artifact_root,
            )
            screenshot_refs[screen] = write_binary_artifact(
                run_id,
                flow_id,
                f"screenshot-{screen}.png",
                _PLACEHOLDER_PNG,
                artifact_root=self.artifact_root,
            )

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=False,
            logs=[log_ref],
            snapshots=list(snapshot_refs.values()),
            screenshots=list(screenshot_refs.values()),
            trace_bundle_refs=["trace-web-ui-capture"],
            artifact_refs=[],
            notes=["web ui screenshots captured in deterministic harness mode"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs=snapshot_refs,
            screenshot_refs=screenshot_refs,
        )

    def run_rag_tiny_repo(
        self,
        fixture_root: Path,
        *,
        query: str,
        run_id: str = "e2e-rag-tiny-repo",
        flow_id: str = "rag-dogfood-tiny-repo",
    ) -> FlowRunResult:
        fixture_root = fixture_root.resolve()
        index_entries: list[dict[str, str]] = []
        for candidate in sorted(fixture_root.rglob("*")):
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            relative_path = candidate.relative_to(fixture_root).as_posix()
            index_entries.append({"path": relative_path, "content": candidate.read_text(encoding="utf-8")})

        tokens = {token for token in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(token) >= 2}
        ranked: list[tuple[int, dict[str, str]]] = []
        for entry in index_entries:
            haystack = entry["content"].lower()
            score = sum(1 for token in tokens if token in haystack or token in entry["path"].lower())
            if score > 0:
                ranked.append((score, entry))
        ranked.sort(key=lambda item: (-item[0], item[1]["path"]))

        retrieval_results = []
        for score, entry in ranked[:3]:
            retrieval_results.append(
                {
                    "source_path": entry["path"],
                    "reason": f"matched_tokens={score}",
                    "snippet": entry["content"][:200],
                }
            )

        retrieval_payload = {
            "query": query,
            "index_size": len(index_entries),
            "results": retrieval_results,
            "bounded_to_fixture_root": True,
        }
        log_ref = write_text_artifact(
            run_id,
            flow_id,
            "flow.log",
            json.dumps({"query": query, "result_count": len(retrieval_results)}, sort_keys=True),
            artifact_root=self.artifact_root,
        )
        retrieval_report_ref = write_text_artifact(
            run_id,
            flow_id,
            "retrieval_report.json",
            json.dumps(retrieval_payload, indent=2),
            artifact_root=self.artifact_root,
        )
        snapshot_ref = write_text_artifact(
            run_id,
            flow_id,
            "retrieval_snapshot.txt",
            "\n".join(
                [
                    "ananta rag dogfood",
                    f"query: {query}",
                    f"results: {len(retrieval_results)}",
                    "sources: " + ", ".join(result["source_path"] for result in retrieval_results),
                    "",
                ]
            ),
            artifact_root=self.artifact_root,
        )
        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed" if retrieval_results else "failed",
            blocking=True,
            logs=[log_ref],
            snapshots=[snapshot_ref],
            trace_bundle_refs=["trace-rag-tiny-repo"],
            artifact_refs=[retrieval_report_ref],
            notes=["deterministic fixture retrieval with explicit source refs"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs={"retrieval": snapshot_ref},
            artifact_refs=[retrieval_report_ref],
        )

    def run_policy_approval_visual_evidence(
        self,
        *,
        run_id: str = "e2e-policy-approval",
        flow_id: str = "policy-approval-visual-evidence",
    ) -> FlowRunResult:
        cli_snapshots = {
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
            "approved_safe": write_text_artifact(
                run_id,
                flow_id,
                "approved_safe.txt",
                self.render_cli_approved_safe(),
                artifact_root=self.artifact_root,
            ),
        }
        tui_snapshot = write_text_artifact(
            run_id,
            flow_id,
            "tui_policy_denied.txt",
            self.render_tui_screen("policy_denied", task_id="task-policy"),
            artifact_root=self.artifact_root,
        )
        web_snapshot = write_text_artifact(
            run_id,
            flow_id,
            "web_approval_required.txt",
            self.render_web_screen("approval_required", run_id=run_id),
            artifact_root=self.artifact_root,
        )
        web_screenshot = write_binary_artifact(
            run_id,
            flow_id,
            "screenshot-web-approval-required.png",
            _PLACEHOLDER_PNG,
            artifact_root=self.artifact_root,
        )
        policy_log_ref = write_text_artifact(
            run_id,
            flow_id,
            "policy_decision.log",
            json.dumps(
                {
                    "policy_decision": "unsafe_action_block",
                    "approval_decision": "human_confirmation_gate",
                    "approved_safe_action": True,
                },
                sort_keys=True,
            ),
            artifact_root=self.artifact_root,
        )
        execution_ref = write_text_artifact(
            run_id,
            flow_id,
            "approved_execution.txt",
            "status=success\naction=safe_bounded_restart\nexecuted=yes\n",
            artifact_root=self.artifact_root,
        )

        flow_entry, report, report_path = self._persist_flow(
            run_id=run_id,
            flow_id=flow_id,
            status="passed",
            blocking=True,
            logs=[policy_log_ref],
            snapshots=list(cli_snapshots.values()) + [tui_snapshot, web_snapshot],
            screenshots=[web_screenshot],
            trace_bundle_refs=["trace-policy-decision", "trace-approval-decision"],
            artifact_refs=[execution_ref],
            notes=["denied and approval-required states are explicit and non-success"],
        )
        return FlowRunResult(
            run_id=run_id,
            flow_id=flow_id,
            report_path=report_path,
            report=report,
            flow_entry=flow_entry,
            snapshot_refs={
                **cli_snapshots,
                "tui_policy_denied": tui_snapshot,
                "web_approval_required": web_snapshot,
            },
            screenshot_refs={"web_approval_required": web_screenshot},
            artifact_refs=[execution_ref],
        )
