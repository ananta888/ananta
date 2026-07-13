from __future__ import annotations

import time

import pytest

from agent.services.model_invocation_service import LLMUnavailableError, ModelInvocationService
from agent.services.provider_invocation_middleware import (
    AtomicProviderBudgetLedger,
    BoundedProviderEventSink,
    InMemoryProviderCache,
    ProviderInvocationBlocked,
    ProviderInvocationContext,
    ProviderInvocationMiddleware,
    RetryBudgetOwnerProviderAdapter,
)
from agent.services.workflow_runtime import InMemoryExecutionOwnershipStore


def context(**overrides) -> ProviderInvocationContext:
    values = {
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "policy_version": "policy-v1",
        "prompt_version": "prompt-v1",
        "external_egress_allowed": True,
        "max_attempts": 2,
        "max_total_tokens": 10000,
        "secret_refs": ("secret-value",),
    }
    values.update(overrides)
    return ProviderInvocationContext(**values)


def test_external_egress_is_fail_closed() -> None:
    subject = ProviderInvocationMiddleware()

    with pytest.raises(ProviderInvocationBlocked, match="provider_egress_denied"):
        subject.prepare(
            context=context(external_egress_allowed=False),
            provider="cloud",
            model="model-a",
            endpoint_url="https://provider.example/v1/chat/completions",
            payload={"messages": [{"role": "user", "content": "hello"}]},
        )


def test_secret_refs_are_redacted_before_transport() -> None:
    subject = ProviderInvocationMiddleware()

    prepared = subject.prepare(
        context=context(),
        provider="local",
        model="model-a",
        endpoint_url="http://localhost:11434/v1/chat/completions",
        payload={"messages": [{"role": "user", "content": "secret-value"}]},
    )

    assert prepared.payload["messages"][0]["content"] == "***REDACTED***"


def test_cache_key_is_tenant_policy_model_and_prompt_version_bound() -> None:
    subject = ProviderInvocationMiddleware()
    base = context(cache_enabled=True)

    first = subject.prepare(
        context=base,
        provider="local",
        model="model-a",
        endpoint_url="http://localhost/v1",
        payload={"messages": []},
    )
    second = subject.prepare(
        context=context(tenant_id="tenant-2", cache_enabled=True),
        provider="local",
        model="model-a",
        endpoint_url="http://localhost/v1",
        payload={"messages": []},
    )

    assert first.cache_key != second.cache_key


def test_retry_and_deadline_budgets_are_atomic() -> None:
    ledger = AtomicProviderBudgetLedger()
    subject = ProviderInvocationMiddleware(budgets=ledger)
    bound = context(max_attempts=1)
    kwargs = {
        "context": bound,
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": []},
    }

    subject.prepare(**kwargs)
    with pytest.raises(ProviderInvocationBlocked, match="provider_retry_budget_exceeded"):
        subject.prepare(**kwargs)

    with pytest.raises(ProviderInvocationBlocked, match="provider_deadline_exceeded"):
        subject.prepare(**{**kwargs, "context": context(deadline_epoch_seconds=time.time() - 1)})


def test_provider_retry_consumes_shared_hub_budget_and_missing_port_fails_closed() -> None:
    owner = InMemoryExecutionOwnershipStore()
    owner.consume_retry(
        tenant_id="tenant-1",
        run_id="run-1",
        retry_id="temporal-retry-1",
        category="temporal_activity",
        maximum=2,
    )
    bound = context(
        max_attempts=8,
        require_hub_retry_budget=True,
        combined_retry_maximum=2,
        retry_attempt=1,
        retry_id="provider-retry-1",
        workflow_id="workflow-1",
        step_id="step-1",
        plan_hash="a" * 64,
        authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
        attempt_id="attempt-1",
        fencing_token=1,
    )
    request = {
        "context": bound,
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": []},
    }

    unavailable = ProviderInvocationMiddleware()
    with pytest.raises(ProviderInvocationBlocked, match="combined_retry_budget_unavailable"):
        unavailable.prepare(**request)

    shared = ProviderInvocationMiddleware(
        retry_budgets=RetryBudgetOwnerProviderAdapter(owner)
    )
    shared.prepare(**request)
    # Delivery of the same retry reservation is idempotent.
    ProviderInvocationMiddleware(
        retry_budgets=RetryBudgetOwnerProviderAdapter(owner)
    ).prepare(**request)
    exhausted = ProviderInvocationMiddleware(
        retry_budgets=RetryBudgetOwnerProviderAdapter(owner)
    )
    with pytest.raises(ProviderInvocationBlocked, match="retry_budget_exhausted"):
        exhausted.prepare(
            **{
                **request,
                "context": context(
                    max_attempts=8,
                    require_hub_retry_budget=True,
                    combined_retry_maximum=2,
                    retry_attempt=2,
                    retry_id="provider-retry-2",
                    workflow_id="workflow-1",
                    step_id="step-1",
                    plan_hash="a" * 64,
                    authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
                    attempt_id="attempt-1",
                    fencing_token=1,
                ),
            }
        )


