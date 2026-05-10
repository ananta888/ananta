"""External tool adapters: Hermes, OpenCode, MCP.

EW-T045: HermesAdapter — bounded task/context, sensitivity-aware, artifact parsing.
EW-T046: OpenCodeAdapter — allowed-files-only, PatchArtifact output, no direct tree write.
EW-T047: MCPAdapter — ToolPolicy filtering, env allowlist, sanitized results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.execution_envelope import ExecutionEnvelope
from worker.core.sanitizer import OutputSanitizer

_SANITIZER = OutputSanitizer()

CLOUD_BLOCKED_SENSITIVITIES = frozenset({
    ContextSensitivity.confidential,
    ContextSensitivity.secret,
})


# ── AdapterResult ─────────────────────────────────────────────────────────────

@dataclass
class AdapterResult:
    allowed: bool
    reason_code: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sanitized_output: str = ""
    policy_observations: list[str] = field(default_factory=list)
    detail: str = ""


# ── HermesAdapter (EW-T045) ───────────────────────────────────────────────────

class HermesAdapter:
    """Sends bounded task/context to Hermes; parses response into Ananta artifacts. EW-T045.

    Policy:
    - Sends to Hermes only when policy allows (provider_call capability required).
    - Strips or redacts context based on ContextBlock sensitivity.
    - Parses Hermes response into Ananta artifacts and trace.
    - Never trusts Hermes raw output directly as executed instructions.
    """

    def prepare_context(
        self,
        blocks: list[ContextBlock],
        *,
        cloud_allowed: bool = False,
    ) -> tuple[list[ContextBlock], list[str]]:
        """Filter context blocks for Hermes; blocks sensitive content when cloud=False."""
        allowed: list[ContextBlock] = []
        redacted_origins: list[str] = []

        for block in blocks:
            if not cloud_allowed and block.sensitivity in CLOUD_BLOCKED_SENSITIVITIES:
                redacted_origins.append(block.origin_id)
                continue
            allowed.append(block)

        return allowed, redacted_origins

    def parse_response(
        self,
        raw_response: str,
        *,
        task_id: str,
        sanitize: bool = True,
    ) -> AdapterResult:
        """Parse Hermes response into Ananta artifacts. EW-T045."""
        if sanitize:
            san = _SANITIZER.sanitize(raw_response)
            clean_output = san.text
            observations = [f"secret_redacted:{r}" for r in san.redactions]
        else:
            clean_output = raw_response
            observations = []

        artifacts = []
        # Extract patch blocks
        for match in re.finditer(r"```(?:diff|patch)\n(.*?)```", clean_output, re.DOTALL):
            artifacts.append({
                "kind": "patch_candidate",
                "task_id": task_id,
                "content": match.group(1),
                "source": "hermes",
            })

        return AdapterResult(
            allowed=True,
            reason_code="hermes_ok",
            artifacts=artifacts,
            sanitized_output=clean_output,
            policy_observations=observations,
        )

    def check_policy(self, envelope: ExecutionEnvelope) -> tuple[bool, str]:
        if not envelope.has_capability("provider_call"):
            return False, "missing_capability"
        return True, "allow"


# ── OpenCodeAdapter (EW-T046) ─────────────────────────────────────────────────

class OpenCodeAdapter:
    """Hardens OpenCode invocation: allowed files only, PatchArtifact output. EW-T046.

    OpenCode cannot directly write into main tree unless patch_apply path is approved.
    """

    def filter_allowed_files(
        self,
        requested_files: list[str],
        *,
        read_paths: list[str],
        workspace_root: str = "",
    ) -> tuple[list[str], list[str]]:
        """Return (allowed, denied) file lists based on filesystem scope."""
        from worker.core.file_policy import FilePolicy
        policy = FilePolicy()
        allowed, denied = [], []
        for f in requested_files:
            result = policy.check_read(f, read_paths=read_paths, workspace_root=workspace_root)
            (allowed if result.allowed else denied).append(f)
        return allowed, denied

    def parse_patch_output(
        self,
        raw_output: str,
        *,
        task_id: str,
        artifact_id: str,
        workspace_root: str = "",
        write_paths: list[str] | None = None,
    ) -> AdapterResult:
        """Parse OpenCode output as PatchArtifact; enforce scope. EW-T046."""
        from worker.core.file_policy import FilePolicy, _parse_unified_diff

        san = _SANITIZER.sanitize(raw_output)
        hunks = _parse_unified_diff(san.text)

        if not hunks:
            return AdapterResult(
                allowed=False,
                reason_code="adapter_validation_failed",
                detail="OpenCode output contained no parseable patch",
            )

        from worker.core.file_policy import PatchArtifact, FilePolicy
        artifact = PatchArtifact(
            artifact_id=artifact_id,
            task_id=task_id,
            provenance=f"{task_id}:opencode",
            hunks=hunks,
        )

        scope_result = FilePolicy().check_patch_paths(
            artifact,
            write_paths=write_paths or [],
            workspace_root=workspace_root,
        )
        if not scope_result.allowed:
            return AdapterResult(
                allowed=False,
                reason_code=scope_result.reason_code,
                detail=scope_result.detail,
            )

        return AdapterResult(
            allowed=True,
            reason_code="opencode_ok",
            artifacts=[artifact.as_dict()],
            sanitized_output=san.text,
        )

    def check_policy(self, envelope: ExecutionEnvelope) -> tuple[bool, str]:
        if not envelope.has_capability("patch_propose"):
            return False, "missing_capability"
        return True, "allow"


# ── MCPAdapter (EW-T047) ──────────────────────────────────────────────────────

class MCPAdapter:
    """MCP server/tool adapter with capability filtering. EW-T047.

    - Tool list filtered by ToolPolicy.
    - Environment variables are explicit allowlist only.
    - Tool results sanitized before model-visible use.
    """

    # Allowlisted env vars that can be passed to MCP tools
    DEFAULT_ALLOWED_ENV_KEYS = frozenset({
        "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
    })

    def filter_tools(
        self,
        mcp_tool_ids: list[str],
        envelope: ExecutionEnvelope,
    ) -> tuple[list[str], list[str]]:
        """Return (allowed_tools, denied_tools) based on ToolPolicy. EW-T047."""
        allowed, denied = [], []
        for tool_id in mcp_tool_ids:
            if envelope.tool_policy.is_tool_allowed(tool_id):
                allowed.append(tool_id)
            else:
                denied.append(tool_id)
        return allowed, denied

    def scoped_env(
        self,
        env: dict[str, str],
        *,
        extra_allowed_keys: set[str] | None = None,
    ) -> dict[str, str]:
        """Return MCP subprocess env with only explicitly allowed keys. EW-T047."""
        allowed_keys = self.DEFAULT_ALLOWED_ENV_KEYS | (extra_allowed_keys or set())
        return {k: v for k, v in env.items() if k in allowed_keys}

    def sanitize_result(self, raw_result: Any) -> Any:
        """Sanitize MCP tool result before model use. EW-T047."""
        if isinstance(raw_result, str):
            return _SANITIZER.sanitize(raw_result).text
        if isinstance(raw_result, dict):
            return _SANITIZER.sanitize_dict(raw_result)
        if isinstance(raw_result, list):
            return [self.sanitize_result(item) for item in raw_result]
        return raw_result

    def check_policy(self, envelope: ExecutionEnvelope) -> tuple[bool, str]:
        if not envelope.has_capability("mcp_call"):
            return False, "missing_capability"
        if not envelope.approval_for("mcp_call"):
            return False, "approval_missing"
        return True, "allow"
