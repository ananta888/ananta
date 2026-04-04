from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenAICompatAccessDecision:
    allowed: bool
    reason: str
    auth_source: str
    policy: dict[str, Any]


class ExposurePolicyService:
    """Resolves and enforces explicit exposure policies for external API adapters."""

    _OPENAI_COMPAT_DEFAULTS = {
        "enabled": True,
        "allow_agent_auth": True,
        "allow_user_auth": True,
        "require_admin_for_user_auth": True,
        "allow_files_api": True,
        "emit_audit_events": True,
        "instance_id": None,
        "max_hops": 3,
    }
    _MCP_DEFAULTS = {
        "enabled": False,
        "allow_agent_auth": False,
        "allow_user_auth": False,
        "require_admin_for_user_auth": True,
    }

    def _normalize_openai_compat_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw or {}
        max_hops_raw = raw.get("max_hops", self._OPENAI_COMPAT_DEFAULTS["max_hops"])
        try:
            max_hops = int(max_hops_raw)
        except (TypeError, ValueError):
            max_hops = int(self._OPENAI_COMPAT_DEFAULTS["max_hops"])
        max_hops = max(1, max_hops)
        return {
            "enabled": bool(raw.get("enabled", self._OPENAI_COMPAT_DEFAULTS["enabled"])),
            "allow_agent_auth": bool(raw.get("allow_agent_auth", self._OPENAI_COMPAT_DEFAULTS["allow_agent_auth"])),
            "allow_user_auth": bool(raw.get("allow_user_auth", self._OPENAI_COMPAT_DEFAULTS["allow_user_auth"])),
            "require_admin_for_user_auth": bool(
                raw.get("require_admin_for_user_auth", self._OPENAI_COMPAT_DEFAULTS["require_admin_for_user_auth"])
            ),
            "allow_files_api": bool(raw.get("allow_files_api", self._OPENAI_COMPAT_DEFAULTS["allow_files_api"])),
            "emit_audit_events": bool(raw.get("emit_audit_events", self._OPENAI_COMPAT_DEFAULTS["emit_audit_events"])),
            "instance_id": str(raw.get("instance_id") or "").strip() or None,
            "max_hops": max_hops,
        }

    def _normalize_mcp_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw or {}
        return {
            "enabled": bool(raw.get("enabled", self._MCP_DEFAULTS["enabled"])),
            "allow_agent_auth": bool(raw.get("allow_agent_auth", self._MCP_DEFAULTS["allow_agent_auth"])),
            "allow_user_auth": bool(raw.get("allow_user_auth", self._MCP_DEFAULTS["allow_user_auth"])),
            "require_admin_for_user_auth": bool(
                raw.get("require_admin_for_user_auth", self._MCP_DEFAULTS["require_admin_for_user_auth"])
            ),
        }

    def normalize_exposure_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        openai_compat = raw.get("openai_compat") if isinstance(raw.get("openai_compat"), dict) else {}
        mcp = raw.get("mcp") if isinstance(raw.get("mcp"), dict) else {}
        return {
            "openai_compat": self._normalize_openai_compat_policy(openai_compat),
            "mcp": self._normalize_mcp_policy(mcp),
        }

    def resolve_openai_compat_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        normalized = self.normalize_exposure_policy((cfg or {}).get("exposure_policy"))
        return normalized["openai_compat"]

    @staticmethod
    def resolve_auth_source(*, is_agent_auth: bool, is_user_auth: bool) -> str:
        if is_agent_auth:
            return "agent_auth"
        if is_user_auth:
            return "user_jwt"
        return "unknown"

    def evaluate_openai_compat_access(
        self,
        *,
        cfg: dict[str, Any] | None,
        is_agent_auth: bool,
        is_user_auth: bool,
        is_admin: bool,
        endpoint_group: str = "core",
        caller_instance_id: str | None = None,
        local_instance_id: str | None = None,
        hop_count: int | None = None,
    ) -> OpenAICompatAccessDecision:
        policy = self.resolve_openai_compat_policy(cfg)
        auth_source = self.resolve_auth_source(is_agent_auth=is_agent_auth, is_user_auth=is_user_auth)
        effective_local_instance = str(policy.get("instance_id") or local_instance_id or "").strip() or None
        effective_caller_instance = str(caller_instance_id or "").strip() or None
        if not policy["enabled"]:
            return OpenAICompatAccessDecision(False, "openai_compat_exposure_disabled", auth_source, policy)
        if effective_local_instance and effective_caller_instance and effective_local_instance == effective_caller_instance:
            return OpenAICompatAccessDecision(False, "openai_compat_self_call_blocked", auth_source, policy)
        if hop_count is not None and int(hop_count) > int(policy.get("max_hops") or 3):
            return OpenAICompatAccessDecision(False, "openai_compat_max_hops_exceeded", auth_source, policy)
        if endpoint_group == "files" and not policy["allow_files_api"]:
            return OpenAICompatAccessDecision(False, "openai_compat_files_api_disabled", auth_source, policy)
        if is_agent_auth and not policy["allow_agent_auth"]:
            return OpenAICompatAccessDecision(False, "openai_compat_agent_auth_disabled", auth_source, policy)
        if is_user_auth:
            if not policy["allow_user_auth"]:
                return OpenAICompatAccessDecision(False, "openai_compat_user_auth_disabled", auth_source, policy)
            if policy["require_admin_for_user_auth"] and not is_admin:
                return OpenAICompatAccessDecision(False, "openai_compat_admin_required", auth_source, policy)
        if auth_source == "unknown":
            return OpenAICompatAccessDecision(False, "openai_compat_auth_source_unknown", auth_source, policy)
        return OpenAICompatAccessDecision(True, "ok", auth_source, policy)


exposure_policy_service = ExposurePolicyService()


def get_exposure_policy_service() -> ExposurePolicyService:
    return exposure_policy_service
