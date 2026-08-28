"""Closed contracts shared by optional coding-agent integrations."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Callable, Mapping, Protocol, Sequence


class IntegrationKind(StrEnum):
    CLI = "cli"
    API = "api"
    CLOUD_AGENT = "cloud_agent"
    IDE_EXTERNAL = "ide_external"


class FreeClass(StrEnum):
    INCLUDED_FREE_INFERENCE = "included_free_inference"
    FREE_TIER_LIMITED = "free_tier_limited"
    OPEN_SOURCE_BYOK = "open_source_byok"
    PAID_OR_UNKNOWN = "paid_or_unknown"


class AuthStatus(StrEnum):
    READY = "ready"
    REQUIRED = "auth_required"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ProviderState(StrEnum):
    READY = "ready"
    NOT_INSTALLED = "not_installed"
    AUTH_REQUIRED = "auth_required"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CodingAgentCapabilities:
    headless: bool
    structured_output: bool
    session_resume: bool = False
    streaming: bool = False
    tools: bool = False
    mcp: bool = False
    images: bool = False
    reasoning: bool = False
    git_changes: bool = False
    sandbox: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {item.name: bool(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class CodingAgentDescriptor:
    provider_id: str
    display_name: str
    integration_kind: IntegrationKind
    free_class: FreeClass
    capabilities: CodingAgentCapabilities
    enabled_by_default: bool = False
    automation_reason: str = "official_headless_interface"

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip().lower()
        if not provider_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in provider_id
        ):
            raise ValueError("coding_agent_provider_id_invalid")
        if not self.display_name.strip():
            raise ValueError("coding_agent_display_name_required")
        object.__setattr__(self, "provider_id", provider_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "integration_kind": self.integration_kind.value,
            "free_class": self.free_class.value,
            "enabled_by_default": self.enabled_by_default,
            "automation_reason": self.automation_reason,
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CodingAgentProbe:
    descriptor: CodingAgentDescriptor
    state: ProviderState
    binary_path: str | None
    version: str | None
    auth_status: AuthStatus
    reason_code: str

    def as_dict(self) -> dict[str, object]:
        return {
            **self.descriptor.as_dict(),
            "state": self.state.value,
            "binary_path": self.binary_path,
            "version": self.version,
            "auth_status": self.auth_status.value,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class CodingAgentRunRequest:
    prompt: str
    workspace: Path
    timeout_seconds: float
    workspace_root: Path | None = None
    model: str | None = None
    session_id: str | None = None
    permission_mode: str = "workspace_write"
    maximum_output_chars: int = 1_000_000
    cancellation: Event = field(default_factory=Event, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 200_000:
            raise ValueError("coding_agent_prompt_invalid")
        workspace = self.workspace.resolve()
        if not workspace.is_dir():
            raise ValueError("coding_agent_workspace_invalid")
        workspace_root = (self.workspace_root or workspace).resolve()
        if not workspace_root.is_dir() or (workspace != workspace_root and workspace_root not in workspace.parents):
            raise ValueError("coding_agent_workspace_outside_allowed_root")
        if not 1 <= self.timeout_seconds <= 86_400:
            raise ValueError("coding_agent_timeout_invalid")
        if self.permission_mode not in {"read_only", "workspace_write", "autonomous"}:
            raise ValueError("coding_agent_permission_mode_invalid")
        if not 1_024 <= self.maximum_output_chars <= 4_000_000:
            raise ValueError("coding_agent_output_limit_invalid")
        if self.session_id is not None and (
            not self.session_id.strip()
            or len(self.session_id) > 200
            or any(ord(value) < 32 for value in self.session_id)
        ):
            raise ValueError("coding_agent_session_id_invalid")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "workspace_root", workspace_root)


@dataclass(frozen=True, slots=True)
class CodingAgentEvent:
    sequence: int
    stream: str
    text: str


@dataclass(frozen=True, slots=True)
class CodingAgentRunResult:
    provider_id: str
    return_code: int
    stdout: str
    stderr: str
    reason_code: str
    duration_ms: int
    output_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True, slots=True)
class ProcessExecutionResult:
    return_code: int
    stdout: str
    stderr: str
    reason_code: str
    duration_ms: int
    output_truncated: bool = False


EventSink = Callable[[CodingAgentEvent], None]


class CodingAgentProvider(Protocol):
    descriptor: CodingAgentDescriptor

    def detect(self) -> CodingAgentProbe: ...

    def run(self, request: CodingAgentRunRequest, *, event_sink: EventSink | None = None) -> CodingAgentRunResult: ...


class ProcessRunnerPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        cancellation: Event,
        maximum_output_chars: int,
        input_text: str | None = None,
        event_sink: EventSink | None = None,
        secret_values: Sequence[str] = (),
    ) -> ProcessExecutionResult: ...


__all__ = [
    "AuthStatus",
    "CodingAgentCapabilities",
    "CodingAgentDescriptor",
    "CodingAgentEvent",
    "CodingAgentProbe",
    "CodingAgentProvider",
    "CodingAgentRunRequest",
    "CodingAgentRunResult",
    "EventSink",
    "FreeClass",
    "IntegrationKind",
    "ProcessRunnerPort",
    "ProcessExecutionResult",
    "ProviderState",
]
