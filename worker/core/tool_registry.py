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

class ToolResult(BaseModel):
    """Structured output from a single tool invocation. EW-T014."""
    tool_id: str
    execution_id: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    files_read: list[str] = Field(default_factory=list)
    files_written: list[str] = Field(default_factory=list)
    patches: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    reason_code: str | None = None
    truncated: bool = False
    duration_seconds: float | None = None

    @classmethod
    def denied(cls, tool_id: str, execution_id: str, reason_code: str) -> "ToolResult":
        return cls(
            tool_id=tool_id,
            execution_id=execution_id,
            success=False,
            reason_code=reason_code,
        )

    @classmethod
    def timeout(cls, tool_id: str, execution_id: str, partial_stdout: str = "") -> "ToolResult":
        return cls(
            tool_id=tool_id,
            execution_id=execution_id,
            success=False,
            stdout=partial_stdout,
            reason_code="tool_timeout",
            truncated=bool(partial_stdout),
        )


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
