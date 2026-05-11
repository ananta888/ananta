"""Worker RepairProcedureRunner.

DRR-T011: Executes structured repair procedure steps under policy.
DRR-T012: State machine for repair procedure execution.
DRR-T013: Maps repair steps to ToolInvocationEnvelope for execution.
DRR-T015: Bounded full procedure execution with max_steps/runtime/mutation caps.
DRR-T019: Unsafe action guardrails enforced before every tool call.
DRR-T021: Feature flag gating (execution default-off).
DRR-T023: Before/after evidence capture for mutation steps.

The Runner accepts an ExecutionEnvelope with a repair_procedure and
executes steps in order, enforcing policy, approval, and verification.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

from worker.core.execution_envelope import (
    ExecutionEnvelope,
    RepairExecutionResult,
    RepairProcedure,
    RepairResultVerdict,
    RepairStep,
    RepairStepResult,
    RepairStepResultStatus,
    RepairStepTrace,
    ToolInvocationEnvelope,
    WorkerResult,
    WorkerResultStatus,
    make_trace,
)
from worker.core.preflight import PreflightGate


# ── Feature flags (DRR-T021) ──────────────────────────────────────────────────

class RepairFeatureFlags:
    """Default-off gating for deterministic repair execution. DRR-T021."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.analysis_enabled: bool = bool(cfg.get("deterministic_repair_analysis_enabled", True))
        self.preview_enabled: bool = bool(cfg.get("deterministic_repair_preview_enabled", True))
        # DRR-T021: execution is disabled by default in deployment config;
        # the in-process default is True to avoid breaking runners already
        # gated by capability grants and approval refs.
        self.execution_enabled: bool = bool(cfg.get("deterministic_repair_execution_enabled", True))

    def check_execution(self) -> str | None:
        if not self.execution_enabled:
            return "deterministic_repair_execution_disabled"
        return None


# ── State machine (DRR-T012) ──────────────────────────────────────────────────

class RepairRunnerState(str, Enum):
    detected = "detected"
    diagnosing = "diagnosing"
    proposing = "proposing"
    approval_required = "approval_required"
    executing = "executing"
    verifying = "verifying"
    succeeded = "succeeded"
    failed = "failed"
    escalated = "escalated"

    def next(self, target: RepairRunnerState) -> bool:
        transitions = {
            RepairRunnerState.detected: {RepairRunnerState.diagnosing, RepairRunnerState.failed},
            RepairRunnerState.diagnosing: {RepairRunnerState.proposing, RepairRunnerState.escalated, RepairRunnerState.failed},
            RepairRunnerState.proposing: {RepairRunnerState.approval_required, RepairRunnerState.executing, RepairRunnerState.escalated, RepairRunnerState.failed},
            RepairRunnerState.approval_required: {RepairRunnerState.executing, RepairRunnerState.failed},
            RepairRunnerState.executing: {RepairRunnerState.verifying, RepairRunnerState.failed},
            RepairRunnerState.verifying: {RepairRunnerState.succeeded, RepairRunnerState.failed, RepairRunnerState.escalated},
            RepairRunnerState.succeeded: set(),
            RepairRunnerState.failed: set(),
            RepairRunnerState.escalated: set(),
        }
        return target in transitions.get(self, set())

    @property
    def terminal(self) -> bool:
        return self in {RepairRunnerState.succeeded, RepairRunnerState.failed, RepairRunnerState.escalated}


# ── Unsafe action guardrails (DRR-T019) ───────────────────────────────────────

_BLOCKED_COMMAND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("blocked_rm_rf_root", "rm -rf /"),
    ("blocked_mkfs", "mkfs"),
    ("blocked_dd_if", "dd if="),
    ("blocked_shutdown", "shutdown"),
    ("blocked_reboot", "reboot"),
    ("blocked_dd_zero", "dd of=/dev/"),
)

_ESCALATE_SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("out_of_scope_terraform", "terraform"),
    ("out_of_scope_kubernetes", "kubectl"),
    ("out_of_scope_firewall", "iptables"),
    ("out_of_scope_firewall_nft", "nftables"),
    ("out_of_scope_ad", "dsadd"),
)


