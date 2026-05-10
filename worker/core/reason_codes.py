"""Policy decision reason codes with human-readable messages.

EW-T011: Reason codes appear in WorkerResult, TraceBundle, and logs.
Human-readable messages are generated from codes without hiding the code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonCode:
    code: str
    message: str
    is_retriable: bool = False

    def __str__(self) -> str:
        return self.code

    def format(self, **ctx: str) -> str:
        """Return 'code: message (key=val ...)' — code is never omitted."""
        detail = " ".join(f"{k}={v!r}" for k, v in ctx.items())
        if detail:
            return f"{self.code}: {self.message} ({detail})"
        return f"{self.code}: {self.message}"


# ── Capability codes ───────────────────────────────────────────────────────────

MISSING_CAPABILITY = ReasonCode(
    "missing_capability",
    "Execution denied: required capability is absent from the envelope.",
)
CAPABILITY_SNAPSHOT_MISMATCH = ReasonCode(
    "capability_snapshot_mismatch",
    "Capability snapshot hash does not match envelope after execution.",
)

# ── Context codes ─────────────────────────────────────────────────────────────

CONTEXT_MISSING = ReasonCode(
    "context_missing",
    "Execution denied: context_envelope_ref is empty or unresolvable.",
)
CONTEXT_SENSITIVITY_BLOCKED = ReasonCode(
    "context_sensitivity_blocked",
    "Context block is too sensitive for the current cloud policy.",
)
CONTEXT_BUDGET_EXCEEDED = ReasonCode(
    "context_budget_exceeded",
    "Context exceeds the token budget for this execution.",
)
CONTEXT_UNBOUNDED_DUMP = ReasonCode(
    "context_unbounded_dump",
    "Worker refused unbounded repository or all-files context dump.",
)

# ── Approval codes ────────────────────────────────────────────────────────────

APPROVAL_MISSING = ReasonCode(
    "approval_missing",
    "Execution requires an ApprovalRef that is absent.",
    is_retriable=True,
)
APPROVAL_STALE = ReasonCode(
    "approval_stale",
    "ApprovalRef exists but was granted outside the allowed time window.",
    is_retriable=True,
)
APPROVAL_OPERATION_MISMATCH = ReasonCode(
    "approval_operation_mismatch",
    "ApprovalRef exists but does not cover the requested operation.",
)

# ── Operation / denial codes ──────────────────────────────────────────────────

DENIED_OPERATION = ReasonCode(
    "denied_operation",
    "Operation is in denied_operations or not in allowed_operations.",
)
TASK_KIND_UNKNOWN = ReasonCode(
    "task_kind_unknown",
    "Execution denied: task_kind is not in the known vocabulary.",
)
INVALID_REQUEST = ReasonCode(
    "invalid_request",
    "Envelope is structurally malformed; execution never started.",
)

# ── Provider codes ────────────────────────────────────────────────────────────

PROVIDER_BLOCKED = ReasonCode(
    "provider_blocked",
    "Model provider is blocked by policy (cloud_allowed=false or not in allowlist).",
)
PROVIDER_UNAVAILABLE = ReasonCode(
    "provider_unavailable",
    "Model provider is configured but not reachable.",
    is_retriable=True,
)
PROVIDER_UNAUTHORIZED = ReasonCode(
    "provider_unauthorized",
    "Model provider rejected credentials.",
)
PROVIDER_MISCONFIGURED = ReasonCode(
    "provider_misconfigured",
    "Model provider configuration is invalid (missing base_url, bad endpoint).",
)
PROVIDER_TIMEOUT = ReasonCode(
    "provider_timeout",
    "Model provider did not respond within the allowed timeout.",
    is_retriable=True,
)

# ── Tool codes ────────────────────────────────────────────────────────────────

TOOL_UNAVAILABLE = ReasonCode(
    "tool_unavailable",
    "Tool is not in allowed_tool_ids or not registered in WorkerToolRegistry.",
)
TOOL_SCHEMA_INVALID = ReasonCode(
    "tool_schema_invalid",
    "Tool invocation arguments do not match the declared input schema.",
)
TOOL_TIMEOUT = ReasonCode(
    "tool_timeout",
    "Tool invocation exceeded timeout_seconds.",
    is_retriable=True,
)
TOOL_OUTPUT_OVERSIZED = ReasonCode(
    "tool_output_oversized",
    "Tool output exceeded max_output_chars; output was truncated.",
)
TOOL_SCOPE_VIOLATION = ReasonCode(
    "tool_scope_violation",
    "Tool attempted to access a path outside the declared filesystem scope.",
)

# ── Shell codes ───────────────────────────────────────────────────────────────

SHELL_COMMAND_UNSAFE = ReasonCode(
    "shell_command_unsafe",
    "Shell command was blocked: outside workspace scope or matches unsafe pattern.",
)
SHELL_EXECUTE_REQUIRES_APPROVAL = ReasonCode(
    "shell_execute_requires_approval",
    "shell_execute capability requires an ApprovalRef when policy is confirm_required.",
    is_retriable=True,
)

# ── Patch / file codes ────────────────────────────────────────────────────────

PATCH_APPLY_REQUIRES_APPROVAL = ReasonCode(
    "patch_apply_requires_approval",
    "patch_apply capability requires an ApprovalRef when policy is confirm_required.",
    is_retriable=True,
)
PATCH_SCOPE_VIOLATION = ReasonCode(
    "patch_scope_violation",
    "Patch targets a file outside the declared filesystem scope.",
)
FILE_SCOPE_VIOLATION = ReasonCode(
    "file_scope_violation",
    "File operation targets a path outside the declared filesystem scope.",
)

# ── Memory codes ──────────────────────────────────────────────────────────────

MEMORY_WRITE_REQUIRES_APPROVAL = ReasonCode(
    "memory_write_requires_approval",
    "memory_write capability requires an ApprovalRef.",
    is_retriable=True,
)
MEMORY_STORE_NOT_FOUND = ReasonCode(
    "memory_store_not_found",
    "Requested memory store does not exist for this scope.",
)

# ── Adapter / sanitizer codes ─────────────────────────────────────────────────

ADAPTER_VALIDATION_FAILED = ReasonCode(
    "adapter_validation_failed",
    "External tool adapter output failed structured artifact validation.",
)
PROMPT_INJECTION_BLOCKED = ReasonCode(
    "prompt_injection_blocked",
    "Artifact-sourced instructions were blocked by prompt-injection guardrails.",
)
SECRET_REDACTED = ReasonCode(
    "secret_redacted",
    "Output contained a potential secret that was redacted before use.",
)

# ── Registry ──────────────────────────────────────────────────────────────────

_ALL: dict[str, ReasonCode] = {
    obj.code: obj
    for name, obj in list(globals().items())
    if isinstance(obj, ReasonCode)
}


def lookup(code: str) -> ReasonCode | None:
    """Return the ReasonCode for a code string, or None if unknown."""
    return _ALL.get(code)


def message_for(code: str, **ctx: str) -> str:
    """Return 'code: human message' for any code, known or unknown."""
    rc = _ALL.get(code)
    if rc is None:
        detail = " ".join(f"{k}={v!r}" for k, v in ctx.items())
        return f"{code}: (unknown reason code)" + (f" ({detail})" if detail else "")
    return rc.format(**ctx)


def is_retriable(code: str) -> bool:
    rc = _ALL.get(code)
    return rc.is_retriable if rc else False


def all_codes() -> frozenset[str]:
    return frozenset(_ALL.keys())
