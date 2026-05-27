from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_CLASS_ORDER = {
    "low-risk-readonly": 0,
    "bounded-mutable": 1,
    "hardened-high-risk": 2,
}


@dataclass(frozen=True)
class SandboxCommandDecision:
    allowed: bool
    reason_code: str
    required_class: str
    active_class: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "reason_code": str(self.reason_code or "sandbox_policy_unknown"),
            "required_class": str(self.required_class or "bounded-mutable"),
            "active_class": str(self.active_class or "bounded-mutable"),
        }


class SandboxPolicyService:
    def normalize(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(raw or {})
        filesystem = dict(payload.get("filesystem") or {})
        network = dict(payload.get("network") or {})
        wrappers = dict(payload.get("command_wrappers") or {})
        terminal = dict(payload.get("terminal_access") or {})
        test_rollout = dict(payload.get("test_rollout") or {})

        workspace_roots = [str(item or "").strip() for item in list(filesystem.get("allowed_workspace_roots") or ["/workspace", "/project-workspaces"]) if str(item or "").strip()]
        blocked_path_fragments = [str(item or "").strip() for item in list(filesystem.get("blocked_path_fragments") or ["/.ssh", "/etc/", "/proc/", "/sys/"]) if str(item or "").strip()]
        allowed_domains = [str(item or "").strip().lower() for item in list(network.get("allowed_domains") or []) if str(item or "").strip()]
        allowed_cidrs = [str(item or "").strip() for item in list(network.get("allowed_cidrs") or []) if str(item or "").strip()]
        blocked_target_types = [
            str(item or "").strip().lower()
            for item in list(terminal.get("blocked_target_types") or ["hub_as_worker"])
            if str(item or "").strip()
        ]
        if not blocked_target_types:
            blocked_target_types = ["hub_as_worker"]

        return {
            "filesystem": {
                "enforce_workspace_boundary": bool(filesystem.get("enforce_workspace_boundary", True)),
                "allowed_workspace_roots": workspace_roots,
                "blocked_path_fragments": blocked_path_fragments,
            },
            "network": {
                "egress_mode": str(network.get("egress_mode") or "restricted").strip().lower() or "restricted",
                "allowed_domains": allowed_domains,
                "allowed_cidrs": allowed_cidrs,
            },
            "command_wrappers": {
                "enabled": bool(wrappers.get("enabled", True)),
                "default_isolation_class": self._normalize_class(wrappers.get("default_isolation_class"), "bounded-mutable"),
                "high_risk_patterns": [
                    str(item or "").strip().lower()
                    for item in list(
                        wrappers.get("high_risk_patterns")
                        or ["sudo ", "docker ", "podman ", "rm -rf", "chmod ", "chown ", "apt ", "yum ", "dnf ", "curl ", "wget "]
                    )
                    if str(item or "").strip()
                ],
            },
            "terminal_access": {
                "enforce": bool(terminal.get("enforce", True)),
                "blocked_target_types": blocked_target_types,
                "write_requires_admin_for": [
                    str(item or "").strip().lower()
                    for item in list(terminal.get("write_requires_admin_for") or ["hub", "hub_as_worker"])
                    if str(item or "").strip()
                ],
            },
            "test_rollout": {
                "enabled": bool(test_rollout.get("enabled", True)),
                "default_environment": str(test_rollout.get("default_environment") or "sandboxed").strip().lower() or "sandboxed",
                "phases": [
                    str(item or "").strip().lower()
                    for item in list(test_rollout.get("phases") or ["dry_run", "canary", "full"])
                    if str(item or "").strip()
                ],
            },
        }

    def resolve(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(cfg or {})
        raw_policy = payload.get("sandbox_policy") if isinstance(payload.get("sandbox_policy"), dict) else {}
        return self.normalize(raw_policy)

    def command_isolation_class(self, command: str | None, *, policy: dict[str, Any]) -> str:
        text = str(command or "").strip().lower()
        if not text:
            return "low-risk-readonly"
        high_risk_patterns = list(((policy.get("command_wrappers") or {}).get("high_risk_patterns") or []))
        if any(pattern in text for pattern in high_risk_patterns):
            return "hardened-high-risk"
        if text.startswith(("ls", "cat ", "git status", "git diff", "echo ", "pytest", "python ", "npm test", "go test")):
            return "bounded-mutable"
        if text.startswith(("cp ", "mv ", "rm ", "sed ", "tee ", "git commit", "git push")):
            return "bounded-mutable"
        return "bounded-mutable"

    def evaluate_command(
        self,
        *,
        command: str | None,
        active_class: str | None,
        cfg: dict[str, Any] | None,
    ) -> SandboxCommandDecision:
        policy = self.resolve(cfg)
        wrappers = dict(policy.get("command_wrappers") or {})
        if not bool(wrappers.get("enabled", True)):
            active = self._normalize_class(active_class, "bounded-mutable")
            required = self.command_isolation_class(command, policy=policy)
            return SandboxCommandDecision(True, "sandbox_wrappers_disabled", required, active)

        active = self._normalize_class(active_class, wrappers.get("default_isolation_class") or "bounded-mutable")
        required = self.command_isolation_class(command, policy=policy)
        if _CLASS_ORDER[active] < _CLASS_ORDER[required]:
            return SandboxCommandDecision(
                allowed=False,
                reason_code=f"sandbox_class_insufficient:{active}->{required}",
                required_class=required,
                active_class=active,
            )
        return SandboxCommandDecision(
            allowed=True,
            reason_code="sandbox_class_sufficient",
            required_class=required,
            active_class=active,
        )

    @staticmethod
    def _normalize_class(raw: Any, default: str) -> str:
        candidate = str(raw or "").strip().lower()
        if candidate in _CLASS_ORDER:
            return candidate
        return default


_SERVICE = SandboxPolicyService()


def get_sandbox_policy_service() -> SandboxPolicyService:
    return _SERVICE