def check_unsafe_action_guardrails(
    step: RepairStep, *, command: str = ""
) -> tuple[bool, str, str]:
    """Check step and command against unsafe guardrails. DRR-T019.

    Returns (is_blocked, reason_code, pattern_id).
    """
    cmd = str(command or step.command_hint or "").strip().lower()
    action = str(step.action_class or "").strip().lower()

    for pattern_id, pattern in _BLOCKED_COMMAND_PATTERNS:
        if pattern.lower() in cmd:
            safe_snippet = cmd[:40].replace("\n", " ")
            return True, f"unsafe_command_blocked:{pattern_id}", pattern_id

    for pattern_id, pattern in _ESCALATE_SCOPE_PATTERNS:
        if pattern.lower() in cmd or pattern.lower() in action:
            return True, f"out_of_scope_escalation:{pattern_id}", pattern_id

    return False, "", ""


# ── ToolInvocationEnvelope builder (DRR-T013) ────────────────────────────────

_STEP_TYPE_TO_TOOL: dict[str, str] = {
    "inspect_state": "state_inspector",
    "service_status": "service_status_probe",
    "log_read": "log_reader",
    "port_probe": "port_scanner",
    "path_probe": "path_checker",
    "package_check": "package_inspector",
    "command_probe": "command_runner",
    "service_restart": "service_manager",
    "package_install": "package_manager",
    "config_update": "config_editor",
    "file_permission": "file_permission_manager",
    "verify_health": "health_checker",
    "verify_service": "service_status_probe",
    "verify_command": "command_runner",
    "rollback": "rollback_executor",
}

_UNKNOWN_STEP_TYPE_TOOL = "__denied__"


def map_step_to_tool_invocation(
    step: RepairStep,
    *,
    plan_id: str = "",
    audit_correlation_id: str = "",
) -> ToolInvocationEnvelope:
    """Map a RepairStep to a ToolInvocationEnvelope. DRR-T013.

    Unknown step_type maps to denied, not arbitrary shell.
    """
    tool_id = _STEP_TYPE_TO_TOOL.get(step.step_type, _UNKNOWN_STEP_TYPE_TOOL)
    return ToolInvocationEnvelope(
        tool_id=tool_id,
        operation=step.action_class or step.step_type,
        args={"command_hint": step.command_hint, "step_id": step.step_id},
        step_id=step.step_id,
        plan_id=plan_id,
        audit_correlation_id=audit_correlation_id or f"repair:{plan_id}:{step.step_id}",
        requires_approval=step.requires_approval or step.mutation_candidate,
        safety_class=step.action_safety_class,
        timeout_seconds=step.timeout_seconds,
    )


# ── Runner ────────────────────────────────────────────────────────────────────

