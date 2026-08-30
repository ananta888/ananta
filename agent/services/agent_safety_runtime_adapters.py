"""Concrete, fail-closed runtime adapters for Hub-owned agent containment."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent.services.agent_safety_ports import CredentialLeaseGrant, SafetyControlReceipt
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from agent.services.ops_command_runner import CommandResult, CommandRunner
from ananta_contracts.agent_safety import SafetyAction, canonical_digest, require_token, utc_now


class DockerAgentSafetyRuntime:
    """Exact-allowlist Docker command gateway for agent-safety adapters.

    This adapter deliberately does not reuse the interactive Ops approval path.
    It is enabled only through the Hub's explicit agent-safety preauthorization
    configuration and accepts no daemon-side fuzzy target resolution.
    """

    adapter_id = "hub_docker_agent_safety_v1"

    def __init__(
        self,
        *,
        runner: CommandRunner,
        managed_sandboxes: Iterable[str],
        snapshot_root: Path,
    ) -> None:
        managed = frozenset(require_token(value, "sandbox_id") for value in managed_sandboxes)
        if not managed or "*" in managed:
            raise ValueError("agent_safety_managed_sandboxes_required")
        self._runner = runner
        self._managed = managed
        self._snapshot_root = Path(snapshot_root)

    def ready(self) -> bool:
        if not self._runner.exists("docker"):
            return False
        result = self._runner.run(["docker", "version", "--format", "{{json .Server.Version}}"], timeout_seconds=5)
        return result.returncode == 0

    def apply(
        self,
        *,
        operation_id: str,
        run_id: str,
        sandbox_id: str,
        action: SafetyAction,
        reason: str,
    ) -> SafetyControlReceipt:
        del reason
        target = self._target(sandbox_id)
        state = self._inspect_json(target, ".State")
        if state is None:
            return self._receipt(operation_id, run_id, target, action, False, "sandbox_inspect_failed")
        if action == SafetyAction.FREEZE:
            if bool(state.get("Paused")):
                return self._receipt(operation_id, run_id, target, action, True, "sandbox_already_frozen")
            result = self._runner.run(["docker", "pause", target], timeout_seconds=15)
            return self._command_receipt(operation_id, run_id, target, action, result, "sandbox_frozen")
        if action == SafetyAction.TERMINATE:
            if not bool(state.get("Running")):
                return self._receipt(operation_id, run_id, target, action, True, "sandbox_already_terminated")
            result = self._runner.run(["docker", "kill", target], timeout_seconds=15)
            return self._command_receipt(operation_id, run_id, target, action, result, "sandbox_terminated")
        fence = self.deny(operation_id=operation_id, run_id=run_id, sandbox_id=target)
        return SafetyControlReceipt(
            operation_id=operation_id,
            run_id=run_id,
            sandbox_id=target,
            action=action,
            enforced=fence.enforced,
            reason_code="sandbox_isolated" if fence.enforced else fence.reason_code,
            observed_at=utc_now(),
            adapter_id=self.adapter_id,
            runtime_verified=fence.runtime_verified,
        )

    def deny(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt:
        target = self._target(sandbox_id)
        networks = self._inspect_json(target, ".NetworkSettings.Networks")
        if networks is None:
            return self._receipt(
                operation_id, run_id, target, SafetyAction.ISOLATE, False, "sandbox_network_inspect_failed"
            )
        failures: list[str] = []
        for network in sorted(str(name) for name in networks):
            result = self._runner.run(
                ["docker", "network", "disconnect", "--force", network, target], timeout_seconds=15
            )
            if result.returncode != 0 and "not connected" not in str(result.stderr or "").lower():
                failures.append(network)
        return self._receipt(
            operation_id,
            run_id,
            target,
            SafetyAction.ISOLATE,
            not failures,
            "egress_fence_enforced" if not failures else "egress_fence_failed",
        )

    def capture(
        self,
        *,
        operation_id: str,
        run_id: str,
        sandbox_id: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        target = self._target(sandbox_id)
        state = self._inspect_json(target, ".State")
        networks = self._inspect_json(target, ".NetworkSettings.Networks")
        if state is None or networks is None:
            return {
                "sandbox_id": target,
                "captured": False,
                "reason_code": "forensic_snapshot_inspect_failed",
                "adapter_id": self.adapter_id,
            }
        payload = {
            "schema_version": 1,
            "operation_id": require_token(operation_id, "operation_id"),
            "run_id": require_token(run_id, "run_id"),
            "sandbox_id": target,
            "state": _safe_container_state(state),
            "network_names": sorted(str(name) for name in networks),
            "captured_at": utc_now(),
        }
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        bounded = min(max(int(max_bytes), 1_024), 1_048_576)
        if len(rendered) > bounded:
            return {
                "sandbox_id": target,
                "captured": False,
                "reason_code": "forensic_snapshot_size_limit_exceeded",
                "adapter_id": self.adapter_id,
            }
        if self._snapshot_root.is_symlink():
            return {
                "sandbox_id": target,
                "captured": False,
                "reason_code": "forensic_snapshot_root_invalid",
                "adapter_id": self.adapter_id,
            }
        self._snapshot_root.mkdir(parents=True, exist_ok=True)
        filename = f"{operation_id}-{target}.json"
        destination = self._snapshot_root / filename
        if destination.is_symlink():
            return {
                "sandbox_id": target,
                "captured": False,
                "reason_code": "forensic_snapshot_destination_invalid",
                "adapter_id": self.adapter_id,
            }
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(rendered)
        temporary.replace(destination)
        return {
            "sandbox_id": target,
            "captured": True,
            "reason_code": "forensic_snapshot_captured",
            "adapter_id": self.adapter_id,
            "artifact_ref": filename,
            "artifact_digest": hashlib.sha256(rendered).hexdigest(),
            "artifact_bytes": len(rendered),
        }

    def cleanup(self, *, operation_id: str, run_id: str, sandbox_id: str) -> SafetyControlReceipt:
        target = self._target(sandbox_id)
        inspected = self._runner.run(["docker", "inspect", "--format", "{{json .State}}", target], timeout_seconds=10)
        if inspected.returncode != 0 and any(
            marker in str(inspected.stderr or "").lower() for marker in ("no such object", "no such container")
        ):
            return self._receipt(operation_id, run_id, target, SafetyAction.TERMINATE, True, "sandbox_already_removed")
        if inspected.returncode != 0:
            return self._receipt(
                operation_id, run_id, target, SafetyAction.TERMINATE, False, "sandbox_cleanup_inspect_failed"
            )
        result = self._runner.run(["docker", "rm", "--force", target], timeout_seconds=30)
        return self._command_receipt(
            operation_id, run_id, target, SafetyAction.TERMINATE, result, "sandbox_cleanup_enforced"
        )

    def _target(self, sandbox_id: str) -> str:
        target = require_token(sandbox_id, "sandbox_id")
        if target not in self._managed:
            raise ValueError("agent_safety_sandbox_not_pre_authorized")
        return target

    def _inspect_json(self, target: str, selector: str) -> dict[str, Any] | None:
        result = self._runner.run(
            ["docker", "inspect", "--format", f"{{{{json {selector}}}}}", target], timeout_seconds=10
        )
        if result.returncode != 0:
            return None
        try:
            value = json.loads(result.stdout or "{}")
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _command_receipt(
        self,
        operation_id: str,
        run_id: str,
        target: str,
        action: SafetyAction,
        result: CommandResult,
        success_code: str,
    ) -> SafetyControlReceipt:
        return self._receipt(
            operation_id,
            run_id,
            target,
            action,
            result.returncode == 0,
            success_code if result.returncode == 0 else "sandbox_runtime_command_failed",
        )

    def _receipt(
        self,
        operation_id: str,
        run_id: str,
        sandbox_id: str,
        action: SafetyAction,
        enforced: bool,
        reason_code: str,
    ) -> SafetyControlReceipt:
        return SafetyControlReceipt(
            operation_id=require_token(operation_id, "operation_id"),
            run_id=require_token(run_id, "run_id"),
            sandbox_id=require_token(sandbox_id, "sandbox_id"),
            action=action,
            enforced=bool(enforced),
            reason_code=reason_code,
            observed_at=utc_now(),
            adapter_id=self.adapter_id,
            runtime_verified=bool(enforced),
        )


class HubCredentialLeaseAuthority:
    """Durable, run-bound short-lived credential capability authority."""

    adapter_id = "hub_credential_lease_v1"

    def __init__(self, store: AgentSafetyStateStorePort) -> None:
        self._store = store

    def issue(self, *, run_id: str, agent_id: str, ttl_seconds: int) -> CredentialLeaseGrant:
        normalized_run = require_token(run_id, "run_id")
        normalized_agent = require_token(agent_id, "agent_id")
        bounded_ttl = min(max(int(ttl_seconds), 30), 3_600)
        lease_id = f"asl_{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(seconds=bounded_ttl)
        self._store.append(
            "credential_lease",
            lease_id,
            {
                "lease_id": lease_id,
                "run_id": normalized_run,
                "agent_id": normalized_agent,
                "token_digest": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "state": "active",
                "expires_at": _iso(expires),
                "issued_at": utc_now(),
            },
            expected_revision=0,
        )
        return CredentialLeaseGrant(lease_id, normalized_run, normalized_agent, token, _iso(expires))

    def verify(self, *, run_id: str, lease_id: str, token: str) -> bool:
        lease = self._store.get("credential_lease", require_token(lease_id, "lease_id"))
        if not lease or lease.get("run_id") != require_token(run_id, "run_id") or lease.get("state") != "active":
            return False
        if str(lease.get("expires_at") or "") <= utc_now():
            return False
        supplied = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
        return hmac.compare_digest(str(lease.get("token_digest") or ""), supplied)

    def revoke(self, *, operation_id: str, run_id: str) -> SafetyControlReceipt:
        normalized_run = require_token(run_id, "run_id")
        active = [
            lease
            for lease in self._store.list("credential_lease", run_id=normalized_run)
            if lease.get("state") == "active"
        ]
        for lease in active:
            self._store.append(
                "credential_lease",
                str(lease["lease_id"]),
                {**lease, "state": "revoked", "revoked_at": utc_now(), "revoked_by_operation": operation_id},
                expected_revision=int(lease["revision"]),
            )
        return SafetyControlReceipt(
            operation_id=require_token(operation_id, "operation_id"),
            run_id=normalized_run,
            sandbox_id="run-credentials",
            action=SafetyAction.ISOLATE,
            enforced=True,
            reason_code="credential_leases_revoked" if active else "credential_leases_already_absent",
            observed_at=utc_now(),
            adapter_id=self.adapter_id,
            runtime_verified=True,
        )


def _safe_container_state(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(value.get("Status") or ""),
        "running": bool(value.get("Running")),
        "paused": bool(value.get("Paused")),
        "restarting": bool(value.get("Restarting")),
        "oom_killed": bool(value.get("OOMKilled")),
        "dead": bool(value.get("Dead")),
        "exit_code": int(value.get("ExitCode") or 0),
        "started_at": str(value.get("StartedAt") or ""),
        "finished_at": str(value.get("FinishedAt") or ""),
        "state_digest": canonical_digest(
            {
                "status": str(value.get("Status") or ""),
                "running": bool(value.get("Running")),
                "paused": bool(value.get("Paused")),
                "exit_code": int(value.get("ExitCode") or 0),
            }
        ),
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DockerSandboxSafetyControlAdapter:
    def __init__(self, runtime: DockerAgentSafetyRuntime) -> None:
        self._runtime = runtime

    def apply(self, **kwargs: Any) -> SafetyControlReceipt:
        return self._runtime.apply(**kwargs)


class DockerEgressFenceAdapter:
    def __init__(self, runtime: DockerAgentSafetyRuntime) -> None:
        self._runtime = runtime

    def deny(self, **kwargs: Any) -> SafetyControlReceipt:
        return self._runtime.deny(**kwargs)


class DockerForensicSnapshotAdapter:
    def __init__(self, runtime: DockerAgentSafetyRuntime) -> None:
        self._runtime = runtime

    def capture(self, **kwargs: Any) -> dict[str, Any]:
        return self._runtime.capture(**kwargs)


class DockerSandboxCleanupAdapter:
    def __init__(self, runtime: DockerAgentSafetyRuntime) -> None:
        self._runtime = runtime

    def cleanup(self, **kwargs: Any) -> SafetyControlReceipt:
        return self._runtime.cleanup(**kwargs)


__all__ = [
    "DockerAgentSafetyRuntime",
    "DockerEgressFenceAdapter",
    "DockerForensicSnapshotAdapter",
    "DockerSandboxCleanupAdapter",
    "DockerSandboxSafetyControlAdapter",
    "HubCredentialLeaseAuthority",
]
