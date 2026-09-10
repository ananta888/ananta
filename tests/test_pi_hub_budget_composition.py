"""Pi against the actual Hub budget/lease service with synthetic Hub grants.

The existing test Hub owns signing, grants, assignments and the budget store.
The Worker only has a decision-client adapter; it never receives signing keys.
"""

from dataclasses import replace

import pytest

from agent.cli_backends.pi_policy import PiInvocationPolicy
from agent.services.hub_provider_context_factory import HubProviderContextSpec
from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayError
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)
from ananta_contracts.provider_invocation import ProviderInvocationContext
from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_COMMAND_SCHEMA
from tests.test_pi_coding_agent_provider import Runner, provider, request
from tests.test_workflow_worker_gateway_service import fixture as hub_fixture
from worker.runtime.workflow_hub_gateway import HubProviderBudgetAdapter, WorkflowHubDecisionError


class ServiceClient:
    def __init__(self, service):
        self.service, self.calls = service, []
        self.worker_id, self.worker_url = "worker-1", "http://worker-1:5000"

    def command(self, name, **values):
        self.calls.append((name, values))
        try:
            return self.service.execute(
                {"schema": WORKFLOW_WORKER_COMMAND_SCHEMA, "command": name, **values},
                authenticated_worker_id=self.worker_id,
                authenticated_worker_url=self.worker_url,
            )
        except WorkflowWorkerGatewayError as exc:
            raise WorkflowHubDecisionError(exc.reason_code) from exc


def composition():
    selected = ProviderProfileExecutionBinding(
        profile_id="pi-primary",
        binding=ProviderExecutionBinding(
            provider_id="ollama",
            model_id="selected-model",
            source="hub_model_profile_routing",
            reason_code="hub_provider_profile_selected",
            endpoint_identity="http://ollama:11434/v1/chat/completions",
        ),
    )
    entry = ProviderProfileAttemptPlanEntry.from_profile_binding(selected, maximum_attempts=1)
    service, base, events, _, _ = hub_fixture(
        provider_attempts=1,
        provider_attempt_plan=(entry,),
        budget_overrides={"tokens": 8192},
    )
    spec = HubProviderContextSpec(
        **base["binding"],
        attempt_id=base["attempt_id"],
        fencing_token=base["fencing_token"],
        prompt_version="synthetic-prompt-v1",
        max_attempts=1,
        max_total_tokens=8192,
        max_completion_tokens_per_call=32,
        max_cost_micros=1000,
        combined_retry_maximum=0,
        require_separate_provider_attempt_budget=True,
    )
    raw = spec.build(selected.binding, decision_reason="synthetic-hub-selection", profile_id=selected.profile_id)
    context = replace(
        ProviderInvocationContext.from_value(raw).for_provider_call("synthetic-pi-call-1"),
        deadline_epoch_seconds=raw["authorization_envelope"]["expires_at"],
    )
    return ServiceClient(service), context, events


def test_pi_consumes_existing_hub_profile_budget_with_exact_lease_binding(tmp_path):
    client, context, events = composition()
    runner = Runner()
    result = provider(
        tmp_path,
        runner,
        execution_policy=PiInvocationPolicy(
            context=context,
            budget=HubProviderBudgetAdapter(client),
        ),
    ).run(request(tmp_path))
    assert result.succeeded and len(runner.calls) == 1
    assert len(client.calls) == 1 and client.calls[0][0] == "provider_budget_reserve"
    sent = client.calls[0][1]
    assert sent["provider_profile_id"] == "pi-primary"
    assert sent["attempt_id"] == context.attempt_id and sent["fencing_token"] == context.fencing_token
    assert any(
        event.event_type == "workflow.budget.provider_reserved"
        for event in events.list_events(tenant_id=context.tenant_id, run_id=context.run_id)
    )


def test_pi_second_instance_cannot_get_another_hub_profile_attempt(tmp_path):
    client, context, _ = composition()
    budget, runner = HubProviderBudgetAdapter(client), Runner()
    first = provider(tmp_path, runner, execution_policy=PiInvocationPolicy(context=context, budget=budget))
    second = provider(
        tmp_path,
        runner,
        execution_policy=PiInvocationPolicy(
            context=context.for_provider_call("synthetic-pi-call-2"),
            budget=budget,
        ),
    )
    assert first.run(request(tmp_path)).succeeded
    result = second.run(request(tmp_path))
    assert result.reason_code == "pi_hub_budget_denied" and len(runner.calls) == 1


@pytest.mark.parametrize("mutation", ["worker", "fence", "attempt", "signature", "profile"])
def test_pi_actual_hub_rejects_stale_foreign_or_mutated_budget_authority(tmp_path, mutation):
    client, context, _ = composition()
    if mutation == "worker":
        client.worker_id, client.worker_url = "other-worker", "http://other-worker:5000"
    elif mutation == "fence":
        context = replace(context, fencing_token=context.fencing_token + 1)
    elif mutation == "attempt":
        context = replace(context, attempt_id="other-attempt")
    elif mutation == "signature":
        context.authorization_envelope["signature"] = "invalid-synthetic-signature"
    elif mutation == "profile":
        context = replace(context, provider_profile_id="unselected-profile")
    runner = Runner()
    result = provider(
        tmp_path,
        runner,
        execution_policy=PiInvocationPolicy(
            context=context,
            budget=HubProviderBudgetAdapter(client),
        ),
    ).run(request(tmp_path))
    assert result.reason_code == "pi_hub_budget_denied" and not runner.calls
    assert len(client.calls) == 1
