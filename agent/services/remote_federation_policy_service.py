from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TRUST_LEVELS = ("untrusted", "partner", "trusted-internal", "local-equivalent")

_DEFAULT_POLICY = {
    "enabled": True,
    "default_trust_level": "partner",
    "allowed_operations": ["models", "chat"],
    "allow_artifact_access": False,
    "allow_file_access": False,
    "require_provenance": True,
    "max_hops": 3,
}


@dataclass(frozen=True)
class RemoteFederationDecision:
    allowed: bool
    reason: str
    policy: dict[str, Any]


def _normalize_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item or "").strip() for item in raw if str(item or "").strip()]


def _positive_int(raw: Any, default: int) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


class RemoteFederationPolicyService:
    """Normalizes trust boundaries for Remote-Ananta backends."""

    def normalize_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        trust_level = str(raw.get("default_trust_level") or _DEFAULT_POLICY["default_trust_level"]).strip().lower()
        if trust_level not in TRUST_LEVELS:
            trust_level = _DEFAULT_POLICY["default_trust_level"]
        operations = _normalize_list(raw.get("allowed_operations")) or list(_DEFAULT_POLICY["allowed_operations"])
        return {
            "enabled": bool(raw.get("enabled", _DEFAULT_POLICY["enabled"])),
            "default_trust_level": trust_level,
            "allowed_operations": operations,
            "allow_artifact_access": bool(raw.get("allow_artifact_access", _DEFAULT_POLICY["allow_artifact_access"])),
            "allow_file_access": bool(raw.get("allow_file_access", _DEFAULT_POLICY["allow_file_access"])),
            "require_provenance": bool(raw.get("require_provenance", _DEFAULT_POLICY["require_provenance"])),
            "max_hops": _positive_int(raw.get("max_hops"), int(_DEFAULT_POLICY["max_hops"])),
        }

    def resolve_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        return self.normalize_policy(cfg.get("remote_federation_policy") if isinstance(cfg.get("remote_federation_policy"), dict) else {})

    def normalize_backend(self, backend: dict[str, Any], *, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = self.resolve_policy(cfg)
        raw_trust = str(backend.get("trust_level") or policy["default_trust_level"]).strip().lower()
        trust_level = raw_trust if raw_trust in TRUST_LEVELS else policy["default_trust_level"]
        operations = _normalize_list(backend.get("allowed_operations")) or list(policy["allowed_operations"])
        return {
            "trust_level": trust_level,
            "allowed_operations": operations,
            "allowed_roles": _normalize_list(backend.get("allowed_roles")),
            "allowed_capabilities": _normalize_list(backend.get("allowed_capabilities")),
            "allow_artifact_access": bool(backend.get("allow_artifact_access", policy["allow_artifact_access"])),
            "allow_file_access": bool(backend.get("allow_file_access", policy["allow_file_access"])),
            "require_provenance": bool(backend.get("require_provenance", policy["require_provenance"])),
            "max_hops": _positive_int(backend.get("max_hops", policy["max_hops"]), int(policy["max_hops"])),
        }

    def evaluate(
        self,
        *,
        backend_policy: dict[str, Any],
        operation: str,
        hop_count: int | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> RemoteFederationDecision:
        policy = dict(backend_policy or {})
        op = str(operation or "").strip().lower()
        if op not in set(policy.get("allowed_operations") or []):
            return RemoteFederationDecision(False, "remote_operation_not_allowed", policy)
        if op in {"artifact", "artifacts"} and not bool(policy.get("allow_artifact_access", False)):
            return RemoteFederationDecision(False, "remote_artifact_access_disabled", policy)
        if op in {"file", "files"} and not bool(policy.get("allow_file_access", False)):
            return RemoteFederationDecision(False, "remote_file_access_disabled", policy)
        if hop_count is not None and int(hop_count) > int(policy.get("max_hops") or 3):
            return RemoteFederationDecision(False, "remote_max_hops_exceeded", policy)
        if bool(policy.get("require_provenance", True)) and not provenance:
            return RemoteFederationDecision(False, "remote_provenance_required", policy)
        return RemoteFederationDecision(True, "ok", policy)

    def provenance_headers(self, *, local_instance_id: str | None, trace_id: str | None, hop_count: int = 0) -> dict[str, str]:
        next_hop = max(1, int(hop_count or 0) + 1)
        headers = {"X-Ananta-Hop-Count": str(next_hop)}
        if local_instance_id:
            headers["X-Ananta-Instance-ID"] = str(local_instance_id)
        if trace_id:
            headers["X-Ananta-Trace-ID"] = str(trace_id)
        return headers


remote_federation_policy_service = RemoteFederationPolicyService()


def get_remote_federation_policy_service() -> RemoteFederationPolicyService:
    return remote_federation_policy_service
