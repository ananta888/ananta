from __future__ import annotations

import hashlib
import json
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.services.approval_policy_service import ApprovalDecision
from agent.services.execution_risk_policy_service import ExecutionRiskDecision
from agent.services.tool_intent_taxonomy_service import get_tool_intent_taxonomy_service


@dataclass(frozen=True)
class MutationGateDecision:
    classification: str  # allow | confirm_required | blocked
    reason_code: str
    mutation_class: str
    normalized_target: dict[str, Any]
    approval_scope: dict[str, Any]
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "reason_code": self.reason_code,
            "mutation_class": self.mutation_class,
            "normalized_target": dict(self.normalized_target),
            "approval_scope": dict(self.approval_scope),
            "details": dict(self.details),
        }


class MutationGateService:
    """Central mutation boundary before write-like execution paths."""

    def evaluate(
        self,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
        task: dict | None,
        agent_cfg: dict | None,
        approval_decision: ApprovalDecision | dict | None,
        risk_decision: ExecutionRiskDecision | dict | None,
        trace_id: str | None = None,
        actor: str | None = None,
    ) -> MutationGateDecision:
        cfg = dict((agent_cfg or {}).get("mutation_gate") or {})
        if not bool(cfg.get("enabled", True)):
            return MutationGateDecision(
                classification="allow",
                reason_code="mutation_gate_disabled",
                mutation_class="read_only",
                normalized_target={},
                approval_scope={},
                details={"enabled": False},
            )

        approval_payload = self._approval_payload(approval_decision)
        risk_payload = self._risk_payload(risk_decision)
        mutation_class = self.classify_mutation_class(command=command, tool_calls=tool_calls, approval_payload=approval_payload)
        normalized_target = self.normalize_target(command=command, tool_calls=tool_calls, task=task)
        is_mutation = mutation_class != "read_only"
        scope = self._approval_scope(task=task, trace_id=trace_id, actor=actor)
        governance_mode = str(
            approval_payload.get("governance_mode")
            or (agent_cfg or {}).get("governance_mode")
            or "balanced"
        ).strip().lower()
        operation_class = str(approval_payload.get("operation_class") or "read_only").strip().lower()

        if not is_mutation:
            return MutationGateDecision(
                classification="allow",
                reason_code="mutation_gate_not_required",
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"enabled": True},
            )
        if governance_mode in {"safe", "strict"} and operation_class == "read_only":
            return MutationGateDecision(
                classification="blocked",
                reason_code="mutation_gate_unknown_high_risk_classification",
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"blocked_by": "mutation_gate_hardening", "governance_mode": governance_mode},
            )

        if approval_payload.get("classification") == "blocked" and bool(approval_payload.get("enforced", False)):
            return MutationGateDecision(
                classification="blocked",
                reason_code=str(approval_payload.get("reason_code") or "approval_blocked"),
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"blocked_by": "approval_policy"},
            )
        if not bool(risk_payload.get("allowed", True)):
            reasons = list(risk_payload.get("reasons") or [])
            return MutationGateDecision(
                classification="blocked",
                reason_code=str(reasons[0] if reasons else "execution_risk_denied"),
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"blocked_by": "execution_risk_policy", "risk_level": risk_payload.get("risk_level")},
            )

        scoped = self._validate_scoped_approval(task=task, mutation_class=mutation_class, normalized_target=normalized_target, trace_id=trace_id, actor=actor)
        if not scoped["ok"] and scoped["present"]:
            return MutationGateDecision(
                classification="blocked",
                reason_code=str(scoped["reason_code"]),
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"blocked_by": "scoped_approval", "scope_check": scoped},
            )

        if approval_payload.get("classification") == "confirm_required":
            if scoped["ok"]:
                return MutationGateDecision(
                    classification="allow",
                    reason_code="mutation_scope_approved",
                    mutation_class=mutation_class,
                    normalized_target=normalized_target,
                    approval_scope=scope,
                    details={"approval_binding": "scoped"},
                )
            if bool((task or {}).get("approval_confirmed")):
                return MutationGateDecision(
                    classification="allow",
                    reason_code="mutation_approved_legacy_task_scope",
                    mutation_class=mutation_class,
                    normalized_target=normalized_target,
                    approval_scope=scope,
                    details={"approval_binding": "legacy_task_approval"},
                )
            return MutationGateDecision(
                classification="confirm_required",
                reason_code="mutation_scope_confirmation_required",
                mutation_class=mutation_class,
                normalized_target=normalized_target,
                approval_scope=scope,
                details={"scope_check": scoped},
            )

        return MutationGateDecision(
            classification="allow",
            reason_code="mutation_gate_allow",
            mutation_class=mutation_class,
            normalized_target=normalized_target,
            approval_scope=scope,
            details={},
        )

    def classify_mutation_class(
        self,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
        approval_payload: dict[str, Any] | None = None,
    ) -> str:
        command_text = str(command or "").strip().lower()
        if command_text:
            if any(token in command_text for token in ("pip install", "pip uninstall", "npm install", "apt install", "apt remove", "brew install", "brew uninstall")):
                return "install_remove"
            if any(token in command_text for token in ("apply_patch", "git apply", "patch -p")):
                return "patch_apply"
            if any(token in command_text for token in ("rm -rf", "shutdown", "reboot", "systemctl", "sudo ")):
                return "system_mutation"
            if any(token in command_text for token in ("sed -i", "chmod ", "chown ", "mv ", "cp ", "tee ", "cat >")):
                return "file_write"

        for item in list(tool_calls or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("tool_name") or "").strip()
            if not name:
                continue
            taxonomy = get_tool_intent_taxonomy_service().classify_tool(name)
            intent = str(taxonomy.get("intent") or "").strip()
            if intent == "file_write":
                return "file_write"
            if intent == "shell_command":
                return "shell_write_effect"
            lowered = name.lower()
            if "evolution" in lowered and "apply" in lowered:
                return "patch_apply"
            if "artifact" in lowered:
                return "artifact_mutation"
            if "task" in lowered and any(part in lowered for part in ("update", "patch", "set_", "review", "status")):
                return "task_state_mutation"
            if "repair" in lowered:
                return "repair_action"

        operation_class = str((approval_payload or {}).get("operation_class") or "").strip().lower()
        fallback = {
            "admin_mutation": "admin_mutation",
            "system_mutation": "system_mutation",
            "install_remove": "install_remove",
            "mutation": "file_write",
            "read_only": "read_only",
        }
        return fallback.get(operation_class, "read_only")

    def normalize_target(self, *, command: str | None, tool_calls: list[dict] | None, task: dict | None) -> dict[str, Any]:
        task_payload = dict(task or {})
        base_dir = str((task_payload.get("worker_execution_context") or {}).get("cwd") or task_payload.get("working_directory") or Path.cwd())
        target: dict[str, Any] = {
            "target_type": "none",
            "path": None,
            "artifact_id": None,
            "task_id": str(task_payload.get("id") or "").strip() or None,
            "service_name": None,
            "system_resource": None,
            "project_scope": str(task_payload.get("goal_id") or "").strip() or None,
        }
        candidate_path = self._extract_command_path(command, base_dir=base_dir)
        if candidate_path:
            target["target_type"] = "path"
            target["path"] = candidate_path
        for item in list(tool_calls or []):
            if not isinstance(item, dict):
                continue
            args = item.get("args") or item.get("tool_input") or item.get("parameters") or {}
            if not isinstance(args, dict):
                continue
            for key in ("path", "file", "file_path", "target", "target_path"):
                value = str(args.get(key) or "").strip()
                if value:
                    target["target_type"] = "path"
                    target["path"] = self._normalize_path(value, base_dir=base_dir)
                    break
            artifact_id = str(args.get("artifact_id") or "").strip()
            if artifact_id:
                target["target_type"] = "artifact"
                target["artifact_id"] = artifact_id
            service_name = str(args.get("service") or args.get("service_name") or "").strip()
            if service_name:
                target["service_name"] = service_name
            system_resource = str(args.get("resource") or args.get("system_resource") or "").strip()
            if system_resource:
                target["system_resource"] = system_resource

        fingerprint_payload = {
            "target_type": target["target_type"],
            "path": target["path"],
            "artifact_id": target["artifact_id"],
            "task_id": target["task_id"],
            "service_name": target["service_name"],
            "system_resource": target["system_resource"],
            "project_scope": target["project_scope"],
        }
        target["target_fingerprint"] = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        return target

    @staticmethod
    def _approval_payload(approval_decision: ApprovalDecision | dict | None) -> dict[str, Any]:
        if approval_decision is None:
            return {}
        if isinstance(approval_decision, dict):
            return dict(approval_decision)
        return approval_decision.as_dict()

    @staticmethod
    def _risk_payload(risk_decision: ExecutionRiskDecision | dict | None) -> dict[str, Any]:
        if risk_decision is None:
            return {"allowed": True, "reasons": [], "risk_level": "low"}
        if isinstance(risk_decision, dict):
            return dict(risk_decision)
        return {
            "allowed": bool(risk_decision.allowed),
            "reasons": list(risk_decision.reasons or []),
            "risk_level": str(risk_decision.risk_level or "low"),
        }

    @staticmethod
    def _normalize_path(raw: str, *, base_dir: str) -> str:
        candidate = Path(raw)
        if candidate.is_absolute():
            return str(candidate.resolve())
        return str((Path(base_dir) / candidate).resolve())

    def _extract_command_path(self, command: str | None, *, base_dir: str) -> str | None:
        text = str(command or "").strip()
        if not text:
            return None
        try:
            parts = shlex.split(text, posix=True)
        except ValueError:
            return None
        if not parts:
            return None
        ignored = {"bash", "sh", "python", "python3", "node", "npm", "git", "apt", "pip", "sudo", "echo", "cat", "tee", "mv", "cp", "chmod", "chown", "sed"}
        for token in parts[1:]:
            value = str(token or "").strip()
            if not value or value.startswith("-") or value in {"&&", "||", ";"}:
                continue
            if value in ignored:
                continue
            if "/" in value or value.startswith(".") or value.startswith("~"):
                return self._normalize_path(value.replace("~", str(Path.home()), 1), base_dir=base_dir)
        return None

    @staticmethod
    def _approval_scope(*, task: dict | None, trace_id: str | None, actor: str | None) -> dict[str, Any]:
        scoped = dict((task or {}).get("mutation_approval") or {})
        return {
            "task_id": str((task or {}).get("id") or "").strip() or None,
            "trace_id": trace_id or None,
            "actor": actor or None,
            "mutation_classes": list(scoped.get("mutation_classes") or []),
            "expires_at": scoped.get("expires_at"),
        }

    def _validate_scoped_approval(
        self,
        *,
        task: dict | None,
        mutation_class: str,
        normalized_target: dict[str, Any],
        trace_id: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        scoped = dict((task or {}).get("mutation_approval") or {})
        if not scoped:
            return {"ok": False, "present": False, "reason_code": "mutation_scope_missing"}
        expires_at = scoped.get("expires_at")
        if expires_at is None:
            return {"ok": False, "present": True, "reason_code": "mutation_scope_incomplete:expires_at_missing"}
        try:
            if float(expires_at) < time.time():
                return {"ok": False, "present": True, "reason_code": "mutation_scope_expired"}
        except (TypeError, ValueError):
            return {"ok": False, "present": True, "reason_code": "mutation_scope_invalid:expires_at"}

        expected_task_id = str(scoped.get("task_id") or "").strip()
        if expected_task_id and expected_task_id != str((task or {}).get("id") or "").strip():
            return {"ok": False, "present": True, "reason_code": "mutation_scope_mismatch:task_id"}
        expected_trace_id = str(scoped.get("trace_id") or "").strip()
        if expected_trace_id and expected_trace_id != str(trace_id or "").strip():
            return {"ok": False, "present": True, "reason_code": "mutation_scope_mismatch:trace_id"}
        expected_actor = str(scoped.get("actor") or "").strip()
        if expected_actor and expected_actor != str(actor or "").strip():
            return {"ok": False, "present": True, "reason_code": "mutation_scope_mismatch:actor"}

        allowed_classes = [str(item).strip().lower() for item in list(scoped.get("mutation_classes") or []) if str(item).strip()]
        if allowed_classes and mutation_class.lower() not in set(allowed_classes):
            return {"ok": False, "present": True, "reason_code": "mutation_scope_mismatch:class"}

        expected_fingerprint = str(scoped.get("target_fingerprint") or "").strip()
        actual_fingerprint = str(normalized_target.get("target_fingerprint") or "").strip()
        if expected_fingerprint and expected_fingerprint != actual_fingerprint:
            return {"ok": False, "present": True, "reason_code": "mutation_scope_mismatch:target"}

        return {"ok": True, "present": True, "reason_code": "mutation_scope_ok"}


_mutation_gate_service = MutationGateService()


def get_mutation_gate_service() -> MutationGateService:
    return _mutation_gate_service
