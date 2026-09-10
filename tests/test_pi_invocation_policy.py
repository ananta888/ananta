"""Headless synthetic Hub policy/budget boundaries, without model or DNS access."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.cli_backends.pi_policy import PiInvocationPolicy
from ananta_contracts.provider_invocation import ProviderBudgetDecision, ProviderInvocationBlocked
from tests.test_pi_coding_agent_provider import Runner, budget_port, hub_context, policy, provider, request, target


@pytest.mark.parametrize("context", [None, {}, "legacy"])
def test_pi_never_creates_legacy_context(context):
    with pytest.raises(ProviderInvocationBlocked, match="pi_hub_context_required"):
        PiInvocationPolicy(context=context, budget=budget_port())


@pytest.mark.parametrize(
    "changes",
    [
        {"require_hub_provider_budget": False},
        {"provider_call_id": ""},
        {"provider_endpoint_identity": ""},
        {"authorization_envelope": {}},
        {"fencing_token": 0},
        {"selected_model_id": "other"},
        {"selected_provider_id": "openrouter"},
        {"provider_transport_mode": "legacy"},
        {"max_total_tokens": 0},
        {"max_completion_tokens_per_call": True},
        {"deadline_epoch_seconds": None},
        {"deadline_epoch_seconds": float("nan")},
        {"deadline_epoch_seconds": float("inf")},
        {"deadline_epoch_seconds": 100},
        {"retry_attempt": 1},
        {"require_hub_provider_attempt_budget": True, "provider_profile_id": ""},
        {"require_hub_retry_budget": True, "combined_retry_maximum": 2},
    ],
)
def test_pi_unbound_mismatched_or_unsupported_context_never_executes(tmp_path, changes):
    budget, runner = budget_port(), Runner()
    result = provider(tmp_path, runner, execution_policy=policy(context=hub_context(**changes), budget=budget)).run(
        request(tmp_path),
    )
    assert not result.succeeded and not runner.calls and not budget.reserve.called


def test_pi_enabled_provider_without_policy_never_executes(tmp_path):
    runner = Runner()
    result = provider(tmp_path, runner, execution_policy=None).run(request(tmp_path))
    assert result.reason_code == "pi_hub_policy_required" and not runner.calls


@pytest.mark.parametrize(
    "url", ["http://other:11434/v1", "http://169.254.169.254/v1", "http://ollama:11434/api/generate"]
)
def test_pi_unapproved_endpoint_never_contacts_budget_or_process(tmp_path, url):
    runner, budget = Runner(), budget_port()
    value = provider(tmp_path, runner, target=target(base_url=url), execution_policy=policy(budget=budget))
    result = value.run(request(tmp_path))
    assert result.reason_code == "pi_endpoint_not_authorized" and not runner.calls and not budget.reserve.called


def test_pi_small_hub_output_budget_reaches_actual_private_sdk_configuration(tmp_path):
    budget = budget_port()
    runner = Runner()
    run = runner.run

    def inspect(argv, **kwargs):
        config = kwargs["environment"]["PI_CODING_AGENT_DIR"]
        with open(f"{config}/models.json") as handle:
            model = json.load(handle)["providers"]["ananta"]["models"][0]
        assert model["maxTokens"] == 37
        return run(argv, **kwargs)

    runner.run = inspect
    result = provider(
        tmp_path,
        runner,
        execution_policy=policy(
            context=hub_context(max_completion_tokens_per_call=37),
            budget=budget,
        ),
    ).run(request(tmp_path))
    assert result.succeeded and budget.reserve.call_count == 1
    call = budget.reserve.call_args.kwargs
    assert call["context"].max_completion_tokens_per_call == 37 and call["reservation_id"] == "test-call"
    assert call["estimated_prompt_tokens"] > len("Explain this code.")
    assert not budget.reconcile.called  # Unknown tokenizer-specific usage is never refunded.


@pytest.mark.parametrize(
    "decision",
    [None, True, ProviderBudgetDecision(False, "denied", 1, 8192, 0), ProviderBudgetDecision(True, "allowed", 1, 1, 0)],
)
def test_pi_missing_denied_or_inadequate_reservation_never_executes(tmp_path, decision):
    budget, runner = budget_port(), Runner()
    budget.reserve.side_effect = None
    budget.reserve.return_value = decision
    result = provider(tmp_path, runner, execution_policy=policy(budget=budget)).run(request(tmp_path))
    assert result.reason_code == "pi_hub_budget_denied" and not runner.calls
    assert not list(tmp_path.glob("ananta-pi-*"))


@pytest.mark.parametrize("failure", [False, True])
def test_pi_reservation_is_one_shot_even_when_hub_response_is_uncertain(tmp_path, failure):
    budget, runner = budget_port(), Runner()
    if failure:
        budget.reserve.side_effect = TimeoutError("private detail")
    value = provider(tmp_path, runner, execution_policy=policy(budget=budget))
    first = value.run(request(tmp_path))
    second = value.run(request(tmp_path))
    assert first.succeeded is not failure
    if failure:
        assert first.reason_code == "pi_hub_budget_unavailable"
    assert second.reason_code == "pi_invocation_already_reserved"
    assert budget.reserve.call_count == 1 and len(runner.calls) == (0 if failure else 1)


def test_pi_excessive_prompt_is_rejected_before_budget_or_process(tmp_path):
    budget, runner = budget_port(), Runner()
    result = provider(tmp_path, runner, execution_policy=policy(budget=budget)).run(
        request(tmp_path, prompt="ä" * 4000),
    )
    assert result.reason_code == "pi_prompt_budget_exceeded" and not budget.reserve.called and not runner.calls


def test_pi_context_is_copied_before_caller_mutation():
    context, budget = hub_context(), budget_port()
    value = policy(context=context, budget=budget)
    context.authorization_envelope.clear()
    assert value.project(target()).max_tokens == 1024


def test_pi_deadline_expiring_during_hub_reservation_prevents_process(tmp_path):
    now = [100]
    budget, runner = budget_port(), Runner()
    reserve = budget.reserve.side_effect

    def expire(**kwargs):
        now[0] = 200
        return reserve(**kwargs)

    budget.reserve.side_effect = expire
    result = provider(tmp_path, runner, execution_policy=policy(budget=budget, clock=lambda: now[0])).run(
        request(tmp_path)
    )
    assert result.reason_code == "timeout" and budget.reserve.call_count == 1 and not runner.calls


def test_pi_hub_deadline_bounds_process_timeout(tmp_path):
    runner = Runner()
    result = provider(tmp_path, runner, execution_policy=policy(context=hub_context(deadline_epoch_seconds=103))).run(
        request(tmp_path),
    )
    assert result.succeeded and runner.calls[0][1]["timeout_seconds"] == 3


def test_pi_authorization_expiry_cannot_be_widened_by_context_deadline(tmp_path):
    runner = Runner()
    context = hub_context(authorization_envelope={"synthetic": True, "expires_at": 102})
    result = provider(tmp_path, runner, execution_policy=policy(context=context)).run(request(tmp_path))
    assert result.succeeded and runner.calls[0][1]["timeout_seconds"] == 2


def test_pi_time_spent_revalidating_hub_authority_is_not_added_to_deadline(tmp_path):
    now, checks = [100], []
    runner = Runner()

    def authorize(_request):
        checks.append(True)
        if len(checks) == 3:
            now[0] = 200
        return True

    result = provider(tmp_path, runner, authorize=authorize, execution_policy=policy(clock=lambda: now[0])).run(
        request(tmp_path)
    )
    assert result.reason_code == "timeout" and not runner.calls


def test_pi_late_result_is_discarded_without_publishing_text(tmp_path):
    now, runner, sink = [100], Runner(), Mock()
    run = runner.run

    def expire(argv, **kwargs):
        result = run(argv, **kwargs)
        now[0] = 200
        return result

    runner.run = expire
    result = provider(tmp_path, runner, execution_policy=policy(clock=lambda: now[0])).run(
        request(tmp_path),
        event_sink=sink,
    )
    assert result.reason_code == "timeout" and result.stdout == "" and not sink.called


def test_pi_revocation_while_reserving_budget_prevents_process(tmp_path):
    runner, budget = Runner(), budget_port()
    result = provider(
        tmp_path, runner, execution_policy=policy(budget=budget), authorize=Mock(side_effect=[True, True, False])
    ).run(request(tmp_path))
    assert result.reason_code == "pi_execution_not_authorized" and not runner.calls
    assert budget.reserve.call_count == 1 and not budget.reconcile.called


def test_pi_external_provider_requires_egress_and_public_resolution():
    selected = target(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    context = hub_context(
        selected_provider_id="openrouter", provider_endpoint_identity="https://openrouter.ai/api/v1/chat/completions"
    )
    resolver = Mock(return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
    value = PiInvocationPolicy(context=context, budget=budget_port(), clock=lambda: 100, resolver=resolver)
    with pytest.raises(ProviderInvocationBlocked, match="pi_endpoint_not_authorized"):
        value.project(selected)
    assert not resolver.called
    value = PiInvocationPolicy(
        context=replace(context, external_egress_allowed=True),
        budget=budget_port(),
        clock=lambda: 100,
        resolver=resolver,
    )
    assert value.project(selected).target == selected
    resolver.return_value.append((2, 1, 6, "", ("127.0.0.1", 443)))
    with pytest.raises(ProviderInvocationBlocked, match="pi_endpoint_not_authorized"):
        value.project(selected)
