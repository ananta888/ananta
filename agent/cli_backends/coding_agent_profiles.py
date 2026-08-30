"""Declarative catalog and CLI adapter for optional coding agents."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from agent.cli_backends.coding_agent_contract import (
    AuthStatus,
    CodingAgentCapabilities,
    CodingAgentDescriptor,
    CodingAgentProbe,
    CodingAgentRunRequest,
    CodingAgentRunResult,
    EventSink,
    FreeClass,
    IntegrationKind,
    ProcessRunnerPort,
    ProviderState,
)
from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess

_BASE_ENVIRONMENT = frozenset({"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"})


@dataclass(frozen=True, slots=True)
class CodingAgentCliProfile:
    descriptor: CodingAgentDescriptor
    binary_name: str
    version_arguments: tuple[str, ...]
    static_arguments: tuple[str, ...]
    permission_arguments: Mapping[str, tuple[str, ...]]
    auth_environment: tuple[str, ...] = ()
    passthrough_environment: tuple[str, ...] = ()
    model_flag: str | None = None
    resume_flag: str | None = None
    prompt_transport: str = "stdin"
    prompt_flag: str | None = None
    timeout_flag: str | None = None
    timeout_suffix: str = ""
    fixed_environment: Mapping[str, str] = field(default_factory=dict)
    accepted_version_major: int | None = None
    minimum_version: tuple[int, int, int] | None = None

    def __post_init__(self) -> None:
        if self.descriptor.integration_kind is not IntegrationKind.CLI:
            raise ValueError("coding_agent_cli_profile_kind_invalid")
        if self.prompt_transport not in {"stdin", "argument"}:
            raise ValueError("coding_agent_prompt_transport_invalid")
        if set(self.permission_arguments) != {"read_only", "workspace_write", "autonomous"}:
            raise ValueError("coding_agent_permission_contract_invalid")

    def command(self, executable: str, request: CodingAgentRunRequest) -> tuple[tuple[str, ...], str | None]:
        arguments = [executable, *self.static_arguments, *self.permission_arguments[request.permission_mode]]
        if self.model_flag and request.model:
            arguments.extend((self.model_flag, request.model))
        if request.session_id:
            if not self.resume_flag:
                raise ValueError("coding_agent_session_resume_unsupported")
            arguments.extend((self.resume_flag, request.session_id))
        if self.timeout_flag:
            timeout_value = f"{max(1, int(request.timeout_seconds))}{self.timeout_suffix}"
            arguments.extend((self.timeout_flag, timeout_value))
        if self.prompt_transport == "argument":
            if self.prompt_flag:
                arguments.append(self.prompt_flag)
            arguments.append(request.prompt)
            return tuple(arguments), None
        return tuple(arguments), request.prompt + "\n"


class CliCodingAgentProvider:
    """Profile-driven provider with injected process and discovery boundaries."""

    def __init__(
        self,
        profile: CodingAgentCliProfile,
        *,
        process_runner: ProcessRunnerPort | None = None,
        binary_resolver: Callable[[str], str | None] = shutil.which,
        environment: Mapping[str, str] | None = None,
        version_probe: Callable[[str, Sequence[str]], tuple[int, str]] | None = None,
    ) -> None:
        self.profile = profile
        self.descriptor = profile.descriptor
        self._runner = process_runner or BoundedCodingAgentProcess()
        self._resolve_binary = binary_resolver
        self._environment = environment if environment is not None else os.environ
        self._version_probe = version_probe or _default_version_probe

    def detect(self) -> CodingAgentProbe:
        binary = self._resolve_binary(self.profile.binary_name)
        if not binary:
            return CodingAgentProbe(
                descriptor=self.descriptor,
                state=ProviderState.NOT_INSTALLED,
                binary_path=None,
                version=None,
                auth_status=AuthStatus.UNKNOWN,
                reason_code="binary_not_installed",
            )
        return_code, version = self._version_probe(binary, self.profile.version_arguments)
        if return_code != 0 or not version:
            return CodingAgentProbe(
                descriptor=self.descriptor,
                state=ProviderState.ERROR,
                binary_path=binary,
                version=None,
                auth_status=self._auth_status(),
                reason_code="version_probe_failed",
            )
        if not self._version_supported(version):
            return CodingAgentProbe(
                descriptor=self.descriptor,
                state=ProviderState.ERROR,
                binary_path=binary,
                version=version,
                auth_status=self._auth_status(),
                reason_code="version_unverified",
            )
        auth_status = self._auth_status()
        return CodingAgentProbe(
            descriptor=self.descriptor,
            state=ProviderState.READY,
            binary_path=binary,
            version=version,
            auth_status=auth_status,
            reason_code="ready" if auth_status is AuthStatus.READY else "auth_status_unverified",
        )

    def run(self, request: CodingAgentRunRequest, *, event_sink: EventSink | None = None) -> CodingAgentRunResult:
        probe = self.detect()
        if probe.state is not ProviderState.READY or probe.binary_path is None:
            return CodingAgentRunResult(
                provider_id=self.descriptor.provider_id,
                return_code=127,
                stdout="",
                stderr="",
                reason_code=probe.reason_code,
                duration_ms=0,
            )
        argv, input_text = self.profile.command(probe.binary_path, request)
        environment = self._allowed_environment()
        execution = self._runner.run(
            argv,
            cwd=request.workspace,
            environment=environment,
            timeout_seconds=request.timeout_seconds,
            cancellation=request.cancellation,
            maximum_output_chars=request.maximum_output_chars,
            input_text=input_text,
            event_sink=event_sink,
            secret_values=tuple(
                value
                for name in self.profile.auth_environment
                if (value := environment.get(name, ""))
            ),
        )
        reason_code = execution.reason_code
        if execution.return_code != 0 and _looks_like_quota_exhaustion(execution.stderr):
            reason_code = "quota_exhausted"
        return CodingAgentRunResult(
            provider_id=self.descriptor.provider_id,
            return_code=execution.return_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
            reason_code=reason_code,
            duration_ms=execution.duration_ms,
            output_truncated=execution.output_truncated,
        )

    def _version_supported(self, raw_version: str) -> bool:
        if self.profile.accepted_version_major is None and self.profile.minimum_version is None:
            return True
        match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", raw_version)
        if match is None:
            return False
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        if self.profile.accepted_version_major is not None and version[0] != self.profile.accepted_version_major:
            return False
        return self.profile.minimum_version is None or version >= self.profile.minimum_version

    def _allowed_environment(self) -> dict[str, str]:
        allowed = _BASE_ENVIRONMENT.union(self.profile.auth_environment).union(self.profile.passthrough_environment)
        result = {name: value for name in allowed if (value := self._environment.get(name)) is not None}
        result.update({"CI": "1", "NO_COLOR": "1"})
        result.update({str(name): str(value) for name, value in self.profile.fixed_environment.items()})
        return result

    def _auth_status(self) -> AuthStatus:
        if not self.profile.auth_environment:
            return AuthStatus.UNKNOWN
        if any(self._environment.get(name) for name in self.profile.auth_environment):
            return AuthStatus.READY
        # Cached CLI-owned credentials are intentionally not read by Ananta.
        return AuthStatus.UNKNOWN


def _capabilities(**overrides: bool) -> CodingAgentCapabilities:
    values = {
        "headless": True,
        "structured_output": True,
        "session_resume": False,
        "streaming": True,
        "tools": True,
        "mcp": False,
        "images": False,
        "reasoning": True,
        "git_changes": True,
        "sandbox": False,
    }
    values.update(overrides)
    return CodingAgentCapabilities(**values)


def _descriptor(
    provider_id: str,
    display_name: str,
    free_class: FreeClass,
    *,
    capabilities: CodingAgentCapabilities | None = None,
    integration_kind: IntegrationKind = IntegrationKind.CLI,
    automation_reason: str = "official_headless_interface",
) -> CodingAgentDescriptor:
    return CodingAgentDescriptor(
        provider_id=provider_id,
        display_name=display_name,
        integration_kind=integration_kind,
        free_class=free_class,
        capabilities=capabilities or _capabilities(),
        enabled_by_default=False,
        automation_reason=automation_reason,
    )


CLI_PROFILES = {
    "qwen_code": CodingAgentCliProfile(
        descriptor=_descriptor(
            "qwen_code",
            "Qwen Code",
            FreeClass.OPEN_SOURCE_BYOK,
            capabilities=_capabilities(session_resume=True, mcp=True),
        ),
        binary_name="qwen",
        version_arguments=("--version",),
        static_arguments=("--output-format", "stream-json", "--safe-mode", "--max-tool-calls", "50"),
        permission_arguments={
            "read_only": ("--approval-mode", "plan"),
            "workspace_write": ("--approval-mode", "auto-edit"),
            "autonomous": ("--approval-mode", "yolo", "--max-session-turns", "30"),
        },
        auth_environment=(
            "ANTHROPIC_API_KEY",
            "BAILIAN_CODING_PLAN_API_KEY",
            "DASHSCOPE_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        ),
        passthrough_environment=(
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "GEMINI_MODEL",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "QWEN_MODEL",
        ),
        model_flag="--model",
        resume_flag="--resume",
        prompt_transport="argument",
        prompt_flag="-p",
        timeout_flag="--max-wall-time",
        timeout_suffix="s",
        accepted_version_major=0,
        minimum_version=(0, 22, 0),
    ),
    "gemini_cli": CodingAgentCliProfile(
        descriptor=_descriptor(
            "gemini_cli",
            "Google Gemini CLI",
            FreeClass.FREE_TIER_LIMITED,
            capabilities=_capabilities(session_resume=True, mcp=True, sandbox=True),
        ),
        binary_name="gemini",
        version_arguments=("--version",),
        static_arguments=("--output-format", "stream-json", "--sandbox"),
        permission_arguments={
            "read_only": ("--approval-mode", "plan"),
            "workspace_write": ("--approval-mode", "auto_edit"),
            "autonomous": ("--approval-mode", "yolo"),
        },
        auth_environment=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT"),
        model_flag="--model",
        resume_flag="--resume",
        prompt_transport="argument",
        prompt_flag="-p",
    ),
    "copilot_cli": CodingAgentCliProfile(
        descriptor=_descriptor("copilot_cli", "GitHub Copilot CLI", FreeClass.FREE_TIER_LIMITED),
        binary_name="copilot",
        version_arguments=("--version",),
        static_arguments=("--output-format=json", "--no-ask-user", "--no-remote"),
        permission_arguments={
            "read_only": (),
            "workspace_write": ("--allow-tool=write",),
            "autonomous": ("--allow-all-tools", "--max-autopilot-continues=30"),
        },
        auth_environment=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        model_flag="--model",
        resume_flag="--resume",
        prompt_transport="argument",
        prompt_flag="-p",
    ),
    "aider": CodingAgentCliProfile(
        descriptor=_descriptor(
            "aider",
            "Aider",
            FreeClass.OPEN_SOURCE_BYOK,
            capabilities=_capabilities(structured_output=False, session_resume=False, mcp=False, sandbox=False),
        ),
        binary_name="aider",
        version_arguments=("--version",),
        static_arguments=("--yes", "--no-auto-commits", "--no-git"),
        permission_arguments={
            "read_only": ("--dry-run",),
            "workspace_write": (),
            "autonomous": (),
        },
        auth_environment=(
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        ),
        passthrough_environment=("OPENAI_API_BASE", "OPENAI_BASE_URL"),
        model_flag="--model",
        prompt_transport="argument",
        prompt_flag="--message",
    ),
    "cline": CodingAgentCliProfile(
        descriptor=_descriptor("cline", "Cline", FreeClass.OPEN_SOURCE_BYOK),
        binary_name="cline",
        version_arguments=("--version",),
        static_arguments=("--json",),
        permission_arguments={
            "read_only": ("--plan", "--auto-approve", "false"),
            "workspace_write": ("--auto-approve", "true"),
            "autonomous": ("--auto-approve", "true"),
        },
        auth_environment=("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"),
        prompt_transport="argument",
        timeout_flag="--timeout",
        fixed_environment={
            "CLINE_COMMAND_PERMISSIONS": '{"allow":["git *","npm *","pytest *"],"deny":["rm -rf *","sudo *"]}'
        },
    ),
    "kilo_code": CodingAgentCliProfile(
        descriptor=_descriptor(
            "kilo_code",
            "Kilo Code",
            FreeClass.OPEN_SOURCE_BYOK,
            capabilities=_capabilities(session_resume=False, mcp=True),
        ),
        binary_name="kilo",
        version_arguments=("--version",),
        static_arguments=("run",),
        permission_arguments={"read_only": (), "workspace_write": ("--auto",), "autonomous": ("--auto",)},
        auth_environment=("KILO_API_KEY", "KILOCODE_API_KEY"),
        model_flag="--model",
        prompt_transport="argument",
    ),
}


EXTERNAL_DESCRIPTORS = {
    "jules": _descriptor(
        "jules",
        "Google Jules",
        FreeClass.FREE_TIER_LIMITED,
        integration_kind=IntegrationKind.CLOUD_AGENT,
        capabilities=_capabilities(session_resume=True, streaming=False, sandbox=True),
        automation_reason="official_alpha_api",
    ),
    "windsurf": _descriptor(
        "windsurf",
        "Windsurf",
        FreeClass.PAID_OR_UNKNOWN,
        integration_kind=IntegrationKind.IDE_EXTERNAL,
        capabilities=CodingAgentCapabilities(headless=False, structured_output=False),
        automation_reason="official_headless_interface_unavailable",
    ),
}

EXISTING_DESCRIPTORS = {
    "codex": _descriptor(
        "codex",
        "OpenAI Codex CLI",
        FreeClass.PAID_OR_UNKNOWN,
        capabilities=_capabilities(session_resume=True, mcp=True, sandbox=True),
        automation_reason="existing_ananta_backend",
    ),
    "claude_code": _descriptor(
        "claude_code",
        "Claude Code CLI",
        FreeClass.PAID_OR_UNKNOWN,
        capabilities=_capabilities(session_resume=True, mcp=True, sandbox=True),
        automation_reason="existing_ananta_backend",
    ),
    "opencode": _descriptor(
        "opencode",
        "OpenCode",
        FreeClass.OPEN_SOURCE_BYOK,
        capabilities=_capabilities(session_resume=True, mcp=True),
        automation_reason="existing_ananta_backend",
    ),
    "mistral_code": _descriptor(
        "mistral_code",
        "Mistral Code",
        FreeClass.PAID_OR_UNKNOWN,
        capabilities=_capabilities(session_resume=False, mcp=False),
        automation_reason="existing_ananta_backend",
    ),
}


def coding_agent_descriptors() -> tuple[CodingAgentDescriptor, ...]:
    descriptors = [profile.descriptor for profile in CLI_PROFILES.values()]
    descriptors.extend(EXTERNAL_DESCRIPTORS.values())
    descriptors.extend(EXISTING_DESCRIPTORS.values())
    return tuple(sorted(descriptors, key=lambda item: item.provider_id))


def build_cli_coding_agent_provider(
    provider_id: str,
    **kwargs: object,
) -> CliCodingAgentProvider:
    profile = CLI_PROFILES.get(str(provider_id or "").strip().lower())
    if profile is None:
        raise ValueError("coding_agent_cli_provider_unsupported")
    return CliCodingAgentProvider(profile, **kwargs)  # type: ignore[arg-type]


def run_profile_coding_agent(
    provider_id: str,
    *,
    prompt: str,
    model: str | None,
    timeout: int,
    workdir: str | None,
    session_id: str | None = None,
    permission_mode: str = "workspace_write",
) -> tuple[int, str, str]:
    try:
        request = CodingAgentRunRequest(
            prompt=prompt,
            workspace=Path(workdir or os.getcwd()),
            timeout_seconds=float(timeout),
            model=model,
            session_id=session_id,
            permission_mode=permission_mode,
        )
        result = build_cli_coding_agent_provider(provider_id).run(request)
    except ValueError as exc:
        return 64, "", str(exc)
    return result.return_code, result.stdout, result.stderr or ("" if result.succeeded else result.reason_code)


def _default_version_probe(executable: str, arguments: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - absolute executable from shutil.which
            (executable, *arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    return completed.returncode, output[0][:200] if output else ""


def _looks_like_quota_exhaustion(stderr: str) -> bool:
    normalized = str(stderr or "").lower()
    return any(marker in normalized for marker in ("quota exceeded", "rate limit", "usage limit"))


__all__ = [
    "CLI_PROFILES",
    "EXTERNAL_DESCRIPTORS",
    "EXISTING_DESCRIPTORS",
    "CliCodingAgentProvider",
    "CodingAgentCliProfile",
    "build_cli_coding_agent_provider",
    "coding_agent_descriptors",
    "run_profile_coding_agent",
]
