"""Pi executes only through a verified, task-scoped Native runtime."""

from dataclasses import asdict, replace
from unittest.mock import Mock

import pytest

from agent.services.workflow_runtime.execution_plan import ExecutionNode
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.provider_execution import ProviderExecutionBinding, ProviderProfileExecutionBinding
from tests.test_pi_coding_agent_provider import Runner, provider
from tests.test_pi_hub_budget_composition import composition
from worker.runtime.native_graph.authorization import HubBackedNativeAuthorizationVerifier
from worker.runtime.native_graph.composition import ConfiguredNativeNodePolicy, NativeHubExecutionScope
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime
from worker.runtime.native_graph.pi_node import PI_NATIVE_CAPABILITY, PI_NATIVE_TASK_KIND, NativePiNodeHandler
from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter
from worker.runtime.workflow_hub_gateway import HubProviderBudgetAdapter


def task_command(context):
    binding = ProviderExecutionBinding(
        provider_id="ollama",
        model_id="selected-model",
        source="hub_model_profile_routing",
        reason_code="hub_provider_profile_selected",
        endpoint_identity="http://ollama:11434/v1/chat/completions",
    )
    envelope = RuntimeAuthorizationEnvelope.from_mapping(context.authorization_envelope)
    return NativeNodeCommand(
        command_id="pi-test-command",
        control_task_id="control-test-task",
        tenant_id=context.tenant_id,
        workflow_id=context.workflow_id,
        run_id=context.run_id,
        plan_hash=context.plan_hash,
        policy_version=context.policy_version,
        attempt_id=context.attempt_id,
        fencing_token=context.fencing_token,
        node=ExecutionNode(
            node_id=context.step_id,
            task_kind=PI_NATIVE_TASK_KIND,
            required_capabilities=(PI_NATIVE_CAPABILITY, "text_generation"),
        ),
        authorization=envelope,
        input_data={"workflow_input": {"prompt": "Explain this code."}},
        provider_binding=binding,
        primary_profile_id=context.provider_profile_id,
        provider_profile_bindings=(ProviderProfileExecutionBinding(context.provider_profile_id, binding),),
        provider_attempt_plan=envelope.provider_attempt_plan,
        provider_maximum_attempts=1,
        provider_context=asdict(context),
        provider_contexts_by_profile_id={context.provider_profile_id: asdict(context)},
    )


def native_setup(tmp_path, *, mutate=lambda command: command, mutate_task=lambda task: task, runner=None):
    client, context, _ = composition()
    command = mutate(task_command(context))
    task = mutate_task(
        {
            "id": "hub-task-1",
            "worker_execution_context": {
                "schema": "ananta.native_graph_worker_context.v1",
                "runtime_path": "native_graph_node",
                "native_node_command": command.to_dict(),
            },
        }
    )
    scope, runner = NativeHubExecutionScope(client), runner or Runner()
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    credentials = Mock(return_value="synthetic-private-key")
    handler = NativePiNodeHandler(
        scope=scope,
        budget=HubProviderBudgetAdapter(client),
        workspace_for_task=lambda _: workspace,
        credential_for_profile=credentials,
        runtime_root=tmp_path,
        provider_factory=lambda **kwargs: provider(tmp_path, runner, **kwargs),
    )
    runtime = NativeDelegatedNodeRuntime(
        handler=handler,
        authorization_verifier=HubBackedNativeAuthorizationVerifier(),
        policy=ConfiguredNativeNodePolicy(allowed_task_types=frozenset({PI_NATIVE_TASK_KIND})),
        capabilities=frozenset({PI_NATIVE_CAPABILITY, "text_generation"}),
        hub_revalidator=scope,
    )
    return NativeGraphWorkerTaskAdapter(runtime, execution_scope=scope), task, runner, client, credentials


def test_pi_node_completes_via_real_hub_authority_budget_and_native_result(tmp_path):
    adapter, task, runner, client, credentials = native_setup(tmp_path)
    result = adapter.execute_task(task)
    assert result.status == "completed" and result.output_data["output"] == "A\u2028B"
    assert result.hub_task_id == task["id"] and result.node_id == "step-1"
    assert result.artifact_refs == {} and result.budget_usage == {}
    assert len(runner.calls) == 1 and credentials.call_args.args == ("pi-primary",)
    assert [name for name, _ in client.calls].count("provider_budget_reserve") == 1
    assert [name for name, _ in client.calls].count("authorize_execution") >= 4
    assert not (tmp_path / "project" / ".pi").exists()
    assert adapter.verification_update(result)["native_node_result"]["status"] == "completed"


def test_pi_native_nonce_replay_cannot_invoke_provider_twice(tmp_path):
    adapter, task, runner, _, _ = native_setup(tmp_path)
    assert adapter.execute_task(task).status == "completed"
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == "authorization_replay_detected"
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (
            lambda c: replace(c, node=replace(c.node, required_capabilities=("text_generation",))),
            "pi_native_capability_required",
        ),
        (
            lambda c: replace(c, node=replace(c.node, output_artifacts=("not-materialized",))),
            "pi_native_write_capability_unsupported",
        ),
        (
            lambda c: replace(c, node=replace(c.node, input_artifacts=("unhydrated",))),
            "pi_native_artifact_context_unsupported",
        ),
        (
            lambda c: replace(c, input_data={"command": "do not run this as a shell command"}),
            "pi_native_prompt_required",
        ),
    ],
)
def test_pi_native_unsupported_contract_never_reads_credentials_or_runs(tmp_path, mutation, reason):
    adapter, task, runner, _, credentials = native_setup(tmp_path, mutate=mutation)
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == reason
    assert not runner.calls and not credentials.called


def test_pi_native_unhydrated_context_reference_is_not_silently_ignored(tmp_path):
    adapter, task, runner, _, credentials = native_setup(
        tmp_path,
        mutate_task=lambda t: {**t, "context_bundle_id": "unhydrated-context"},
    )
    result = adapter.execute_task(task)
    assert result.reason_code == "pi_context_bundle_transport_required"
    assert not runner.calls and not credentials.called


def test_pi_native_foreign_worker_is_rejected_before_credentials(tmp_path):
    adapter, task, runner, client, credentials = native_setup(tmp_path)
    client.worker_id, client.worker_url = "foreign-worker", "http://foreign-worker:5000"
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == "authorization_hub_verification_failed"
    assert not runner.calls and not credentials.called


def test_pi_native_failure_never_materializes_artifact_or_partial_text(tmp_path):
    adapter, task, runner, _, _ = native_setup(tmp_path, runner=Runner(malformed=True))
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.output_data["output"] == ""
    assert result.artifact_refs == {} and not runner.calls[0][1]["cwd"].parent.exists()


def test_pi_native_hub_decision_requires_boolean_true(tmp_path):
    adapter, task, runner, client, credentials = native_setup(tmp_path)
    command = client.command

    def malformed(name, **values):
        if name == "authorize_execution":
            return {"allowed": "false"}
        return command(name, **values)

    client.command = malformed
    result = adapter.execute_task(task)
    assert result.status == "failed" and result.reason_code == "authorization_hub_verification_failed"
    assert not runner.calls and not credentials.called
