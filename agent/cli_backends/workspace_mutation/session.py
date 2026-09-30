"""Per-run state of the workspace-mutation loop (AWWPI-014/015/016).

``MutationSession`` owns the evidence feedback, the hub observation step
(DiffResult + PolicyResult against the baseline), the progress detection
and the final ``.ananta/mutation-report.json``. The per-kind action
handlers (``handlers``) only talk to this object, which keeps the loop
driver in ``_orchestrator`` small.
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.cli_backends.workspace_mutation.audit_events import emit_evaluation_events
from agent.cli_backends.workspace_mutation.signatures import changes_signature, evidence_signature
from agent.cli_backends.workspace_mutation.tools import extract_policy_config

LoopResult = tuple[int, str, str]


@dataclass(frozen=True)
class LoopTurn:
    """One parsed model answer inside the loop; ``row`` is its (mutable) report row."""

    iteration: int
    message: dict[str, Any]
    row: dict[str, Any]
    out: str
    err: str


@dataclass(frozen=True)
class MutationServices:
    """Service ports resolved once per run from ``CliBackendContext``."""

    workspace: Any
    mutation_policy: Any
    tool_policy: Any
    source_line_policy: Any


@dataclass(frozen=True)
class MutationLimits:
    max_iterations: int
    max_invalid: int
    max_attempts_per_file: int
    max_diff_chars: int


class MutationSession:
    def __init__(
        self,
        *,
        cfg: dict[str, Any],
        mode: str,
        workspace: pathlib.Path,
        task_id: str | None,
        services: MutationServices,
        limits: MutationLimits,
    ) -> None:
        self.cfg = cfg
        self.mode = mode
        self.workspace = workspace
        self.task_id = task_id
        self.services = services
        self.limits = limits
        self.session_id = uuid.uuid4().hex[:12]
        self.materialization_manifest = services.workspace.load_materialization_manifest(workspace)
        self.baseline_meta = services.workspace.refresh_mutation_baseline(workspace_dir=workspace, mutation_mode=mode)
        self.evidence_blocks: list[dict[str, Any]] = []
        self._seen_evidence: set[str] = set()
        self.report_iterations: list[dict[str, Any]] = []
        self.file_attempts: dict[str, int] = {}
        self.invalid_count = 0
        self.tool_call_count = 0
        self.last_policy_result: dict[str, Any] | None = None
        self.last_source_line_policy_result: dict[str, Any] | None = None
        self._last_change_signature: str | None = None
        self._repeated_signature_count = 0

    # --- evidence -------------------------------------------------------------------------------

    def add_evidence(self, entry: dict[str, Any]) -> None:
        signature = evidence_signature(entry)
        if signature in self._seen_evidence:
            return
        self._seen_evidence.add(signature)
        self.evidence_blocks.append(entry)

    def next_tool_call_id(self, prefix: str) -> str:
        self.tool_call_count += 1
        return f"{prefix}:{self.tool_call_count}"

    def count_file_attempt(self, rel: str) -> int:
        attempts = self.file_attempts.get(rel, 0) + 1
        self.file_attempts[rel] = attempts
        return attempts

    def evaluate_source_lines(self, changed_rel_paths: list[str], baseline: dict[str, Any] | None) -> Any:
        return self.services.source_line_policy.evaluate_changed_files(
            workspace_dir=self.workspace,
            changed_rel_paths=changed_rel_paths,
            cfg=extract_policy_config(self.cfg),
            baseline=baseline,
            context={"task_id": self.task_id},
        )

    # --- hub observation step -------------------------------------------------------------------

    def hub_check(
        self,
        *,
        iteration_number: int | None = None,
        ran_tests_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """DiffResult + PolicyResult against baseline (the hub observation step).

        ALWA-014: emits ``workspace_mutation_evaluated`` on every call with the canonical schema fields.
        """
        ws_svc = self.services.workspace
        changed = ws_svc.detect_changed_files_against_interactive_baseline(workspace_dir=self.workspace)
        meaningful = ws_svc.filter_meaningful_changed_files(changed)
        diff_text, diff_truncated = ws_svc.build_workspace_diff_text(
            workspace_dir=self.workspace, changed_rel_paths=meaningful, max_chars=self.limits.max_diff_chars
        )
        policy_result = self.services.mutation_policy.evaluate_changed_files(
            workspace_dir=self.workspace,
            changed_rel_paths=meaningful,
            materialization_manifest=self.materialization_manifest,
            allowed_new_file_globs=list(self.cfg.get("allowed_new_file_globs") or []),
            require_materialized_scope=bool(self.cfg.get("require_materialized_scope", True)),
            strict_path_markers=list(self.cfg.get("strict_path_markers") or []) or None,
        )
        source_line_policy_result = self.evaluate_source_lines(meaningful, None)
        self.last_policy_result = policy_result.as_dict()
        self.last_source_line_policy_result = source_line_policy_result.as_dict()
        check: dict[str, Any] = {
            "schema": "ananta_workspace_feedback.v1",
            "diff_result": {"changed_files": meaningful, "diff_excerpt": diff_text, "truncated": diff_truncated},
            "policy_result": self.last_policy_result,
            "source_line_policy_result": self.last_source_line_policy_result,
        }
        if ran_tests_result is not None:
            check["test_result"] = ran_tests_result
        emit_evaluation_events(
            policy_result=policy_result,
            source_line_result=self.last_source_line_policy_result,
            diff_text=diff_text,
            changed_paths=meaningful,
            task_id=self.task_id,
            iteration_number=iteration_number,
            mode=self.mode,
        )
        return check

    def no_progress_detected(self, check: dict[str, Any], *, applied: bool) -> bool:
        """Track the change-set signature; True once the loop stops making progress."""
        changed = list((check.get("diff_result") or {}).get("changed_files") or [])
        signature = changes_signature(self.workspace, changed)
        if signature == self._last_change_signature:
            self._repeated_signature_count += 1
        else:
            self._repeated_signature_count = 0
        self._last_change_signature = signature
        if self._repeated_signature_count >= 1 and not applied:
            return True
        return self._repeated_signature_count >= 2

    # --- report ---------------------------------------------------------------------------------

    def write_report(self, outcome: str) -> None:
        source_line = self.last_source_line_policy_result
        report = {
            "schema": "ananta_worker_mutation_report.v1",
            "session_id": self.session_id,
            "task_id": self.task_id,
            "mutation_mode": self.mode,
            "outcome": outcome,
            "baseline": self.baseline_meta,
            "final_policy_result": self.last_policy_result,
            "source_line_policy_summary": dict((source_line or {}).get("summary") or {}) if source_line else None,
            "final_source_line_policy_result": source_line,
            "invalid_output_count": self.invalid_count,
            "tool_call_count": self.tool_call_count,
            "created_at": time.time(),
            "iterations": self.report_iterations,
        }
        try:
            report_path = self.workspace / ".ananta" / "mutation-report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def finish(self, outcome: str, rc: int, out: str, err: str) -> LoopResult:
        self.write_report(outcome)
        return rc, out, err

    def finish_with_summary(self, outcome: str, summary: dict[str, Any], err: str) -> LoopResult:
        return self.finish(outcome, 0, json.dumps(summary, ensure_ascii=False), err)
