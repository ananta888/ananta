"""Optional no-tools Pi execution; authorization remains with the task owner."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from agent.cli_backends.coding_agent_contract import (
    AuthStatus,
    CodingAgentCapabilities,
    CodingAgentDescriptor,
    CodingAgentEvent,
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
from agent.cli_backends.pi_configuration import (
    PI_PROVIDER_NAME,
    PI_VERSION,
    isolated_pi_configuration,
    validate_pi_target,
)
from agent.cli_backends.pi_events import PiProtocolError, parse_pi_one_shot
from agent.cli_backends.pi_policy import PiInvocationPolicy
from agent.cli_backends.pi_runtime import pi_sdk_command
from agent.cli_backends.provisioning import CliBackendProvisioningError, get_cli_backend_provisioner
from ananta_contracts.provider_invocation import ProviderInvocationBlocked

if TYPE_CHECKING:
    from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget


class PiCodingAgentProvider:
    descriptor = CodingAgentDescriptor(
        provider_id="pi", display_name="Pi", integration_kind=IntegrationKind.CLI,
        free_class=FreeClass.OPEN_SOURCE_BYOK, enabled_by_default=False,
        capabilities=CodingAgentCapabilities(headless=True, structured_output=True),
    )

    def __init__(
        self, *, enabled: bool = False, target: CodingAgentInferenceTarget | None = None,
        authorize: Callable[[CodingAgentRunRequest], bool] | None = None,
        execution_policy: PiInvocationPolicy | None = None,
        process_runner: ProcessRunnerPort | None = None,
        runtime_probe: Callable[[], Mapping[str, object]] | None = None,
        command_builder: Callable[[str], tuple[str, ...]] | None = None,
        runtime_root: Path | None = None, environment: Mapping[str, str] | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("pi_enabled_flag_invalid")
        self._enabled, self._target, self._authorize = enabled, target, authorize
        self._execution_policy = execution_policy
        self._runner = process_runner if process_runner is not None else BoundedCodingAgentProcess()
        self._runtime_probe = runtime_probe or (lambda: get_cli_backend_provisioner().status("pi"))
        self._runtime_root = runtime_root
        source = environment if environment is not None else os.environ
        self._environment = {key: source[key] for key in ("PATH", "LANG", "LC_ALL") if key in source}
        self._command_builder = command_builder or (
            lambda binary: pi_sdk_command(binary, path_environment=self._environment.get("PATH", os.defpath))
        )

    def detect(self) -> CodingAgentProbe:
        if not self._enabled:
            return self._probe(ProviderState.UNSUPPORTED, "pi_disabled")
        reason = validate_pi_target(self._target)
        if reason:
            state = ProviderState.AUTH_REQUIRED if reason == "pi_auth_required" else ProviderState.UNSUPPORTED
            return self._probe(state, reason)
        try:
            runtime = self._runtime_probe()
        except (CliBackendProvisioningError, OSError, ValueError):
            return self._probe(ProviderState.ERROR, "pi_runtime_probe_failed")
        if not isinstance(runtime, Mapping):
            return self._probe(ProviderState.ERROR, "pi_runtime_probe_failed")
        if runtime.get("installed") is not True:
            return self._probe(ProviderState.NOT_INSTALLED, "binary_not_installed")
        binary = runtime.get("binary_path")
        if (
            runtime.get("status") != "ready" or runtime.get("version") != PI_VERSION
            or runtime.get("version_probe") != {"rc": 0, "stdout": PI_VERSION, "stderr": ""}
            or not isinstance(binary, str) or not Path(binary).is_absolute() or "\x00" in binary
        ):
            return self._probe(ProviderState.ERROR, "pi_version_unverified")
        return self._probe(ProviderState.READY, "ready", binary)

    def _probe(self, state: ProviderState, reason: str, binary: str | None = None) -> CodingAgentProbe:
        auth = AuthStatus.READY if state is ProviderState.READY else AuthStatus.UNKNOWN
        if state is ProviderState.AUTH_REQUIRED:
            auth = AuthStatus.REQUIRED
        return CodingAgentProbe(
            descriptor=self.descriptor, state=state, binary_path=binary, version=PI_VERSION if binary else None,
            auth_status=auth, reason_code=reason,
        )

    def _authorized(self, request: CodingAgentRunRequest) -> bool:
        try:
            return self._authorize is not None and self._authorize(request) is True
        except Exception:
            return False

    def run(self, request: CodingAgentRunRequest, *, event_sink: EventSink | None = None) -> CodingAgentRunResult:
        started = time.monotonic()

        def result(code: int, reason: str, text: str = "", truncated: bool = False) -> CodingAgentRunResult:
            return CodingAgentRunResult(
                "pi", code, text, "", reason, int((time.monotonic() - started) * 1000), truncated,
            )

        if not self._enabled:
            return result(77, "pi_disabled")
        if request.cancellation.is_set():
            return result(130, "cancelled")
        if request.session_id is not None or request.permission_mode != "read_only":
            return result(64, "pi_capability_unsupported")
        if not self._authorized(request):
            return result(77, "pi_execution_not_authorized")
        if self._target is not None and request.model is not None and request.model != self._target.model:
            return result(77, "pi_model_not_authorized")
        probe = self.detect()
        if probe.state is not ProviderState.READY or probe.binary_path is None:
            return result(127, probe.reason_code)
        if self._execution_policy is None:
            return result(77, "pi_hub_policy_required")
        try:
            projection = self._execution_policy.project(self._target)
            with isolated_pi_configuration(
                projection.target, project=request.workspace, runtime_root=self._runtime_root,
                max_tokens=projection.max_tokens,
            ) as invocation:
                remaining = request.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    return result(124, "timeout")
                if not self._authorized(request):
                    return result(77, "pi_execution_not_authorized")
                self._execution_policy.reserve(projection, prompt=request.prompt, cwd=invocation.cwd)
                if not self._authorized(request):
                    return result(77, "pi_execution_not_authorized")
                remaining = min(
                    request.timeout_seconds - (time.monotonic() - started),
                    self._execution_policy.remaining_seconds(projection),
                )
                if remaining <= 0 or request.cancellation.is_set():
                    return result(130, "cancelled") if request.cancellation.is_set() else result(124, "timeout")
                environment = self._environment | {
                    "CI": "1", "NO_COLOR": "1", "PI_OFFLINE": "1", "PI_TELEMETRY": "0",
                    "PI_CODING_AGENT_DIR": str(invocation.config_directory), "ANANTA_PI_API_KEY": self._target.api_key,
                }
                execution = self._runner.run(
                    (*self._command_builder(probe.binary_path), *invocation.arguments),
                    cwd=invocation.cwd, environment=environment,
                    timeout_seconds=remaining, cancellation=request.cancellation,
                    maximum_output_chars=request.maximum_output_chars, input_text=request.prompt + "\n",
                    secret_values=(self._target.api_key,),
                )
                if request.cancellation.is_set():
                    return result(130, "cancelled")
                if not self._authorized(request):
                    return result(77, "pi_execution_not_authorized")
                if self._execution_policy.remaining_seconds(projection) <= 0:
                    return result(124, "timeout")
                if execution.return_code != 0 or execution.output_truncated or execution.reason_code != "completed":
                    reason = execution.reason_code if execution.reason_code in {
                        "timeout", "cancelled", "output_limit_exceeded", "process_io_failed",
                    } else "pi_process_failed"
                    return result(execution.return_code or 1, reason, truncated=execution.output_truncated)
                if execution.stderr.strip():
                    return result(65, "pi_process_diagnostics")
                answer = parse_pi_one_shot(
                    execution.stdout, provider=PI_PROVIDER_NAME, model=self._target.model, workspace=invocation.cwd,
                )
                # JSON escaping can hide a secret from the process line redactor.
                answer = answer.replace(self._target.api_key, "<redacted>")
        except ProviderInvocationBlocked as exc:
            return result(77, exc.reason_code)
        except PiProtocolError as exc:
            return result(65, str(exc))
        except (OSError, ValueError):
            return result(74, "pi_runtime_failed")
        if event_sink is not None:
            try:
                event_sink(CodingAgentEvent(sequence=1, stream="stdout", text=answer))
            except Exception:
                pass
        return result(0, "completed", answer)
