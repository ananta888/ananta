"""One no-tools Pi turn inside the existing Hub-delegated Native node runtime."""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent.cli_backends.coding_agent_contract import CodingAgentProvider, CodingAgentRunRequest, CodingAgentRunResult
from agent.cli_backends.pi_policy import PiInvocationPolicy
from agent.cli_backends.pi_provider import PiCodingAgentProvider
from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget
from ananta_contracts.provider_invocation import ProviderInvocationContext
from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.pi_context import PiTaskContextPort, pi_context_bundle_reference

if TYPE_CHECKING:
    from agent.services.provider_invocation_middleware import ProviderBudgetPort
    from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope

PI_NATIVE_TASK_KIND = "pi_coding_agent"
PI_NATIVE_CAPABILITY = "coding.agent.pi"


class PiTaskScopePort(Protocol):
    def task_snapshot(self, *, hub_task_id: str) -> dict[str, Any]: ...

    def revalidate(self, envelope: RuntimeAuthorizationEnvelope) -> bool: ...


class NativePiNodeHandler:
    """Consumes a verified node; cannot create tasks, choose models or sign."""

    def __init__(
        self,
        *,
        scope: PiTaskScopePort,
        budget: ProviderBudgetPort,
        workspace_for_task: Callable[[Mapping[str, Any]], Path],
        credential_for_profile: Callable[[str], str],
        provider_factory: Callable[..., CodingAgentProvider] = PiCodingAgentProvider,
        runtime_root: Path | None = None,
        clock: Callable[[], float] = time.time,
        context_reader: PiTaskContextPort | None = None,
    ) -> None:
        self._scope, self._budget = scope, budget
        self._workspace, self._credential = workspace_for_task, credential_for_profile
        self._provider_factory, self._runtime_root, self._clock = provider_factory, runtime_root, clock
        self._context_reader = context_reader

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        started_at = self._clock()
        task, prompt = self._bound_task_and_prompt(command, hub_task_id=hub_task_id)
        if self._scope.revalidate(command.authorization) is not True:
            raise ValueError("pi_execution_not_authorized")
        approved_context = self._read_context(task, command)
        if approved_context is not None:
            # Context stays model input, never system/extension/tool authority.
            prompt = json.dumps({
                "task": prompt,
                "context_bundle": {"id": approved_context.bundle_id, "content": approved_context.content},
            }, ensure_ascii=True)
        context = self._invocation_context(command, started_at=started_at)
        target = self._target(context)
        workspace = self._workspace(task)
        remaining = context.deadline_epoch_seconds - self._clock()
        if remaining < 1:
            raise ValueError("pi_deadline_expired")
        request = CodingAgentRunRequest(
            prompt=prompt,
            workspace=workspace,
            model=context.selected_model_id,
            permission_mode="read_only",
            timeout_seconds=remaining,
            maximum_output_chars=262_144,
        )
        provider = self._provider_factory(
            enabled=True,
            target=target,
            runtime_root=self._runtime_root,
            execution_policy=PiInvocationPolicy(context=context, budget=self._budget, clock=self._clock),
            authorize=lambda candidate: (
                candidate is request and self._scope.revalidate(command.authorization) is True
                and self._read_context(task, command) == approved_context
            ),
        )
        return self._result(command, hub_task_id=hub_task_id, result=provider.run(request))

    def _bound_task_and_prompt(self, command: NativeNodeCommand, *, hub_task_id: str) -> tuple[dict[str, Any], str]:
        command.assert_valid()
        if (
            command.node.task_kind != PI_NATIVE_TASK_KIND
            or PI_NATIVE_CAPABILITY not in command.node.required_capabilities
        ):
            raise ValueError("pi_native_capability_required")
        if command.node.side_effect_class not in {"none", "read"} or command.node.output_artifacts:
            raise ValueError("pi_native_write_capability_unsupported")
        if command.artifact_refs or command.node.input_artifacts:
            raise ValueError("pi_native_artifact_context_unsupported")
        if not command.provider_context or not command.provider_profile_bindings:
            raise ValueError("pi_hub_context_required")
        task = self._scope.task_snapshot(hub_task_id=hub_task_id)
        if (
            str(task.get("id") or "") != hub_task_id
            or (task.get("worker_execution_context") or {}).get("native_node_command") != command.to_dict()
        ):
            raise ValueError("pi_native_task_binding_mismatch")
        workflow_input = command.input_data.get("workflow_input")
        inputs = workflow_input if isinstance(workflow_input, Mapping) else command.input_data
        prompt = inputs.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200_000:
            raise ValueError("pi_native_prompt_required")
        return task, prompt

    def _read_context(self, task: Mapping[str, Any], command: NativeNodeCommand):
        reference = pi_context_bundle_reference(task)
        if reference is None:
            return None
        if self._context_reader is None:
            raise ValueError("pi_context_bundle_transport_required")
        projection = self._context_reader.read(task=task, command=command)
        if projection is None:
            raise ValueError("pi_context_bundle_projection_required")
        projection.assert_valid()
        return projection

    def _target(self, context: ProviderInvocationContext) -> CodingAgentInferenceTarget:
        return CodingAgentInferenceTarget(
            client_id="pi",
            provider_id=context.selected_provider_id,
            model=context.selected_model_id,
            cli_model=context.selected_model_id,
            base_url=context.provider_endpoint_identity,
            target_kind="hub_bound_openai",
            api_key=self._credential(context.provider_profile_id),
            api_key_source="worker_managed_profile_credential",
        )

    @staticmethod
    def _result(command: NativeNodeCommand, *, hub_task_id: str, result: CodingAgentRunResult) -> NativeNodeResult:
        completed = result.succeeded and not result.output_truncated and len(result.stdout) <= 16_384
        return NativeNodeResult(
            result_id=f"nres-{uuid.uuid4().hex}",
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=command.node.node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status="completed" if completed else "failed",
            output_data={
                "provider_id": "pi",
                "model_id": command.provider_binding.model_id,
                "output": result.stdout if completed else "",
                "exit_code": result.return_code,
            },
            reason_code=""
            if completed
            else ("pi_native_output_limit_exceeded" if result.succeeded else result.reason_code),
        )

    def _invocation_context(self, command: NativeNodeCommand, *, started_at: float) -> ProviderInvocationContext:
        context = ProviderInvocationContext.from_value(command.provider_context)
        timeout = command.node.budget.timeout_seconds if command.node.budget else 300.0
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("pi_native_timeout_invalid")
        deadlines = [command.authorization.expires_at, started_at + min(timeout, 3600)]
        if context.deadline_epoch_seconds is not None:
            deadlines.append(context.deadline_epoch_seconds)
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in deadlines):
            raise ValueError("pi_deadline_required")
        # Ordinary provider-call correlation, never a SRC_* or RUN_* identity.
        return replace(
            context.for_provider_call(f"pi-call-{uuid.uuid4().hex}"),
            deadline_epoch_seconds=min(deadlines),
        )