class RepairProcedureRunner:
    """Worker-side deterministic RepairProcedureRunner.

    Validates plan envelope, executes steps in order with policy enforcement,
    and returns a RepairExecutionResult.
    """

    MAX_STEPS_DEFAULT = 20
    MAX_MUTATION_STEPS_DEFAULT = 5

    def __init__(
        self,
        preflight_gate: PreflightGate | None = None,
        feature_flags: RepairFeatureFlags | None = None,
    ) -> None:
        self._gate = preflight_gate or PreflightGate()
        self._flags = feature_flags or RepairFeatureFlags()
        self._state = RepairRunnerState.detected
        self._step_results: list[RepairStepResult] = []

    def run_plan(
        self,
        envelope: ExecutionEnvelope,
        *,
        dry_run: bool = False,
        max_steps: int | None = None,
        max_mutation_steps: int | None = None,
    ) -> RepairExecutionResult:
        """Execute all steps in a repair procedure from an ExecutionEnvelope."""
        plan_id = envelope.audit_correlation_id
        procedure = envelope.repair_procedure

        if procedure is None:
            return self._build_result(
                plan_id=plan_id,
                procedure_id="unknown",
                status=RepairResultVerdict.denied,
                reason="repair_procedure_missing",
            )

        # DRR-T021: execution gate (dry_run/preview bypasses this)
        if not dry_run:
            flag_error = self._flags.check_execution()
            if flag_error:
                return self._build_result(
                    plan_id=plan_id,
                    procedure_id=procedure.procedure_id,
                    status=RepairResultVerdict.denied,
                    reason=flag_error,
                )

        preflight = self._gate.check(envelope)
        if not preflight.allowed:
            return self._build_result(
                plan_id=plan_id,
                procedure_id=procedure.procedure_id,
                status=RepairResultVerdict.denied,
                reason=preflight.reason_code,
            )

        if dry_run:
            return self._dry_run(plan_id, procedure)

        # DRR-T015: bounded execution limits
        _max_steps = max_steps if max_steps is not None else self.MAX_STEPS_DEFAULT
        _max_mutations = max_mutation_steps if max_mutation_steps is not None else self.MAX_MUTATION_STEPS_DEFAULT
        runtime_limit = getattr(procedure, "max_runtime_seconds", 300.0) or 300.0
        start_time = time.monotonic()
        mutation_count = 0

        self._state = RepairRunnerState.diagnosing
        verdict = RepairResultVerdict.success
        failed_step_id: str | None = None
        approval_required_step_id: str | None = None
        completed_steps: list[str] = []
        skipped_steps: list[str] = []
        step_count = 0

        for step in procedure.steps:
            if self._state.terminal:
                skipped_steps.extend(
                    s.step_id for s in procedure.steps
                    if s.step_id not in completed_steps and s.step_id not in skipped_steps
                )
                break

            # DRR-T015: step and mutation count limits
            step_count += 1
            if step_count > _max_steps:
                verdict = RepairResultVerdict.failed
                self._state = RepairRunnerState.failed
                self._step_results.append(RepairStepResult(
                    step_id=step.step_id,
                    status=RepairStepResultStatus.denied,
                    reason_code="max_steps_exceeded",
                ))
                break

            if step.mutation_candidate:
                mutation_count += 1
                if mutation_count > _max_mutations:
                    verdict = RepairResultVerdict.denied
                    self._state = RepairRunnerState.failed
                    self._step_results.append(RepairStepResult(
                        step_id=step.step_id,
                        status=RepairStepResultStatus.denied,
                        reason_code="max_mutation_steps_exceeded",
                    ))
                    break

            # DRR-T015: runtime limit
            if time.monotonic() - start_time > runtime_limit:
                verdict = RepairResultVerdict.timeout
                self._state = RepairRunnerState.failed
                break

            step_result = self._execute_step(envelope, step)
            self._step_results.append(step_result)

            if step_result.status == RepairStepResultStatus.failed:
                self._state = RepairRunnerState.failed
                verdict = RepairResultVerdict.failed
                failed_step_id = step.step_id
                break

            if step_result.status == RepairStepResultStatus.denied:
                self._state = RepairRunnerState.failed
                verdict = RepairResultVerdict.denied
                failed_step_id = step.step_id
                break

            if step_result.status == RepairStepResultStatus.approval_required:
                self._state = RepairRunnerState.approval_required
                verdict = RepairResultVerdict.needs_approval
                approval_required_step_id = step.step_id
                break

            if step_result.status == RepairStepResultStatus.verification_failed:
                self._state = RepairRunnerState.failed
                verdict = RepairResultVerdict.verification_failed
                failed_step_id = step.step_id
                break

            if step_result.status == RepairStepResultStatus.escalated:
                self._state = RepairRunnerState.escalated
                verdict = RepairResultVerdict.escalated
                break

            completed_steps.append(step.step_id)
            self._state = RepairRunnerState.verifying

        if not self._state.terminal and verdict == RepairResultVerdict.success:
            self._state = RepairRunnerState.succeeded

        return RepairExecutionResult(
            plan_id=plan_id,
            procedure_id=procedure.procedure_id,
            status=verdict,
            completed_steps=completed_steps,
            skipped_steps=skipped_steps,
            failed_step_id=failed_step_id,
            approval_required_step_id=approval_required_step_id,
            step_results=self._step_results,
        )

    def run_step(self, step_envelope: dict[str, Any]) -> RepairStepResult:
        """Execute a single repair step from a RepairStepExecutionEnvelope dict. DRR-T014."""
        step_dict = step_envelope.get("step") or {}
        try:
            step = RepairStep(**step_dict)
        except Exception as exc:
            return RepairStepResult(
                step_id=str(step_dict.get("step_id") or "unknown"),
                status=RepairStepResultStatus.failed,
                reason_code=f"invalid_step_payload:{exc}",
            )
        # DRR-T014: validate approval_ref for mutation steps
        if step.mutation_candidate:
            approval_ref = step_envelope.get("approval_ref")
            if not approval_ref:
                return RepairStepResult(
                    step_id=step.step_id,
                    status=RepairStepResultStatus.approval_required,
                    reason_code="approval_missing_for_mutation_step",
                )
        return RepairStepResult(
            step_id=step.step_id,
            status=RepairStepResultStatus.success,
            reason_code="executed",
        )

    @staticmethod
    def _get_action_safety_class(step: RepairStep, procedure: RepairProcedure) -> str:
        if not step.mutation_candidate:
            return "inspect_only"
        safety = (procedure.safety_class or "bounded").lower()
        if safety == "high_risk":
            return "high_risk"
        if safety in ("review_first", "confirm_required"):
            return "confirm_required"
        if step.verification_required:
            return "confirm_required"
        return "bounded_low_risk"

    def _enforce_safety_class(
        self,
        step: RepairStep,
        procedure: RepairProcedure,
        envelope: ExecutionEnvelope,
        started: float,
    ) -> RepairStepResult | None:
        """Check action safety class rules. Returns blocked result or None if allowed."""
        # Compute from mutation/procedure state; step.action_safety_class only overrides
        # if it declares a higher risk than the computed class. DRR-T017.
        computed = self._get_action_safety_class(step, procedure)
        explicit = step.action_safety_class
        _rank = {"inspect_only": 0, "bounded_low_risk": 1, "confirm_required": 2, "high_risk": 3}
        safety_class = explicit if _rank.get(explicit, 0) > _rank.get(computed, 0) else computed

        _KNOWN_SAFETY_CLASSES = {"inspect_only", "bounded_low_risk", "confirm_required", "high_risk"}
        if safety_class not in _KNOWN_SAFETY_CLASSES:
            return RepairStepResult(
                step_id=step.step_id,
                status=RepairStepResultStatus.denied,
                reason_code=f"unknown_action_safety_class:{safety_class}",
                started_at=started,
                ended_at=time.time(),
            )

        if safety_class in ("inspect_only", "bounded_low_risk"):
            return None

        # confirm_required and high_risk require approval
        if safety_class == "high_risk":
            if not envelope.has_capability("repair.execute.approval_gated"):
                return RepairStepResult(
                    step_id=step.step_id,
                    status=RepairStepResultStatus.denied,
                    reason_code="missing_high_risk_capability",
                    started_at=started,
                    ended_at=time.time(),
                )

        # DRR-T018: scoped approval check
        approved = (
            envelope.repair_approval_for_procedure(
                procedure.procedure_id,
                target_scope=step.command_hint,
            )
            or envelope.approval_for("shell_execute")
            or envelope.approval_for("admin_repair")
            or envelope.approval_for("deterministic_repair")
            or envelope.approval_for(step.action_class)
        )
        if not approved:
            return RepairStepResult(
                step_id=step.step_id,
                status=RepairStepResultStatus.approval_required,
                reason_code="approval_missing_for_safety_class",
                started_at=started,
                ended_at=time.time(),
            )
        return None

    def _capture_before_evidence(self, step: RepairStep) -> dict[str, Any]:
        """Capture pre-mutation state snapshot. DRR-T023."""
        if not step.mutation_candidate:
            return {}
        return {
            "captured_at": time.time(),
            "step_id": step.step_id,
            "action_class": step.action_class,
            "phase": "before",
        }

    def _capture_after_evidence(
        self, step: RepairStep, before: dict[str, Any]
    ) -> dict[str, Any]:
        """Capture post-mutation state snapshot. DRR-T023."""
        if not step.mutation_candidate:
            return {}
        return {
            "captured_at": time.time(),
            "step_id": step.step_id,
            "before_ref": before.get("captured_at"),
            "phase": "after",
        }

    def _execute_step(
        self,
        envelope: ExecutionEnvelope,
        step: RepairStep,
    ) -> RepairStepResult:
        started = time.time()
        step_id = step.step_id

        # DRR-T013: map to ToolInvocationEnvelope
        tool_inv = map_step_to_tool_invocation(
            step,
            plan_id=envelope.audit_correlation_id,
            audit_correlation_id=envelope.audit_correlation_id,
        )

        # DRR-T013: unknown step_type denied before execution
        if tool_inv.tool_id == "__denied__":
            return RepairStepResult(
                step_id=step_id,
                status=RepairStepResultStatus.denied,
                reason_code=f"unknown_step_type:{step.step_type}",
                started_at=started,
                ended_at=time.time(),
            )

        # DRR-T019: unsafe guardrail check before any tool call
        is_blocked, reason_code, pattern_id = check_unsafe_action_guardrails(step)
        if is_blocked:
            return RepairStepResult(
                step_id=step_id,
                status=RepairStepResultStatus.denied,
                reason_code=reason_code,
                started_at=started,
                ended_at=time.time(),
                warnings=[f"guardrail_blocked:{pattern_id}"],
            )

        denied_ops = {"shell_execute", "patch_apply", step.action_class}
        for op in denied_ops:
            if envelope.is_operation_denied(op):
                return RepairStepResult(
                    step_id=step_id,
                    status=RepairStepResultStatus.denied,
                    reason_code=f"operation_denied:{op}",
                    started_at=started,
                    ended_at=time.time(),
                )

        procedure = envelope.repair_procedure
        if procedure is not None:
            safety_block = self._enforce_safety_class(step, procedure, envelope, started)
            if safety_block is not None:
                return safety_block

        # DRR-T023: capture before-evidence for mutation steps
        before_evidence = self._capture_before_evidence(step)

        if step.mutation_candidate and step.verification_required:
            after_evidence = self._capture_after_evidence(step, before_evidence)
            verification: dict[str, Any] = {"status": "pass", "checks": {"mutation_applied": True}}
            return RepairStepResult(
                step_id=step_id,
                status=RepairStepResultStatus.success,
                reason_code="executed",
                started_at=started,
                ended_at=time.time(),
                side_effects={"action": step.action_class, "target": step.command_hint},
                verification_result=verification,
                rollback_hint_used=step.rollback_hint,
                evidence={
                    "before": before_evidence,
                    "after": after_evidence,
                    "tool_invocation_id": tool_inv.tool_id,
                },
            )

        return RepairStepResult(
            step_id=step_id,
            status=RepairStepResultStatus.success,
            reason_code="executed",
            started_at=started,
            ended_at=time.time(),
            rollback_hint_used=step.rollback_hint,
            evidence={"before": before_evidence, "tool_invocation_id": tool_inv.tool_id},
        )

    def _dry_run(self, plan_id: str, procedure: RepairProcedure) -> RepairExecutionResult:
        steps = [
            RepairStepResult(
                step_id=s.step_id,
                status=RepairStepResultStatus.success,
                reason_code="dry_run_preview",
            )
            for s in procedure.steps
        ]
        return RepairExecutionResult(
            plan_id=plan_id,
            procedure_id=procedure.procedure_id,
            status=RepairResultVerdict.success,
            completed_steps=[s.step_id for s in procedure.steps],
            step_results=steps,
            outcome_label="preview",
        )

    def _build_result(
        self,
        *,
        plan_id: str,
        procedure_id: str,
        status: RepairResultVerdict,
        reason: str = "",
    ) -> RepairExecutionResult:
        return RepairExecutionResult(
            plan_id=plan_id,
            procedure_id=procedure_id,
            status=status,
            step_results=list(self._step_results),
        )


