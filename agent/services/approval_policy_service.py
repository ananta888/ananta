from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.governance_modes import resolve_governance_mode
from agent.security_risk import classify_command_risk, classify_tool_calls_risk, max_risk_level

_ACTION_CLASSES = {"read_only", "mutation", "system_mutation", "install_remove", "admin_mutation"}
_DEFAULT_POLICY = {
    "enabled": True,
    "enforce_confirm_required": False,
    "governance_overrides": {
        "safe": {
            "confirm_required": ["mutation"],
            "blocked": ["system_mutation", "install_remove", "admin_mutation"],
        },
        "balanced": {
            "confirm_required": ["system_mutation", "install_remove"],
            "blocked": ["admin_mutation"],
        },
        "strict": {
            "confirm_required": ["mutation"],
            "blocked": ["system_mutation", "install_remove", "admin_mutation"],
        },
    },
}


@dataclass(frozen=True)
class ApprovalDecision:
    classification: str
    reason_code: str
    required_confirmation_level: str
    operation_class: str
    governance_mode: str
    enforced: bool
    policy_source: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "reason_code": self.reason_code,
            "required_confirmation_level": self.required_confirmation_level,
            "operation_class": self.operation_class,
            "governance_mode": self.governance_mode,
            "enforced": self.enforced,
            "policy_source": self.policy_source,
            "details": dict(self.details),
        }


