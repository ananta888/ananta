from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent.governance_modes import resolve_governance_mode
from agent.security_risk import classify_command_risk, classify_tool_calls_risk, max_risk_level

if TYPE_CHECKING:
    from agent.services.shell_command_policy import CommandChainAnalysisResult

_ACTION_CLASSES = {"read_only", "mutation", "system_mutation", "install_remove", "admin_mutation"}
_DEFAULT_POLICY = {
    "enabled": False,
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
        command_analysis: "CommandChainAnalysisResult | None" = None,
        approval_context: dict | None = None,
    ) -> ApprovalDecision:
        cfg = dict(agent_cfg or {})
        policy = self.normalize_policy(cfg.get("unified_approval_policy"))
        governance_mode = str((resolve_governance_mode(cfg) or {}).get("effective") or "balanced").strip().lower()
        if governance_mode not in {"safe", "balanced", "strict"}:
            governance_mode = "balanced"

        # SCG-007: segment-aware operation classification for chain commands
        seg_op_classes: list[dict[str, Any]] = []
        if command_analysis is not None and command_analysis.contains_chain and command_analysis.allowed:
            raw_classes = [self._classify_operation(command=seg.raw, tool_calls=None, cfg=cfg) for seg in command_analysis.segments]
            operation_class = self._aggregate_operation_classes(raw_classes)
            seg_op_classes = [{"index": i + 1, "class": c} for i, c in enumerate(raw_classes)]
            risk_level = max_risk_level(
                *[classify_command_risk(seg.raw) for seg in command_analysis.segments],
                classify_tool_calls_risk(tool_calls, guard_cfg=cfg),
            )
        else:
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

        # ALWA-005: digest-bound grant resolution comes first — a persisted
        # ApprovalRequest grant for exactly this canonicalized call turns
        # confirm_required into allow.
        granted_request_details: dict[str, Any] | None = None
        if classification == "confirm_required" and approval_context:
            granted_request_details = self._resolve_request_grant(approval_context=approval_context, task=task)
            if granted_request_details is not None:
                classification = "allow"
                reason = "approval_granted_by_request"
                confirmation_level = "none"
                enforced = False

        # ALWA-004: task.approval_confirmed is legacy-only. It applies only
        # while the backward-compatibility policy is enabled and is audited
        # as approval_legacy_bypass_used whenever it actually bypasses.
        lifecycle_cfg = dict(cfg.get("approval_lifecycle") or {})
        legacy_enabled = bool(lifecycle_cfg.get("legacy_approval_confirmed_enabled", True))
        if classification == "confirm_required" and approval_confirmed and legacy_enabled:
            classification = "allow"
            reason = "approval_confirmed_by_operator"
            confirmation_level = "none"
            enforced = False
            self._audit_legacy_bypass(task=task, operation_class=operation_class)

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
                **({"approval_request": granted_request_details} if granted_request_details else {}),
                **({"segment_operation_classes": seg_op_classes} if seg_op_classes else {}),
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
    def _resolve_request_grant(*, approval_context: dict, task: dict | None) -> dict[str, Any] | None:
        """ALWA-005: look up a digest-bound grant for the requested call.

        Returns content-free details (request_id, digest_prefix,
        scope_summary, reason_code) or None. A wrong digest, wrong scope or
        an expired grant resolves to None — the classification then stays
        confirm_required/blocked.
        """
        try:
            from agent.services.approval_request_service import (
                digest_prefix,
                get_approval_request_service,
            )

            svc = get_approval_request_service()
            grant = svc.resolve_grant_for_call(
                tool_name=str(approval_context.get("tool_name") or ""),
                arguments=approval_context.get("arguments") if isinstance(approval_context.get("arguments"), dict) else {},
                task_id=str(approval_context.get("task_id") or (task or {}).get("id") or "").strip() or None,
                goal_id=str(approval_context.get("goal_id") or (task or {}).get("goal_id") or "").strip() or None,
                target_fingerprint=str(approval_context.get("target_fingerprint") or "").strip() or None,
            )
            if grant is None:
                return None
            return {
                "request_id": grant.id,
                "digest_prefix": digest_prefix(grant.arguments_digest),
                "scope_summary": {k: v for k, v in dict(grant.scope or {}).items() if k in {"approval_class", "pre_approval", "goal_id"}},
                "reason_code": "approval_granted_by_request",
            }
        except Exception:
            return None

    @staticmethod
    def _audit_legacy_bypass(*, task: dict | None, operation_class: str) -> None:
        try:
            from agent.services.approval_request_service import AUDIT_APPROVAL_LEGACY_BYPASS_USED
            from agent.common.audit import log_audit

            log_audit(
                AUDIT_APPROVAL_LEGACY_BYPASS_USED,
                {
                    "task_id": str((task or {}).get("id") or "").strip() or None,
                    "goal_id": str((task or {}).get("goal_id") or "").strip() or None,
                    "operation_class": operation_class,
                    "warning": "legacy task.approval_confirmed bypassed confirm_required",
                },
            )
        except Exception:
            pass

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
    def _aggregate_operation_classes(classes: list[str]) -> str:
        """Return the most privileged operation class across a list."""
        rank = {"read_only": 0, "mutation": 1, "install_remove": 2, "system_mutation": 3, "admin_mutation": 4}
        return max(classes, key=lambda c: rank.get(c, 0), default="read_only")

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