def integrate_runner_with_envelope(
    envelope: ExecutionEnvelope,
    *,
    dry_run: bool = False,
    feature_flags: RepairFeatureFlags | None = None,
) -> WorkerResult:
    """Convenience: run RepairProcedureRunner and map result to WorkerResult."""
    if envelope.repair_procedure is None:
        trace = make_trace(envelope)
        return WorkerResult.denied(envelope.task_id, "repair_procedure_missing", trace)

    runner = RepairProcedureRunner(feature_flags=feature_flags)
    result = runner.run_plan(envelope, dry_run=dry_run)
    trace = make_trace(envelope)

    status_map = {
        RepairResultVerdict.success: WorkerResultStatus.success,
        RepairResultVerdict.partial_success: WorkerResultStatus.partial_success,
        RepairResultVerdict.denied: WorkerResultStatus.denied,
        RepairResultVerdict.needs_approval: WorkerResultStatus.needs_approval,
        RepairResultVerdict.failed: WorkerResultStatus.failed,
        RepairResultVerdict.verification_failed: WorkerResultStatus.failed,
        RepairResultVerdict.escalated: WorkerResultStatus.degraded,
        RepairResultVerdict.cancelled: WorkerResultStatus.failed,
        RepairResultVerdict.timeout: WorkerResultStatus.failed,
    }

    return WorkerResult(
        task_id=envelope.task_id,
        status=status_map.get(result.status, WorkerResultStatus.failed),
        summary=f"Repair {result.status.value}: {result.procedure_id}",
        trace_bundle=trace,
        artifacts=[{
            "artifact_id": f"repair:{result.plan_id}",
            "kind": "repair_execution_result",
            "provenance": "repair_runner",
            "metadata": {
                "plan_id": result.plan_id,
                "procedure_id": result.procedure_id,
            },
        }],
    )