class ApprovalPolicyService:
    """Unified approval check service for execution actions."""

    def normalize_policy(self, value: dict | None) -> dict[str, Any]:
        payload = dict(value or {})
        normalized = {
            "enabled": bool(payload.get("enabled", _DEFAULT_POLICY["enabled"])),
            "enforce_confirm_required": bool(payload.get("enforce_confirm_required", _DEFAULT_POLICY["enforce_confirm_required"])),
            "governance_overrides": {},
        }
        overrides = payload.get("governance_overrides") if isinstance(payload.get("governance_overrides"), dict) else {}
        for mode in ("safe", "balanced", "strict"):
            mode_cfg = overrides.get(mode) if isinstance(overrides.get(mode), dict) else {}
            default_cfg = _DEFAULT_POLICY["governance_overrides"][mode]
            confirm_required = self._normalize_action_classes(mode_cfg.get("confirm_required"), default_cfg["confirm_required"])
            blocked = self._normalize_action_classes(mode_cfg.get("blocked"), default_cfg["blocked"])
            normalized["governance_overrides"][mode] = {
                "confirm_required": confirm_required,
                "blocked": blocked,
            }
        return normalized

    def evaluate(
        self,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
        task: dict | None,
        agent_cfg: dict | None,
    ) -> ApprovalDecision:
        cfg = dict(agent_cfg or {})
        policy = self.normalize_policy(cfg.get("unified_approval_policy"))
        governance_mode = str(resolve_governance_mode(cfg) or "balanced").strip().lower()
        if governance_mode not in {"safe", "balanced", "strict"}:
            governance_mode = "balanced"
        operation_class = self._classify_operation(command=command, tool_calls=tool_calls, cfg=cfg)
        risk_level = max_risk_level(classify_command_risk(command), classify_tool_calls_risk(tool_calls, guard_cfg=cfg))
        mode_policy = policy["governance_overrides"][governance_mode]

        classification = "allow"
        reason = "approval_not_required"
        confirmation_level = "none"
        if operation_class in set(mode_policy.get("blocked") or []):
            classification = "blocked"
            reason = f"approval_blocked:{operation_class}"
            confirmation_level = "admin"
        elif operation_class in set(mode_policy.get("confirm_required") or []):
            classification = "confirm_required"
            reason = f"approval_confirmation_required:{operation_class}"
            confirmation_level = "operator"

        if risk_level == "critical":
            classification = "blocked"
            reason = "approval_blocked:critical_risk"
            confirmation_level = "admin"

        specialized = self._resolve_specialized_profile(task=task, cfg=cfg)
        specialized_backend_id = str((specialized or {}).get("backend_id") or "").strip() or None
        specialized_profile = dict((specialized or {}).get("profile") or {})
        specialized_risk_class = str(specialized_profile.get("risk_class") or "").strip().lower() or None
        specialized_requires_approval = bool(specialized_profile.get("requires_approval", False))
        if specialized_backend_id and classification != "blocked" and specialized_requires_approval:
            classification = "confirm_required"
            reason = f"approval_confirmation_required:specialized_backend:{specialized_backend_id}"
            confirmation_level = "operator"
        if specialized_backend_id and specialized_risk_class == "high" and governance_mode in {"safe", "strict"}:
            classification = "blocked"
            reason = "approval_blocked:specialized_backend_high_risk"
            confirmation_level = "admin"

        approval_confirmed = bool((task or {}).get("approval_confirmed"))
        enforced = bool(policy.get("enabled", True)) and (
            classification == "blocked" or (classification == "confirm_required" and bool(policy.get("enforce_confirm_required")))
        )
        if classification == "confirm_required" and approval_confirmed:
            classification = "allow"
            reason = "approval_confirmed_by_operator"
            confirmation_level = "none"
            enforced = False

        return ApprovalDecision(
            classification=classification,
            reason_code=reason,
            required_confirmation_level=confirmation_level,
            operation_class=operation_class,
            governance_mode=governance_mode,
            enforced=enforced,
            policy_source="agent_config.unified_approval_policy_or_default",
            details={
                "risk_level": risk_level,
                "approval_confirmed": approval_confirmed,
                "policy": policy,
                "task_id": str((task or {}).get("id") or "").strip() or None,
                "specialized_backend": {
                    "backend_id": specialized_backend_id,
                    "risk_class": specialized_risk_class,
                    "requires_approval": specialized_requires_approval,
                }
                if specialized_backend_id
                else None,
            },
        )

    @staticmethod
    def _normalize_action_classes(raw: Any, default: list[str]) -> list[str]:
        if not isinstance(raw, list):
            return list(default)
        values: list[str] = []
        for item in raw:
            value = str(item or "").strip().lower()
            if value not in _ACTION_CLASSES or value in values:
                continue
            values.append(value)
        return values or list(default)

    @staticmethod
    def _tool_class_map(cfg: dict[str, Any]) -> dict[str, str]:
        payload = (((cfg or {}).get("llm_tool_guardrails") or {}).get("tool_classes"))
        raw = payload if isinstance(payload, dict) else {}
        result: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key or "").strip()
            if not name:
                continue
            tool_class = str(value or "unknown").strip().lower()
            if tool_class not in {"read", "write", "admin", "unknown"}:
                tool_class = "unknown"
            result[name] = tool_class
        return result

    def _classify_operation(self, *, command: str | None, tool_calls: list[dict] | None, cfg: dict[str, Any]) -> str:
        command_text = str(command or "").strip().lower()
        if command_text:
            if any(
                token in command_text
                for token in ("pip install", "pip uninstall", "npm install", "apt install", "apt remove", "brew install", "brew uninstall")
            ):
                return "install_remove"
            if any(token in command_text for token in ("rm -rf", "shutdown", "reboot", "systemctl")):
                return "system_mutation"
            if any(token in command_text for token in ("sed -i", "chmod ", "chown ", "mv ", "cp ", "tee ")):
                return "mutation"
            return "read_only"

        classes = [self._tool_class_map(cfg).get(str((item or {}).get("name") or "").strip(), "unknown") for item in list(tool_calls or []) if isinstance(item, dict)]
        if "admin" in classes:
            return "admin_mutation"
        if "write" in classes:
            return "mutation"
        return "read_only"

    @staticmethod
    def _specialized_profiles(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
        payload = cfg.get("specialized_worker_profiles") if isinstance(cfg.get("specialized_worker_profiles"), dict) else {}
        if not bool(payload.get("enabled", False)):
            return {}
        profiles = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
        result: dict[str, dict[str, Any]] = {}
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or not bool(profile.get("enabled", False)):
                continue
            result[str(profile_id).strip().lower()] = profile
            for alias in list(profile.get("routing_aliases") or []):
                alias_id = str(alias or "").strip().lower()
                if alias_id and alias_id not in result:
                    result[alias_id] = profile
        return result

    def _resolve_specialized_profile(self, *, task: dict | None, cfg: dict[str, Any]) -> dict[str, Any] | None:
        task_payload = dict(task or {})
        proposal = dict(task_payload.get("last_proposal") or {})
        routing = dict(proposal.get("routing") or {})
        backend_candidates = [
            str(task_payload.get("preferred_backend") or "").strip().lower(),
            str(task_payload.get("backend") or "").strip().lower(),
            str(routing.get("effective_backend") or "").strip().lower(),
            str(proposal.get("backend") or "").strip().lower(),
        ]
        backend_id = next((value for value in backend_candidates if value), None)
        if not backend_id:
            return None
        profiles = self._specialized_profiles(cfg)
        profile = profiles.get(backend_id)
        if not profile:
            return None
        return {"backend_id": backend_id, "profile": profile}


approval_policy_service = ApprovalPolicyService()


def get_approval_policy_service() -> ApprovalPolicyService:
    return approval_policy_service