def test_workflow_provider_token_and_cost_budget_uses_shared_hub_port() -> None:
    hub_budget = AtomicProviderBudgetLedger()
    bound = context(
        max_attempts=1,
        max_total_tokens=100,
        require_hub_provider_budget=True,
        selected_provider_id="local",
        selected_model_id="model-a",
        provider_binding_id="provider-binding:test",
        provider_transport_mode="hub_bound",
        provider_decision_reason="hub_provider_policy_selected",
        workflow_id="workflow-1",
        step_id="step-1",
        plan_hash="a" * 64,
        authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
        attempt_id="attempt-1",
        fencing_token=1,
    )
    request = {
        "context": bound,
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": []},
    }

    with pytest.raises(ProviderInvocationBlocked, match="hub_budget_unavailable"):
        ProviderInvocationMiddleware().prepare(**request)
    first = ProviderInvocationMiddleware(hub_budgets=hub_budget).prepare(**request)
    assert first.uses_hub_budget is True
    assert first.budget_reservation_id.startswith("provider-call-")
    with pytest.raises(ProviderInvocationBlocked, match="retry_budget_exceeded"):
        ProviderInvocationMiddleware(hub_budgets=hub_budget).prepare(
            **{
                **request,
                "payload": {"messages": [{"role": "user", "content": "second"}]},
            }
        )


def test_hub_provider_selection_is_bound_to_the_actual_invocation() -> None:
    subject = ProviderInvocationMiddleware()
    bound = context(
        selected_provider_id="local-selected",
        selected_model_id="model-selected",
    )

    with pytest.raises(ProviderInvocationBlocked, match="provider_selection_binding_mismatch"):
        subject.prepare(
            context=bound,
            provider="local-other",
            model="model-selected",
            endpoint_url="http://localhost/v1",
            payload={"messages": []},
        )


def test_hub_provider_budget_requires_complete_server_selection_binding() -> None:
    with pytest.raises(
        ProviderInvocationBlocked, match="provider_selection_binding_required"
    ):
        context(
            require_hub_provider_budget=True,
            workflow_id="workflow-1",
            step_id="step-1",
            plan_hash="a" * 64,
            authorization_envelope={"schema": "ananta.runtime_authorization.v1"},
            attempt_id="attempt-1",
            fencing_token=1,
        ).assert_valid()


def test_completion_is_cached_and_events_never_contain_payload() -> None:
    cache = InMemoryProviderCache()
    events = BoundedProviderEventSink()
    subject = ProviderInvocationMiddleware(cache=cache, events=events)
    bound = context(cache_enabled=True)
    kwargs = {
        "context": bound,
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": [{"role": "user", "content": "hello"}]},
    }
    prepared = subject.prepare(**kwargs)
    subject.complete(
        prepared,
        provider="local",
        model="model-a",
        response={"choices": [], "usage": {"total_tokens": 3}},
    )

    cached = subject.prepare(**kwargs)

    assert cached.cached_response == {"choices": [], "usage": {"total_tokens": 3}}
    assert all("payload" not in event and "messages" not in event for event in events.snapshot())


def test_model_invocation_sends_only_middleware_redacted_payload(monkeypatch) -> None:
    subject = ProviderInvocationMiddleware()
    monkeypatch.setattr(ModelInvocationService, "_provider_middleware", subject)

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    sent = {}

    def post(url, *, json, headers, timeout):
        sent.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return Response()

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", post)

    result = ModelInvocationService._make_single_chat_call(
        [{"role": "user", "content": "secret-value"}],
        tools=None,
        response_format=None,
        attempt={
            "provider": "local",
            "url": "http://localhost:11434/v1/chat/completions",
            "model": "model-a",
            "timeout": 3,
            "profile": None,
        },
        resolution_info={},
        provider_context=context(),
    )

    assert sent["json"]["messages"][0]["content"] == "***REDACTED***"
    assert result["metadata"]["provider_middleware"]["cache_hit"] is False


def test_legacy_generate_text_uses_same_redaction_budget_and_event_middleware(monkeypatch) -> None:
    import agent.llm_integration as integration
    import agent.services.provider_invocation_middleware as middleware_module

    events = BoundedProviderEventSink()
    subject = ProviderInvocationMiddleware(events=events)
    monkeypatch.setattr(middleware_module, "_DEFAULT_MIDDLEWARE", subject)
    monkeypatch.setattr(integration, "_check_circuit_breaker", lambda _provider: True)
    monkeypatch.setattr(integration, "_check_rate_limit", lambda _provider: True)
    monkeypatch.setattr(integration, "_report_llm_success", lambda _provider: None)
    captured: dict = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return "safe-response"

    monkeypatch.setattr(integration, "_execute_llm_call", execute)
    result = integration.generate_text(
        "secret-value",
        provider="openai",
        model="model-a",
        base_url="http://localhost:9999/v1",
        provider_context=context(secret_refs=("secret-value",), max_attempts=1),
    )

    assert result == "safe-response"
    assert captured["prompt"] == "***REDACTED***"
    assert {event["event_type"] for event in events.snapshot()} >= {
        "provider.call.authorized",
        "provider.call.completed",
    }


def test_model_invocation_does_not_call_network_after_egress_denial(monkeypatch) -> None:
    monkeypatch.setattr(ModelInvocationService, "_provider_middleware", ProviderInvocationMiddleware())
    called = False

    def post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr("agent.services.model_invocation_service.requests.post", post)

    with pytest.raises(LLMUnavailableError, match="provider_egress_denied"):
        ModelInvocationService._make_single_chat_call(
            [{"role": "user", "content": "hello"}],
            tools=None,
            response_format=None,
            attempt={
                "provider": "cloud",
                "url": "https://provider.example/v1/chat/completions",
                "model": "model-a",
                "timeout": 3,
                "profile": None,
            },
            resolution_info={},
            provider_context=context(external_egress_allowed=False),
        )

    assert called is False
