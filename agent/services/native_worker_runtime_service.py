from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from worker.core.degraded import build_degraded_state
from worker.core.execution_profile import normalize_execution_profile
from worker.core.verification import build_verification_artifact, validate_worker_schema_or_degraded
from worker.shell.command_executor import execute_command_plan
from worker.shell.command_planner import build_command_plan_artifact
from worker.shell.command_policy import classify_command


def _build_context_hash(*, context_bundle_id: str, task_id: str, command: str) -> str:
    source = f"{context_bundle_id}:{task_id}:{command}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _bounded_text(value: str, *, max_chars: int = 2400) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 14)].rstrip() + "\n\n[truncated]"


class NativeWorkerRuntimeService:
    """Owns native worker command-plan/execute/verify stages for task-scoped runtime."""

    @staticmethod
    def _native_runtime_cfg(agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        runtime_cfg = (agent_cfg or {}).get("worker_runtime") if isinstance((agent_cfg or {}).get("worker_runtime"), dict) else {}
        native_cfg = runtime_cfg.get("native_worker_runtime") if isinstance(runtime_cfg.get("native_worker_runtime"), dict) else {}
        return dict(native_cfg or {})

    def is_enabled(self, *, agent_cfg: dict[str, Any] | None) -> bool:
        return bool(self._native_runtime_cfg(agent_cfg).get("enabled", False))

    def fallback_backend(self, *, agent_cfg: dict[str, Any] | None) -> str:
        native_cfg = self._native_runtime_cfg(agent_cfg)
        fallback = str(native_cfg.get("fallback_backend") or "sgpt").strip().lower()
        return fallback or "sgpt"

    def shell_policy(self, *, agent_cfg: dict[str, Any] | None) -> dict[str, Any]:
        native_cfg = self._native_runtime_cfg(agent_cfg)
        configured = native_cfg.get("shell_policy") if isinstance(native_cfg.get("shell_policy"), dict) else {}
        allowlist = [
            str(item).strip()
            for item in list(configured.get("allowlist") or ["python", "pytest", "git", "ls", "pwd", "whoami", "id", "cat", "head", "tail", "wc", "grep", "echo"])
            if str(item).strip()
        ]
        approval_required = [
            str(item).strip()
            for item in list(configured.get("approval_required_commands") or ["rm", "mv", "chmod", "chown", "sudo"])
            if str(item).strip()
        ]
        denylist_tokens = [
            str(item).strip()
            for item in list(configured.get("denylist_tokens") or ["rm -rf /", ":(){", "mkfs"])
            if str(item).strip()
        ]
        return {
            "allowlist": sorted(set(allowlist)),
            "approval_required_commands": sorted(set(approval_required)),
            "denylist_tokens": sorted(set(denylist_tokens)),
        }

    def prepare_native_command_plan(
        self,
        *,
        tid: str,
        task: dict[str, Any],
        command: str | None,
        reason: str | None,
        worker_profile: str | None,
        profile_source: str | None,
        trace_id: str | None,
        context_bundle_id: str | None,
        agent_cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.is_enabled(agent_cfg=agent_cfg):
            return {"runtime_path": "sgpt_fallback_proxy", "worker_context_updates": {}}

        normalized_command = str(command or "").strip()
        if not normalized_command:
            return {
                "runtime_path": "native_worker_pipeline",
                "policy_classification_summary": "no_command",
                "worker_context_updates": {"native_runtime": {"runtime_path": "native_worker_pipeline", "mode": "command_plan_skipped"}},
            }

        normalized_profile = normalize_execution_profile(worker_profile)
        normalized_profile_source = str(profile_source or "agent_default").strip().lower() or "agent_default"
        bundle_id = str(context_bundle_id or task.get("context_bundle_id") or f"ctx-{tid}").strip()
        context_hash = _build_context_hash(context_bundle_id=bundle_id, task_id=tid, command=normalized_command)
        shell_policy = self.shell_policy(agent_cfg=agent_cfg)

        ingress_request = self._build_request_payload(
            tid=tid,
            goal_id=str(task.get("goal_id") or "goal-unknown"),
            trace_id=str(trace_id or f"native-plan-{tid}"),
            capability_id="worker.command.plan",
            mode="command_plan",
            context_bundle_id=bundle_id,
            context_hash=context_hash,
            policy_decision_ref={"decision_id": f"native-plan-{tid}", "decision": "allow", "policy_version": "native_worker_runtime_v1"},
            worker_profile=normalized_profile,
            profile_source=normalized_profile_source,
            requested_outputs=["command_plan_artifact", "trace_metadata"],
        )
        ingress_ok, ingress_degraded = validate_worker_schema_or_degraded(
            schema_name="worker_execution_request.v1",
            payload=ingress_request,
            direction="ingress",
        )
        if not ingress_ok:
            return {
                "runtime_path": "sgpt_fallback_proxy",
                "policy_classification_summary": "schema_invalid:ingress",
                "degraded": ingress_degraded,
                "worker_context_updates": {
                    "native_runtime": {
                        "runtime_path": "sgpt_fallback_proxy",
                        "degraded": ingress_degraded,
                    }
                },
            }

        decision = classify_command(
            command=normalized_command,
            policy=shell_policy,
            hub_policy_decision="allow",
            execution_profile=normalized_profile,
        )
        command_plan = build_command_plan_artifact(
            task_id=tid,
            capability_id="worker.command.plan",
            command=normalized_command,
            explanation=str(reason or "Native worker command plan."),
            expected_effects=["Execute bounded command and produce verification artifact."],
            policy=shell_policy,
            hub_policy_decision="allow",
            execution_profile=normalized_profile,
        )
        plan_ok, plan_degraded = validate_worker_schema_or_degraded(
            schema_name="command_plan_artifact.v1",
            payload=command_plan,
            direction="egress",
        )
        if not plan_ok:
            return {
                "runtime_path": "sgpt_fallback_proxy",
                "policy_classification_summary": "schema_invalid:command_plan",
                "degraded": plan_degraded,
                "worker_context_updates": {
                    "native_runtime": {
                        "runtime_path": "sgpt_fallback_proxy",
                        "degraded": plan_degraded,
                    }
                },
            }

        policy_summary = f"{decision.classification}:{decision.reason}"
        return {
            "runtime_path": "native_worker_pipeline",
            "policy_classification_summary": policy_summary,
            "worker_context_updates": {
                "native_runtime": {
                    "runtime_path": "native_worker_pipeline",
                    "mode": "command_plan",
                    "context_hash": context_hash,
                    "ingress_request": ingress_request,
                    "command_plan_artifact": command_plan,
                    "policy_classification_summary": policy_summary,
                }
            },
        }

    def execute_and_verify_command(
        self,
        *,
        tid: str,
        task: dict[str, Any],
        command: str,
        trace_id: str,
        worker_profile: str | None,
        profile_source: str | None,
        timeout_seconds: int,
        workspace_dir: Path,
        native_runtime_payload: dict[str, Any] | None,
        agent_cfg: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_profile = normalize_execution_profile(worker_profile)
        normalized_profile_source = str(profile_source or "agent_default").strip().lower() or "agent_default"
        runtime_payload = dict(native_runtime_payload or {})
        command_plan = dict(runtime_payload.get("command_plan_artifact") or {})
        context_hash = str(runtime_payload.get("context_hash") or "").strip()
        if not context_hash:
            context_hash = _build_context_hash(
                context_bundle_id=str(
                    ((task.get("worker_execution_context") or {}).get("context_bundle_id") or task.get("context_bundle_id") or f"ctx-{tid}")
                ),
                task_id=tid,
                command=command,
            )
        shell_policy = self.shell_policy(agent_cfg=agent_cfg)

        if not command_plan:
            command_plan = build_command_plan_artifact(
                task_id=tid,
                capability_id="worker.command.plan",
                command=command,
                explanation="Native worker execute fallback plan.",
                expected_effects=["Execute bounded command and verify output."],
                policy=shell_policy,
                hub_policy_decision="allow",
                execution_profile=normalized_profile,
            )

        plan_ok, plan_degraded = validate_worker_schema_or_degraded(
            schema_name="command_plan_artifact.v1",
            payload=command_plan,
            direction="ingress",
        )
        if not plan_ok:
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="schema_invalid",
                degraded=plan_degraded,
                policy_classification_summary="schema_invalid:command_plan",
            )

        ingress_request = self._build_request_payload(
            tid=tid,
            goal_id=str(task.get("goal_id") or "goal-unknown"),
            trace_id=trace_id,
            capability_id="worker.command.execute",
            mode="command_execute",
            context_bundle_id=str(
                ((task.get("worker_execution_context") or {}).get("context_bundle_id") or task.get("context_bundle_id") or f"ctx-{tid}")
            ),
            context_hash=context_hash,
            policy_decision_ref={"decision_id": f"native-exec-{tid}", "decision": "allow", "policy_version": "native_worker_runtime_v1"},
            worker_profile=normalized_profile,
            profile_source=normalized_profile_source,
            requested_outputs=["test_result_artifact", "verification_artifact", "trace_metadata"],
        )
        ingress_ok, ingress_degraded = validate_worker_schema_or_degraded(
            schema_name="worker_execution_request.v1",
            payload=ingress_request,
            direction="ingress",
        )
        if not ingress_ok:
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="schema_invalid",
                degraded=ingress_degraded,
                policy_classification_summary="schema_invalid:ingress",
            )

        decision = classify_command(
            command=str(command_plan.get("command") or command),
            policy=shell_policy,
            hub_policy_decision="allow",
            execution_profile=normalized_profile,
        )
        policy_summary = f"{decision.classification}:{decision.reason}"

        try:
            test_result = execute_command_plan(
                repository_root=workspace_dir,
                command_plan_artifact=command_plan,
                task_id=tid,
                capability_id="worker.command.execute",
                context_hash=context_hash,
                shell_policy=shell_policy,
                hub_policy_decision="allow",
                approval=None,
                timeout_seconds=int(timeout_seconds),
                execution_profile=normalized_profile,
            )
        except PermissionError as exc:
            return self._permission_denied_outcome(
                tid=tid,
                trace_id=trace_id,
                error=str(exc),
                policy_classification_summary=policy_summary,
            )
        except Exception as exc:  # pragma: no cover - defensive branch for subprocess/environment issues
            degraded = build_degraded_state(
                state="unavailable_external_tool",
                machine_reason="runtime_failure",
                details={"error": str(exc)},
            )
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="runtime_failure",
                degraded=degraded,
                policy_classification_summary=policy_summary,
            )

        test_ok, test_degraded = validate_worker_schema_or_degraded(
            schema_name="test_result_artifact.v1",
            payload=test_result,
            direction="egress",
        )
        if not test_ok:
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="schema_invalid",
                degraded=test_degraded,
                policy_classification_summary=policy_summary,
            )

        verification_artifact = build_verification_artifact(task_id=tid, test_results=[test_result], patch_artifact=None)
        verify_ok, verify_degraded = validate_worker_schema_or_degraded(
            schema_name="verification_artifact.v1",
            payload=verification_artifact,
            direction="egress",
        )
        if not verify_ok:
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="schema_invalid",
                degraded=verify_degraded,
                policy_classification_summary=policy_summary,
            )

        status = "completed" if str(test_result.get("status") or "").strip().lower() == "passed" else "failed"
        failure_type = "success" if status == "completed" else "command_failure"
        execution_result = {
            "schema": "worker_execution_result.v1",
            "task_id": tid,
            "trace_id": trace_id,
            "capability_id": "worker.command.execute",
            "context_hash": context_hash,
            "worker_profile": normalized_profile,
            "profile_source": normalized_profile_source,
            "policy_decision_ref": {"decision_id": f"native-exec-{tid}", "decision": "allow", "policy_version": "native_worker_runtime_v1"},
            "status": status,
            "exit_code": int(test_result.get("exit_code") or 0),
            "stdout_ref": _bounded_text(str(test_result.get("stdout_ref") or "")),
            "stderr_ref": _bounded_text(str(test_result.get("stderr_ref") or "")),
            "model_metadata": {
                "provider": "native_worker",
                "model": "native_command_runtime",
                "prompt_template_version": "native_worker_runtime_v1",
            },
            "artifacts": [
                {"artifact_type": "command_plan_artifact", "artifact_ref": f"command:{str(command_plan.get('command_hash') or '')[:16]}"},
                {"artifact_type": "test_result_artifact", "artifact_ref": f"test:{status}:{int(test_result.get('exit_code') or 0)}"},
                {"artifact_type": "verification_artifact", "artifact_ref": f"verify:{str(verification_artifact.get('status') or 'unknown')}"},
            ],
        }
        result_ok, result_degraded = validate_worker_schema_or_degraded(
            schema_name="worker_execution_result.v1",
            payload=execution_result,
            direction="egress",
        )
        if not result_ok:
            return self._degraded_execution_outcome(
                tid=tid,
                trace_id=trace_id,
                failure_type="schema_invalid",
                degraded=result_degraded,
                policy_classification_summary=policy_summary,
            )

        output = "\n".join(
            [
                "Native worker command pipeline executed.",
                f"command={command_plan.get('command')}",
                f"status={status}",
                f"exit_code={test_result.get('exit_code')}",
                f"verification_status={verification_artifact.get('status')}",
                _bounded_text(str(test_result.get("output_summary") or ""), max_chars=1200),
            ]
        )
        return {
            "status": status,
            "exit_code": int(test_result.get("exit_code") or 0),
            "failure_type": failure_type,
            "output": output,
            "policy_classification_summary": policy_summary,
            "native_runtime": {
                "runtime_path": "native_worker_pipeline",
                "command_plan_artifact": command_plan,
                "test_result_artifact": test_result,
                "verification_artifact": verification_artifact,
                "execution_result": execution_result,
            },
            "artifact_refs": [
                {"kind": "native_worker_command_plan", "task_id": tid, "trace_bundle_ref": "native_worker_runtime:command_plan"},
                {"kind": "native_worker_test_result", "task_id": tid, "trace_bundle_ref": "native_worker_runtime:test_result"},
                {"kind": "native_worker_verification", "task_id": tid, "trace_bundle_ref": "native_worker_runtime:verification"},
            ],
            "approval_decision": {
                "classification": "allow",
                "reason_code": "native_worker_pipeline",
            },
        }

    @staticmethod
    def _permission_denied_outcome(*, tid: str, trace_id: str, error: str, policy_classification_summary: str) -> dict[str, Any]:
        marker = str(error or "").strip().lower()
        if marker.startswith("approval_"):
            degraded = build_degraded_state(
                state="missing_approval",
                machine_reason="missing_approval",
                details={"error": error},
            )
            status = "blocked"
            failure_type = "approval_required"
        elif "policy_denied" in marker:
            degraded = build_degraded_state(
                state="denied_policy",
                machine_reason="policy_denied",
                details={"error": error},
            )
            status = "failed"
            failure_type = "policy_denied"
        else:
            degraded = build_degraded_state(
                state="unsafe_command",
                machine_reason="unsafe_command",
                details={"error": error},
            )
            status = "failed"
            failure_type = "unsafe_command"
        return {
            "status": status,
            "exit_code": 1,
            "failure_type": failure_type,
            "output": json.dumps({"trace_id": trace_id, "degraded": degraded}, ensure_ascii=False),
            "policy_classification_summary": policy_classification_summary,
            "native_runtime": {"runtime_path": "native_worker_pipeline", "degraded": degraded},
            "artifact_refs": [{"kind": "native_worker_degraded_state", "task_id": tid, "trace_bundle_ref": "native_worker_runtime:degraded"}],
            "approval_decision": {"classification": "blocked", "reason_code": degraded.get("machine_reason")},
        }

    @staticmethod
    def _degraded_execution_outcome(
        *,
        tid: str,
        trace_id: str,
        failure_type: str,
        degraded: dict[str, Any] | None,
        policy_classification_summary: str,
    ) -> dict[str, Any]:
        degraded_payload = dict(degraded or {})
        return {
            "status": "failed",
            "exit_code": 1,
            "failure_type": str(failure_type or "degraded"),
            "output": json.dumps({"trace_id": trace_id, "degraded": degraded_payload}, ensure_ascii=False),
            "policy_classification_summary": policy_classification_summary,
            "native_runtime": {"runtime_path": "native_worker_pipeline", "degraded": degraded_payload},
            "artifact_refs": [{"kind": "native_worker_degraded_state", "task_id": tid, "trace_bundle_ref": "native_worker_runtime:degraded"}],
            "approval_decision": {"classification": "blocked", "reason_code": str(degraded_payload.get("machine_reason") or "degraded")},
        }

    @staticmethod
    def _build_request_payload(
        *,
        tid: str,
        goal_id: str,
        trace_id: str,
        capability_id: str,
        mode: str,
        context_bundle_id: str,
        context_hash: str,
        policy_decision_ref: dict[str, Any],
        worker_profile: str,
        profile_source: str,
        requested_outputs: list[str],
    ) -> dict[str, Any]:
        return {
            "schema": "worker_execution_request.v1",
            "task_id": str(tid).strip(),
            "goal_id": str(goal_id).strip() or "goal-unknown",
            "trace_id": str(trace_id).strip(),
            "capability_id": str(capability_id).strip(),
            "mode": str(mode).strip(),
            "context_envelope_ref": {
                "context_bundle_id": str(context_bundle_id).strip(),
                "context_hash": str(context_hash).strip(),
                "retrieval_refs": [{"source_id": "task_context", "path": f"tasks/{tid}", "reason": "task_scoped_execution"}],
                "context_chunk_limit": 32,
                "context_byte_limit": 120000,
            },
            "policy_decision_ref": dict(policy_decision_ref or {}),
            "workspace_constraints_ref": {"constraint_id": f"workspace-{str(tid).strip()}"},
            "worker_profile": str(worker_profile).strip(),
            "profile_source": str(profile_source).strip(),
            "requested_outputs": list(requested_outputs or []),
            "requested_state_on_policy_denied": "degraded",
            "requested_state_on_missing_approval": "degraded",
        }


native_worker_runtime_service = NativeWorkerRuntimeService()


def get_native_worker_runtime_service() -> NativeWorkerRuntimeService:
    return native_worker_runtime_service
