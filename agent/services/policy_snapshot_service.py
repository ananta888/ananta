"""PolicySnapshotService — TRANS-002

Unveränderlicher Snapshot der Policy-Konfiguration zum Run-Startzeitpunkt.
Fehlender PolicySnapshot blockiert Worker-Ausführung.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

try:
    from agent.config import settings as _default_settings
except ImportError:
    _default_settings = None


@dataclass
class PolicySnapshot:
    snapshot_id: str
    run_id: str
    policy_scope_id: str | None
    allowed_paths: list[str]
    denied_paths: list[str]
    allowed_tools: list[str]
    denied_tools: list[str]
    allowed_providers: list[str]
    model_policy: str        # "local_only" | "any" | "allowlist"
    network_policy: str      # "deny_all" | "restricted" | "allowed"
    write_policy: str        # "proposal_only" | "approval_required" | "unrestricted"
    approval_gates: list[str]
    created_at: float
    config_hash: str         # sha256 computed after creation

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "policy_scope_id": self.policy_scope_id,
            "allowed_paths": list(self.allowed_paths),
            "denied_paths": list(self.denied_paths),
            "allowed_tools": list(self.allowed_tools),
            "denied_tools": list(self.denied_tools),
            "allowed_providers": list(self.allowed_providers),
            "model_policy": self.model_policy,
            "network_policy": self.network_policy,
            "write_policy": self.write_policy,
            "approval_gates": list(self.approval_gates),
            "created_at": self.created_at,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_settings(
        cls,
        run_id: str,
        *,
        settings_obj: Any = None,
        policy_scope_id: str | None = None,
    ) -> "PolicySnapshot":
        """Create a PolicySnapshot from current settings + optional scope override."""
        s = settings_obj if settings_obj is not None else _default_settings

        def _get(attr: str, default: Any) -> Any:
            if s is None:
                return default
            return getattr(s, attr, default)

        allowed_paths: list[str] = _get("allowed_paths", []) or []
        denied_paths: list[str] = _get("denied_paths", []) or []
        allowed_tools: list[str] = _get("allowed_tools", []) or []
        denied_tools: list[str] = _get("denied_tools", []) or []
        allowed_providers: list[str] = _get("allowed_providers", ["ollama", "lmstudio"]) or ["ollama", "lmstudio"]
        model_policy: str = str(_get("model_policy", "local_only") or "local_only")
        network_policy: str = str(_get("network_policy", "deny_all") or "deny_all")
        write_policy: str = str(_get("write_policy", "proposal_only") or "proposal_only")
        approval_gates: list[str] = _get("approval_gates", []) or []

        snap = cls(
            snapshot_id=str(uuid.uuid4()),
            run_id=str(run_id or ""),
            policy_scope_id=str(policy_scope_id) if policy_scope_id else None,
            allowed_paths=list(allowed_paths),
            denied_paths=list(denied_paths),
            allowed_tools=list(allowed_tools),
            denied_tools=list(denied_tools),
            allowed_providers=list(allowed_providers),
            model_policy=model_policy,
            network_policy=network_policy,
            write_policy=write_policy,
            approval_gates=list(approval_gates),
            created_at=time.time(),
            config_hash="",  # computed below
        )
        snap.config_hash = _compute_hash_for(snap)
        return snap


def _canonical_policy_fields(snap: PolicySnapshot) -> dict[str, Any]:
    """Return only the policy fields (no snapshot_id, no created_at) for hashing."""
    return {
        "run_id": snap.run_id,
        "policy_scope_id": snap.policy_scope_id,
        "allowed_paths": sorted(snap.allowed_paths),
        "denied_paths": sorted(snap.denied_paths),
        "allowed_tools": sorted(snap.allowed_tools),
        "denied_tools": sorted(snap.denied_tools),
        "allowed_providers": sorted(snap.allowed_providers),
        "model_policy": snap.model_policy,
        "network_policy": snap.network_policy,
        "write_policy": snap.write_policy,
        "approval_gates": sorted(snap.approval_gates),
    }


def _compute_hash_for(snap: PolicySnapshot) -> str:
    canonical = json.dumps(_canonical_policy_fields(snap), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PolicySnapshotService:
    """Captures and validates policy snapshots for a run."""

    def capture(
        self,
        run_id: str,
        *,
        settings_obj: Any = None,
        policy_scope_id: str | None = None,
        extra: dict | None = None,
    ) -> PolicySnapshot:
        """Capture current policy state. Returns a PolicySnapshot with stable config_hash."""
        snap = PolicySnapshot.from_settings(
            run_id,
            settings_obj=settings_obj,
            policy_scope_id=policy_scope_id,
        )
        return snap

    def serialize(self, snapshot: PolicySnapshot) -> str:
        """Return canonical JSON string."""
        return json.dumps(snapshot.as_dict(), sort_keys=True, separators=(",", ":"))

    def compute_hash(self, snapshot: PolicySnapshot) -> str:
        """SHA-256 of canonical serialization (policy fields only, for stability)."""
        return _compute_hash_for(snapshot)

    def validate(self, snapshot: PolicySnapshot | None) -> dict:
        """Returns {"valid": bool, "issues": list[str]}"""
        if snapshot is None:
            return {"valid": False, "issues": ["missing"]}

        issues: list[str] = []

        if not snapshot.run_id:
            issues.append("run_id is empty")

        if not snapshot.snapshot_id:
            issues.append("snapshot_id is empty")

        if snapshot.model_policy not in ("local_only", "any", "allowlist"):
            issues.append(f"unknown model_policy: {snapshot.model_policy!r}")

        if snapshot.network_policy not in ("deny_all", "restricted", "allowed"):
            issues.append(f"unknown network_policy: {snapshot.network_policy!r}")

        if snapshot.write_policy not in ("proposal_only", "approval_required", "unrestricted"):
            issues.append(f"unknown write_policy: {snapshot.write_policy!r}")

        if not snapshot.config_hash:
            issues.append("config_hash is empty")

        return {"valid": len(issues) == 0, "issues": issues}
