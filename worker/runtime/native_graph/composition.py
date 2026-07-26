"""Production composition for one Hub-delegated Native graph node."""

from __future__ import annotations

import contextlib
import contextvars
import re
import uuid
from typing import Any, Iterator, Mapping, Protocol

from ananta_contracts.workflow_worker_gateway import (
    SideEffectGatewayReceipt,
    WorkflowWorkerBinding,
)
from worker.runtime.native_graph.contracts import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime
from worker.runtime.native_graph.ports import NativeAuthorizationVerifierPort
from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter
from worker.runtime.workflow_hub_gateway import (
    HttpWorkflowHubDecisionClient,
    WorkflowHubDecisionError,
)


class NativeHubExecutionScope:
    """Task-local binding for Hub revalidation and the shared side-effect ledger."""

    def __init__(self, client: HttpWorkflowHubDecisionClient) -> None:
        self._client = client
        self._command: contextvars.ContextVar[NativeNodeCommand | None] = (
            contextvars.ContextVar("native_graph_hub_command", default=None)
        )
        self._task: contextvars.ContextVar[dict[str, Any] | None] = (
            contextvars.ContextVar("native_graph_hub_task", default=None)
        )

    @contextlib.contextmanager
    def bind(
        self, command: NativeNodeCommand, *, task: Mapping[str, Any]
    ) -> Iterator[None]:
        snapshot = dict(task)
        if str(snapshot.get("id") or "").strip() == "":
            raise WorkflowHubDecisionError("native_hub_task_snapshot_invalid")
        command_token = self._command.set(command)
        task_token = self._task.set(snapshot)
        try:
            yield
        finally:
            self._task.reset(task_token)
            self._command.reset(command_token)

    def task_snapshot(self, *, hub_task_id: str) -> dict[str, Any]:
        task = self._task.get()
        if task is None or str(task.get("id") or "").strip() != str(
            hub_task_id
        ).strip():
            raise WorkflowHubDecisionError("native_hub_task_snapshot_binding_mismatch")
        return dict(task)

    def revalidate(self, envelope) -> bool:
        command = self._bound_command()
        if envelope.envelope_id != command.authorization.envelope_id:
            return False
        try:
            response = self._client.command(
                "authorize_execution",
                binding=self._binding(command).to_dict(),
                adapter_kind="native",
                attempt_id=command.attempt_id,
                fencing_token=command.fencing_token,
            )
        except WorkflowHubDecisionError:
            return False
        return bool(response.get("allowed", False))

    def claim(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
    ) -> Any:
        command = self._assert_execution_binding(
            operation_id=operation_id,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        response = self._client.command(
            "native_side_effect_claim",
            binding=self._binding(command).to_dict(),
            operation_id=operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        return SideEffectGatewayReceipt.from_mapping(response)

    def complete(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        result_ref: str,
    ) -> Any:
        return self._finish(
            "native_side_effect_complete",
            operation_id=operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            result_ref=result_ref,
        ).record

    def fail(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> Any:
        return self._finish(
            "native_side_effect_fail",
            operation_id=operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            reason_code=failure_code,
        ).record

    def mark_uncertain(
        self,
        operation_id: str,
        *,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        failure_code: str,
    ) -> Any:
        return self._finish(
            "native_side_effect_uncertain",
            operation_id=operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            reason_code=failure_code,
        ).record

    def _finish(
        self,
        operation: str,
        *,
        operation_id: str,
        expected_revision: int,
        fencing_token: int,
        attempt_id: str,
        **values: Any,
    ) -> SideEffectGatewayReceipt:
        command = self._assert_execution_binding(
            operation_id=operation_id,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
        )
        response = self._client.command(
            operation,
            binding=self._binding(command).to_dict(),
            operation_id=operation_id,
            expected_revision=expected_revision,
            fencing_token=fencing_token,
            attempt_id=attempt_id,
            **values,
        )
        return SideEffectGatewayReceipt.from_mapping(response)

    def _bound_command(self) -> NativeNodeCommand:
        command = self._command.get()
        if command is None:
            raise WorkflowHubDecisionError("native_hub_execution_scope_missing")
        return command

    def _assert_execution_binding(
        self, *, operation_id: str, fencing_token: int, attempt_id: str
    ) -> NativeNodeCommand:
        command = self._bound_command()
        if (
            command.operation_id != operation_id
            or command.fencing_token != int(fencing_token)
            or command.attempt_id != str(attempt_id)
        ):
            raise WorkflowHubDecisionError("native_hub_execution_binding_mismatch")
        return command

    @staticmethod
    def _binding(command: NativeNodeCommand) -> WorkflowWorkerBinding:
        return WorkflowWorkerBinding.from_mapping(
            {
                "tenant_id": command.tenant_id,
                "workflow_id": command.workflow_id,
                "run_id": command.run_id,
                "step_id": command.node.node_id,
                "plan_hash": command.plan_hash,
                "policy_version": command.policy_version,
                "authorization_envelope": command.authorization.to_dict(),
                "correlation_id": command.run_id,
            }
        )

class ConfiguredNativeNodePolicy:
    def __init__(self, *, allowed_task_types: frozenset[str]) -> None:
        self._allowed_task_types = allowed_task_types

    def allow_node(self, command: NativeNodeCommand) -> tuple[bool, str]:
        if command.node.task_kind not in self._allowed_task_types:
            return False, "native_task_kind_not_configured"
        if command.node.node_type not in {"task", "component"}:
            return False, "native_node_type_unsupported"
        return True, "native_node_policy_allowed"


class NativeWorkerCommandRuntimePort(Protocol):
    def execute(
        self,
        *,
        hub_task_id: str,
        task: dict[str, Any],
        command: str,
        trace_id: str,
        timeout_seconds: int,
        agent_config: dict[str, Any],
    ) -> Mapping[str, Any]: ...


class TaskScopedNativeWorkerExecutor:
    """Worker-only adapter over injected workspace and command-runtime ports."""

    def __init__(self, *, runtime: Any, workspaces: Any) -> None:
        self._runtime = runtime
        self._workspaces = workspaces

    def execute(
        self,
        *,
        hub_task_id: str,
        task: dict[str, Any],
        command: str,
        trace_id: str,
        timeout_seconds: int,
        agent_config: dict[str, Any],
    ) -> Mapping[str, Any]:
        workspace = self._workspaces.resolve_workspace_context(task=task)
        return self._runtime.execute_and_verify_command(
            tid=hub_task_id,
            task=task,
            command=command,
            trace_id=trace_id,
            worker_profile="balanced",
            profile_source="workflow_adapter",
            timeout_seconds=timeout_seconds,
            workspace_dir=workspace.workspace_dir,
            native_runtime_payload=None,
            agent_cfg=agent_config,
        )


class NativeTaskScopedNodeHandler:
    """Adapter over the existing bounded Native worker command runtime."""

    def __init__(
        self,
        *,
        agent_config: Mapping[str, Any],
        task_snapshots: NativeHubExecutionScope,
        executor: NativeWorkerCommandRuntimePort,
    ) -> None:
        self._agent_config = dict(agent_config)
        self._task_snapshots = task_snapshots
        self._executor = executor

    def execute(
        self, command: NativeNodeCommand, *, hub_task_id: str
    ) -> NativeNodeResult:
        raw_command = self._command_text(command)
        if not raw_command:
            return self._failed(command, hub_task_id, "native_node_command_input_required")
        try:
            task_payload = self._task_snapshots.task_snapshot(
                hub_task_id=hub_task_id
            )
            agent_config = self._hub_bound_agent_config(command)
            budget = command.node.budget
            result = self._executor.execute(
                hub_task_id=hub_task_id,
                task=task_payload,
                command=raw_command,
                trace_id=f"native-graph:{command.command_id}",
                timeout_seconds=int(
                    max(1, min(float(budget.timeout_seconds if budget else 300), 3600))
                ),
                agent_config=agent_config,
            )
        except Exception as exc:  # noqa: BLE001 - runtime adapter must fail closed
            return self._failed(
                command,
                hub_task_id,
                _reason(exc, "native_node_handler_failed"),
            )
        status = "completed" if str(result.get("status") or "") == "completed" else "failed"
        reason = "" if status == "completed" else _reason(
            result.get("failure_type"), "native_node_execution_failed"
        )
        artifacts = (
            self._artifact_refs(
                command,
                runtime_result=result,
            )
            if status == "completed"
            else {}
        )
        if (
            status == "completed"
            and set(artifacts)
            != set(command.node.output_artifacts)
        ):
            return self._failed(
                command,
                hub_task_id,
                "native_node_materialized_artifacts_missing",
            )
        output_data = {
            "status": status,
            "exit_code": int(result.get("exit_code") or 0),
            "output": str(result.get("output") or "")[:16_384],
            "policy_classification_summary": str(
                result.get("policy_classification_summary") or ""
            )[:512],
        }
        value = NativeNodeResult(
            result_id=f"nres-{uuid.uuid4().hex}",
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=command.node.node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status=status,
            output_data=output_data,
            artifact_refs=artifacts,
            budget_usage={},
            reason_code=reason,
        )
        value.assert_valid()
        return value

    def _hub_bound_agent_config(
        self, command: NativeNodeCommand
    ) -> dict[str, Any]:
        config = dict(self._agent_config)
        binding = command.provider_binding
        if binding is None:
            return config
        # The worker consumes the Hub choice as data.  It does not consult a
        # second registry or choose a fallback provider/model itself.
        config["default_provider"] = binding.provider_id
        config["default_model"] = binding.model_id
        llm_config = config.get("llm_config")
        llm_config = dict(llm_config) if isinstance(llm_config, Mapping) else {}
        llm_config.update(
            {"provider": binding.provider_id, "model": binding.model_id}
        )
        config["llm_config"] = llm_config
        if command.provider_profile_bindings:
            # These values were produced and validated by the Hub contract.
            # The Worker only transports exact copies to invocation seams.
            config["provider_context"] = dict(command.provider_context)
            config["provider_contexts_by_profile_id"] = {
                profile_id: dict(context)
                for profile_id, context in (
                    command.provider_contexts_by_profile_id.items()
                )
            }
            config["provider_attempt_plan"] = [
                item.to_dict() for item in command.provider_attempt_plan
            ]
        return config

    @staticmethod
    def _command_text(command: NativeNodeCommand) -> str:
        workflow_input = command.input_data.get("workflow_input")
        source = workflow_input if isinstance(workflow_input, Mapping) else command.input_data
        return str(
            source.get("command")
            or source.get("shell_command")
            or source.get(command.node.node_id)
            or ""
        ).strip()

    @staticmethod
    def _artifact_refs(
        command: NativeNodeCommand,
        *,
        runtime_result: Mapping[str, Any],
    ) -> dict[str, str]:
        raw = runtime_result.get("artifact_refs")
        if not isinstance(raw, Mapping):
            return {}
        declared = set(command.node.output_artifacts)
        if set(raw) != declared:
            return {}
        normalized: dict[str, str] = {}
        for artifact_id in command.node.output_artifacts:
            reference = raw.get(artifact_id)
            if (
                not isinstance(reference, str)
                or not reference.startswith("artifact://")
                or len(reference) > 2_048
                or any(
                    character.isspace()
                    or ord(character) < 32
                    for character in reference
                )
            ):
                return {}
            normalized[artifact_id] = reference
        return normalized

    @staticmethod
    def _failed(
        command: NativeNodeCommand, hub_task_id: str, reason_code: str
    ) -> NativeNodeResult:
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
            status="failed",
            reason_code=reason_code,
        )


def build_native_graph_worker_task_adapter(
    *,
    client: HttpWorkflowHubDecisionClient,
    agent_config: Mapping[str, Any],
    executor: NativeWorkerCommandRuntimePort | None = None,
    authorization_verifier: NativeAuthorizationVerifierPort | None = None,
) -> NativeGraphWorkerTaskAdapter | None:
    runtime_cfg = agent_config.get("worker_runtime")
    runtime_cfg = dict(runtime_cfg) if isinstance(runtime_cfg, Mapping) else {}
    native_cfg = runtime_cfg.get("native_graph")
    native_cfg = dict(native_cfg) if isinstance(native_cfg, Mapping) else {}
    if (
        not bool(native_cfg.get("enabled", False))
        or executor is None
        or authorization_verifier is None
    ):
        return None
    allowed_task_types = frozenset(
        str(item).strip()
        for item in native_cfg.get("allowed_task_types", ())
        if str(item).strip()
    )
    capabilities = frozenset(
        str(item).strip()
        for item in native_cfg.get("capabilities", ())
        if str(item).strip()
    )
    if not allowed_task_types or not capabilities:
        return None
    if (
        len(allowed_task_types) > 128
        or len(capabilities) > 128
        or any(
            len(value) > 128 or "\x00" in value
            for value in (*allowed_task_types, *capabilities)
        )
    ):
        raise ValueError("native_graph_worker_configuration_invalid")
    scope = NativeHubExecutionScope(client)
    runtime = NativeDelegatedNodeRuntime(
        handler=NativeTaskScopedNodeHandler(
            agent_config=agent_config,
            task_snapshots=scope,
            executor=executor,
        ),
        authorization_verifier=authorization_verifier,
        policy=ConfiguredNativeNodePolicy(allowed_task_types=allowed_task_types),
        capabilities=capabilities,
        ledger=scope,
        hub_revalidator=scope,
    )
    return NativeGraphWorkerTaskAdapter(runtime, execution_scope=scope)

def _reason(value: object, fallback: str) -> str:
    text = str(getattr(value, "reason_code", value) or "").strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:,-]{0,159}", text):
        return text
    return fallback


__all__ = [
    "ConfiguredNativeNodePolicy",
    "NativeHubExecutionScope",
    "NativeTaskScopedNodeHandler",
    "NativeWorkerCommandRuntimePort",
    "TaskScopedNativeWorkerExecutor",
    "build_native_graph_worker_task_adapter",
]
