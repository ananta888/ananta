"""WorkerToolRegistry and ToolInvocationEnvelope.

EW-T013: Tool registry with capability/schema declarations.
EW-T014: ToolInvocationEnvelope with argument validation and ToolResult.
EW-T018: Resource limits per tool invocation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Risk classes (mirrors capability vocabulary) ───────────────────────────────

TOOL_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})


# ── ToolResult ────────────────────────────────────────────────────────────────

class ToolResult(dict):
    """Structured output from a single tool invocation with legacy dict compatibility."""

    _CORE_FIELDS = (
        "tool_id",
        "execution_id",
        "success",
        "stdout",
        "stderr",
        "exit_code",
        "files_read",
        "files_written",
        "patches",
        "artifacts",
        "reason_code",
        "truncated",
        "duration_seconds",
        "task_id",
        "command",
    )

    def __init__(self, **data: Any) -> None:
        payload: dict[str, Any] = {
            "tool_id": "",
            "execution_id": "",
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "files_read": [],
            "files_written": [],
            "patches": [],
            "artifacts": [],
            "reason_code": None,
            "truncated": False,
            "duration_seconds": None,
            "task_id": "",
            "command": "",
        }
        payload.update(data)
        super().__init__(payload)
        self._refresh_legacy_projection()

    def __getattr__(self, item: str) -> Any:
        if item in self:
            return self[item]
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {item!r}")

    def __setattr__(self, key: str, value: Any) -> None:
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        self[key] = value
        if key in self._CORE_FIELDS:
            self._refresh_legacy_projection()

    def _legacy_status(self) -> str:
        if bool(self.get("success")):
            return "passed"
        if self.get("reason_code") == "tool_timeout":
            return "degraded"
        return "failed"

    def _refresh_legacy_projection(self) -> None:
        status = self._legacy_status()
        duration_ms = int((float(self.get("duration_seconds") or 0.0)) * 1000)
        stdout = str(self.get("stdout") or "")
        stderr = str(self.get("stderr") or "")
        self["schema"] = "test_result_artifact.v1"
        self["status"] = status
        self["stdout_ref"] = stdout or "<empty>"
        self["stderr_ref"] = stderr or "<empty>"
        self["output_summary"] = (
            f"Execution status={status}, duration_ms={duration_ms}, "
            f"tool_id={self.get('tool_id')}, execution_id={self.get('execution_id')}"
        )
        self["failure_hints"] = ([self["reason_code"]] if self.get("reason_code") else [])
        if self.get("exit_code") is None:
            self["exit_code"] = 0 if bool(self.get("success")) else 1

    @classmethod
    def denied(
        cls,
        tool_id: str,
        execution_id: str,
        reason_code: str,
        *,
        task_id: str = "",
        command: str = "",
    ) -> "ToolResult":
        return cls(
            tool_id=tool_id,
            execution_id=execution_id,
            success=False,
            reason_code=reason_code,
            task_id=task_id,
            command=command,
        )

    @classmethod
    def timeout(
        cls,
        tool_id: str,
        execution_id: str,
        partial_stdout: str = "",
        *,
        task_id: str = "",
        command: str = "",
    ) -> "ToolResult":
        return cls(
            tool_id=tool_id,
            execution_id=execution_id,
            success=False,
            stdout=partial_stdout,
            reason_code="tool_timeout",
            truncated=bool(partial_stdout),
            task_id=task_id,
            command=command,
        )

    def to_test_result_artifact(self, *, task_id: str, command: str) -> dict[str, Any]:
        """Convert to legacy test_result_artifact.v1 dict for consumers that predate ToolResult. T010."""
        status = self._legacy_status()
        duration_ms = int((float(self.get("duration_seconds") or 0.0)) * 1000)
        return {
            "schema": "test_result_artifact.v1",
            "task_id": task_id,
            "command": command,
            "exit_code": self.get("exit_code") if self.get("exit_code") is not None else (0 if self.get("success") else 1),
            "status": status,
            "stdout_ref": str(self.get("stdout") or "") or "<empty>",
            "stderr_ref": str(self.get("stderr") or "") or "<empty>",
            "output_summary": (
                f"Execution status={status}, duration_ms={duration_ms}, "
                f"tool_id={self.get('tool_id')}, execution_id={self.get('execution_id')}"
            ),
            "failure_hints": ([self["reason_code"]] if self.get("reason_code") else []),
        }


# ── ResourceLimits (EW-T018) ──────────────────────────────────────────────────

class ResourceLimits(BaseModel):
    """Per-invocation resource limits. EW-T018."""
    timeout_seconds: float = 30.0
    max_output_chars: int = 32_000
    max_artifact_bytes: int = 10 * 1024 * 1024   # 10 MiB
    max_files_touched: int = 50

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return v

    @field_validator("max_output_chars", "max_artifact_bytes", "max_files_touched")
    @classmethod
    def _positive_limits(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("limit must be > 0")
        return v


# ── ToolInvocationEnvelope (EW-T014) ─────────────────────────────────────────

class ToolInvocationEnvelope(BaseModel):
    """Per-tool-call contract. Validates arguments before any invocation. EW-T014."""
    execution_id: str
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    capability_ref: str = ""
    context_refs: list[str] = Field(default_factory=list)
    approval_ref: str | None = None
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    invoked_at: float = Field(default_factory=time.time)

    @field_validator("execution_id", "tool_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    def validate_arguments(self, schema: dict[str, Any]) -> list[str]:
        """Validate arguments against a JSON schema subset.

        Returns a list of error strings; empty means valid.
        Only checks 'required' fields — full jsonschema validation is
        the caller's responsibility.
        """
        errors: list[str] = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for req in required:
            if req not in self.arguments:
                errors.append(f"missing required argument: {req!r}")
        for key, value in self.arguments.items():
            if key in properties:
                prop_type = properties[key].get("type")
                if prop_type and not _type_matches(value, prop_type):
                    errors.append(f"argument {key!r}: expected {prop_type}, got {type(value).__name__}")
        return errors

    def apply_output_limit(self, raw_output: str) -> tuple[str, bool]:
        """Truncate output to max_output_chars. Returns (output, was_truncated)."""
        limit = self.resource_limits.max_output_chars
        if len(raw_output) <= limit:
            return raw_output, False
        return raw_output[:limit], True


def _type_matches(value: Any, json_type: str) -> bool:
    mapping = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    expected = mapping.get(json_type)
    if expected is None:
        return True
    if json_type == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


# ── WorkerToolEntry ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkerToolEntry:
    """A single registered tool. EW-T013."""
    id: str
    kind: str                                  # e.g. "shell", "file_read", "patch", "search"
    capability_classes: tuple[str, ...]        # capability classes required to invoke this tool
    risk_class: str                            # low / medium / high / critical
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    side_effects: tuple[str, ...] = ()         # e.g. ("filesystem_write",)
    description: str = ""
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)

    def as_catalog_entry(self) -> dict[str, Any]:
        """Safe representation for Hub ToolRouter — no secrets, no internals."""
        return {
            "id": self.id,
            "kind": self.kind,
            "capability_classes": list(self.capability_classes),
            "risk_class": self.risk_class,
            "side_effects": list(self.side_effects),
            "description": self.description,
        }


# ── WorkerToolRegistry (EW-T013) ─────────────────────────────────────────────

class WorkerToolRegistry:
    """Registry of all tools available to this worker.

    Tools cannot be invoked unless present here AND allowed by ToolPolicy.
    """

    def __init__(self) -> None:
        self._tools: dict[str, WorkerToolEntry] = {}

    def register(self, entry: WorkerToolEntry) -> None:
        if entry.risk_class not in TOOL_RISK_CLASSES:
            raise ValueError(f"invalid risk_class {entry.risk_class!r} for tool {entry.id!r}")
        self._tools[entry.id] = entry

    def get(self, tool_id: str) -> WorkerToolEntry | None:
        return self._tools.get(tool_id)

    def is_registered(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def tools_for_capability(self, capability: str) -> list[WorkerToolEntry]:
        return [t for t in self._tools.values() if capability in t.capability_classes]

    def capability_catalog(self) -> list[dict[str, Any]]:
        """Normalized catalog for Hub ToolRouter — safe to return externally."""
        return [entry.as_catalog_entry() for entry in sorted(self._tools.values(), key=lambda e: e.id)]

    def validate_invocation(
        self, envelope: ToolInvocationEnvelope
    ) -> list[str]:
        """Return list of validation errors; empty means invocation can proceed."""
        entry = self._tools.get(envelope.tool_id)
        if entry is None:
            return [f"tool {envelope.tool_id!r} is not registered"]
        if not entry.input_schema:
            return []
        return envelope.validate_arguments(entry.input_schema)


# ── Built-in tool registry ────────────────────────────────────────────────────

# ── ResourceLimitEnforcer (T011) ─────────────────────────────────────────────

class ResourceLimitEnforcer:
    """Applies per-tool resource limits at service boundaries. AWF-T011.

    Used by callers that do not go through ToolInvocationEnvelope directly
    (e.g. NativeWorkerRuntimeService) to read limits from the registry.
    """

    def __init__(self, registry: WorkerToolRegistry) -> None:
        self._registry = registry

    def limits_for(self, tool_id: str) -> ResourceLimits:
        entry = self._registry.get(tool_id)
        return entry.resource_limits if entry is not None else ResourceLimits()

    def bound_output(self, raw: str, tool_id: str) -> tuple[str, bool]:
        """Truncate raw output to max_output_chars for this tool. Returns (output, truncated)."""
        limits = self.limits_for(tool_id)
        if len(raw) <= limits.max_output_chars:
            return raw, False
        return raw[: limits.max_output_chars], True

    def effective_timeout(self, tool_id: str, requested_seconds: float) -> float:
        """Return min(requested, registry_limit) so callers never exceed limits."""
        return min(requested_seconds, self.limits_for(tool_id).timeout_seconds)


def build_default_registry() -> WorkerToolRegistry:
    """Returns a registry pre-loaded with the standard Ananta tool set."""
    registry = WorkerToolRegistry()
    _DEFAULTS = [
        WorkerToolEntry(
            id="read_file",
            kind="file_read",
            capability_classes=("code_read",),
            risk_class="low",
            input_schema={"required": ["path"], "properties": {"path": {"type": "string"}}},
            description="Read a file within the workspace scope.",
        ),
        WorkerToolEntry(
            id="list_directory",
            kind="file_read",
            capability_classes=("code_read",),
            risk_class="low",
            input_schema={"required": ["path"], "properties": {"path": {"type": "string"}}},
            description="List directory contents within workspace scope.",
        ),
        WorkerToolEntry(
            id="propose_patch",
            kind="patch",
            capability_classes=("patch_propose",),
            risk_class="medium",
            side_effects=("artifact_patch",),
            input_schema={
                "required": ["path", "diff"],
                "properties": {"path": {"type": "string"}, "diff": {"type": "string"}},
            },
            description="Propose a unified-diff patch without modifying the main tree.",
        ),
        WorkerToolEntry(
            id="apply_patch",
            kind="patch",
            capability_classes=("patch_apply",),
            risk_class="high",
            side_effects=("filesystem_write",),
            input_schema={
                "required": ["patch_artifact_id"],
                "properties": {"patch_artifact_id": {"type": "string"}},
            },
            description="Apply an approved PatchArtifact to the workspace.",
        ),
        WorkerToolEntry(
            id="run_shell",
            kind="shell",
            capability_classes=("shell_execute",),
            risk_class="high",
            side_effects=("host_mutation",),
            input_schema={
                "required": ["command"],
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "env": {"type": "object"},
                },
            },
            description="Execute a shell command within workspace and scope constraints.",
        ),
        WorkerToolEntry(
            id="plan_shell",
            kind="shell",
            capability_classes=("shell_plan",),
            risk_class="low",
            side_effects=("artifact_command_plan",),
            input_schema={
                "required": ["goal"],
                "properties": {"goal": {"type": "string"}},
            },
            description="Produce a CommandPlanArtifact without executing.",
        ),
        WorkerToolEntry(
            id="run_tests",
            kind="test",
            capability_classes=("test_run",),
            risk_class="medium",
            side_effects=("artifact_test_result",),
            input_schema={
                "properties": {
                    "test_path": {"type": "string"},
                    "args": {"type": "array"},
                },
            },
            description="Run the test suite and return a structured TestResultArtifact.",
        ),
        WorkerToolEntry(
            id="memory_read",
            kind="memory",
            capability_classes=("memory_read",),
            risk_class="low",
            input_schema={
                "required": ["store", "query"],
                "properties": {"store": {"type": "string"}, "query": {"type": "string"}},
            },
            description="Read from a worker memory store.",
        ),
        WorkerToolEntry(
            id="memory_write",
            kind="memory",
            capability_classes=("memory_write",),
            risk_class="medium",
            side_effects=("persistent_state",),
            input_schema={
                "required": ["store", "key", "value"],
                "properties": {
                    "store": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
            description="Write to a worker memory store (requires approval when confirm_required).",
        ),
    ]
    for entry in _DEFAULTS:
        registry.register(entry)
    return registry
