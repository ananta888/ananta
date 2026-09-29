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
from ananta_contracts.provider_endpoint_policy import (
    build_provider_request_url,
    normalize_provider_endpoint_identity,
    validate_provider_endpoint_resolution,
)


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


def hub_bound_context(
    *,
    provider: str,
    model: str,
    endpoint_identity: str = "",
    external_egress_allowed: bool = False,
) -> ProviderInvocationContext:
    return context(
        external_egress_allowed=external_egress_allowed,
        require_hub_provider_budget=True,
        selected_provider_id=provider,
        selected_model_id=model,
        provider_binding_id="provider-binding:test-endpoint",
        provider_endpoint_identity=endpoint_identity,
        provider_transport_mode="hub_bound",
        provider_decision_reason="hub_provider_policy_selected",
        workflow_id="workflow-1",
        step_id="step-1",
        plan_hash="a" * 64,
        authorization_envelope={
            "schema": "ananta.runtime_authorization.v1"
        },
        attempt_id="attempt-1",
        fencing_token=1,
    )


def test_legacy_invocations_receive_independent_budget_scopes() -> None:
    first = ProviderInvocationContext.from_value(None)
    second = ProviderInvocationContext.from_value(None)

    assert first.run_id.startswith("legacy-unbound:")
    assert second.run_id.startswith("legacy-unbound:")
    assert first.run_id != second.run_id
    assert first.for_attempt(1, retry_id="retry-1").run_id == first.run_id


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


def test_ollama_docker_service_is_local_but_untrusted_hosts_are_external() -> None:
    subject = ProviderInvocationMiddleware()
    request = {
        "context": context(external_egress_allowed=False, max_attempts=3),
        "provider": "ollama",
        "model": "phi4-mini",
        "payload": {"messages": [{"role": "user", "content": "hello"}]},
    }

    prepared = subject.prepare(
        **request,
        endpoint_url="http://ollama:11434/v1/chat/completions",
    )

    assert prepared.context.provider_call_id
    for endpoint in (
        "https://ollama.example/v1/chat/completions",
        "http://arbitrary-single-label:11434/v1/chat/completions",
    ):
        with pytest.raises(
            ProviderInvocationBlocked,
            match="provider_egress_denied",
        ):
            subject.prepare(**request, endpoint_url=endpoint)


@pytest.mark.parametrize(
    ("endpoint_url", "endpoint_identity"),
    (
        (
            "http://ollama:11434/v1",
            "http://ollama:11434/v1/chat/completions",
        ),
        (
            "http://192.168.50.25:11434/v1",
            "http://192.168.50.25:11434/v1/chat/completions",
        ),
        (
            "http://[fd12:3456::1]:11434/v1",
            "http://[fd12:3456::1]:11434/v1/chat/completions",
        ),
    ),
    ids=("docker-service", "signed-rfc1918-lan", "signed-ipv6-ula"),
)
def test_signed_local_provider_endpoints_are_allowed_without_external_egress(
    endpoint_url: str,
    endpoint_identity: str,
) -> None:
    subject = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )

    prepared = subject.prepare(
        context=hub_bound_context(
            provider="ollama",
            model="phi4-mini",
            endpoint_identity=endpoint_identity,
        ),
        provider="ollama",
        model="phi4-mini",
        endpoint_url=endpoint_url,
        payload={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert prepared.uses_hub_budget is True
    assert (
        prepared.context.provider_endpoint_identity
        == endpoint_identity
    )


def test_unsigned_hub_bound_lan_endpoint_requires_narrow_legacy_default() -> None:
    subject = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_binding_required",
    ):
        subject.prepare(
            context=hub_bound_context(
                provider="ollama",
                model="phi4-mini",
            ),
            provider="ollama",
            model="phi4-mini",
            endpoint_url="http://192.168.50.25:11434/v1",
            payload={"messages": []},
        )


@pytest.mark.parametrize(
    "endpoint_url",
    (
        "http://169.254.169.254/v1",
        "http://[::]/v1",
        "http://224.0.0.1/v1",
        "http://192.0.2.1/v1",
        "http://[fe80::1]/v1",
        "http://[2001:db8::1]/v1",
        "http://[100::]/v1",
    ),
    ids=(
        "metadata",
        "unspecified",
        "multicast",
        "reserved-v4",
        "link-local-v6",
        "documentation-v6",
        "reserved-v6",
    ),
)
def test_forbidden_literal_targets_are_denied_even_when_signed_and_egress_allowed(
    endpoint_url: str,
) -> None:
    from ananta_contracts.provider_endpoint_policy import (
        normalize_provider_endpoint_identity,
    )

    endpoint_identity = normalize_provider_endpoint_identity(
        provider_id="ollama",
        endpoint_url=endpoint_url,
    )
    subject = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_target_denied",
    ):
        subject.prepare(
            context=hub_bound_context(
                provider="ollama",
                model="phi4-mini",
                endpoint_identity=endpoint_identity,
                external_egress_allowed=True,
            ),
            provider="ollama",
            model="phi4-mini",
            endpoint_url=endpoint_url,
            payload={"messages": []},
        )


@pytest.mark.parametrize(
    "host",
    (
        "2852039166",
        "2130706433",
        "0xA9FEA9FE",
        "0251.0376.0251.0376",
        "127.1",
    ),
    ids=("metadata-decimal", "loopback-decimal", "hex", "octal", "short-dot"),
)
def test_alternative_numeric_ip_hosts_are_rejected(host: str) -> None:
    with pytest.raises(
        ValueError,
        match="provider_endpoint_identity_invalid",
    ):
        normalize_provider_endpoint_identity(
            provider_id="ollama",
            endpoint_url=f"http://{host}:11434/v1",
        )


def test_dns_resolution_rejects_mixed_and_changed_private_answers() -> None:
    public = (
        2,
        1,
        6,
        "",
        ("93.184.216.34", 443),
    )
    private = (
        2,
        1,
        6,
        "",
        ("10.0.0.5", 443),
    )

    with pytest.raises(
        ValueError,
        match="provider_endpoint_resolution_denied",
    ):
        validate_provider_endpoint_resolution(
            provider_id="openai",
            endpoint_url="https://provider.example/v1",
            resolver=lambda *args, **kwargs: [public, private],
        )

    answers = iter(([public], [private]))

    def changing_resolver(*args, **kwargs):
        return next(answers)

    assert validate_provider_endpoint_resolution(
        provider_id="openai",
        endpoint_url="https://provider.example/v1",
        resolver=changing_resolver,
    ) == ("93.184.216.34",)
    with pytest.raises(
        ValueError,
        match="provider_endpoint_resolution_denied",
    ):
        validate_provider_endpoint_resolution(
            provider_id="openai",
            endpoint_url="https://provider.example/v1",
            resolver=changing_resolver,
        )


def test_cloud_provider_label_cannot_authorize_loopback_dns_alias() -> None:
    subject = ProviderInvocationMiddleware()

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_resolution_denied",
    ):
        subject.prepare(
            context=context(external_egress_allowed=True),
            provider="openai",
            model="model-a",
            endpoint_url="http://localhost:9999/v1",
            payload={"messages": []},
        )


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://127.0.0.1:9999/v1/chat/completions",
        "http://10.0.0.5:9999/v1/chat/completions",
        "http://100.64.0.1:9999/v1/chat/completions",
        "http://[fc00::1]:9999/v1/chat/completions",
    ),
    ids=(
        "loopback-literal",
        "rfc1918-literal",
        "cgnat-literal",
        "ipv6-ula-literal",
    ),
)
def test_cloud_provider_label_cannot_authorize_private_literal(
    endpoint: str,
) -> None:
    subject = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_non_global_literal_denied",
    ):
        subject.prepare(
            context=hub_bound_context(
                provider="openai",
                model="model-a",
                endpoint_identity=endpoint,
                external_egress_allowed=True,
            ),
            provider="openai",
            model="model-a",
            endpoint_url=endpoint,
            payload={"messages": []},
        )


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://127.0.0.1:9999/v1/chat/completions",
        "http://10.0.0.5:9999/v1/chat/completions",
    ),
    ids=("loopback-literal", "rfc1918-literal"),
)
def test_signed_openai_compatible_private_literals_remain_local(
    endpoint: str,
) -> None:
    prepared = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    ).prepare(
        context=hub_bound_context(
            provider="openai_compatible",
            model="model-a",
            endpoint_identity=endpoint,
        ),
        provider="openai_compatible",
        model="model-a",
        endpoint_url=endpoint,
        payload={"messages": []},
    )

    assert prepared.uses_hub_budget is True


@pytest.mark.parametrize(
    ("provider", "endpoint", "expected"),
    (
        (
            "ollama",
            "http://ollama:11434/v1",
            "http://ollama:11434/v1/chat/completions",
        ),
        (
            "ollama",
            "http://ollama:11434/api/generate",
            "http://ollama:11434/api/generate",
        ),
    ),
    ids=("openai-compatible-v1", "ollama-native-generate"),
)
def test_provider_request_url_builder_has_one_exact_standard_target(
    provider: str,
    endpoint: str,
    expected: str,
) -> None:
    assert build_provider_request_url(
        provider_id=provider,
        endpoint_url=endpoint,
    ) == expected


def test_provider_request_url_builder_denies_ambiguous_custom_path() -> None:
    with pytest.raises(
        ValueError,
        match="provider_endpoint_path_unsupported",
    ):
        build_provider_request_url(
            provider_id="ollama",
            endpoint_url="http://ollama:11434/custom/chat",
        )


@pytest.mark.parametrize(
    "endpoint_url",
    (
        "http://ollama:11435/v1",
        "http://ollama:11434/api/generate",
    ),
    ids=("port", "path"),
)
def test_signed_endpoint_port_and_path_cannot_be_changed(
    endpoint_url: str,
) -> None:
    subject = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_binding_mismatch",
    ):
        subject.prepare(
            context=hub_bound_context(
                provider="ollama",
                model="phi4-mini",
                endpoint_identity=(
                    "http://ollama:11434/v1/chat/completions"
                ),
            ),
            provider="ollama",
            model="phi4-mini",
            endpoint_url=endpoint_url,
            payload={"messages": []},
        )


def test_signed_public_endpoint_still_requires_external_egress_grant(
    monkeypatch,
) -> None:
    endpoint = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(
        "ananta_contracts.provider_endpoint_policy.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ],
    )
    blocked = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    )
    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_egress_denied",
    ):
        blocked.prepare(
            context=hub_bound_context(
                provider="openai",
                model="gpt-test",
                endpoint_identity=endpoint,
            ),
            provider="openai",
            model="gpt-test",
            endpoint_url=endpoint,
            payload={"messages": []},
        )

    allowed = ProviderInvocationMiddleware(
        hub_budgets=AtomicProviderBudgetLedger()
    ).prepare(
        context=hub_bound_context(
            provider="openai",
            model="gpt-test",
            endpoint_identity=endpoint,
            external_egress_allowed=True,
        ),
        provider="openai",
        model="gpt-test",
        endpoint_url=endpoint,
        payload={"messages": []},
    )
    assert allowed.uses_hub_budget is True


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


def test_retry_attempt_changes_provider_budget_reservation_id() -> None:
    subject = ProviderInvocationMiddleware()
    first_context = context(max_attempts=2).for_attempt(
        0,
        retry_id="request:provider:0",
    )
    second_context = context(max_attempts=2).for_attempt(
        1,
        retry_id="request:provider:1",
    )
    request = {
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": [{"role": "user", "content": "same"}]},
    }

    first = subject.prepare(context=first_context, **request)
    second = subject.prepare(context=second_context, **request)

    assert first.budget_reservation_id != second.budget_reservation_id


def test_logical_provider_calls_reserve_twice_but_exact_replay_is_idempotent() -> None:
    ledger = AtomicProviderBudgetLedger()
    subject = ProviderInvocationMiddleware(budgets=ledger)
    bound = context(max_attempts=2)
    request = {
        "provider": "local",
        "model": "model-a",
        "endpoint_url": "http://localhost/v1",
        "payload": {"messages": [{"role": "user", "content": "same"}]},
    }

    first = subject.prepare(context=bound, **request)
    second = subject.prepare(context=bound, **request)
    replay = subject.prepare(context=first.context, **request)

    assert first.context.provider_call_id != second.context.provider_call_id
    assert first.budget_reservation_id != second.budget_reservation_id
    assert replay.budget_reservation_id == first.budget_reservation_id
    assert ledger.snapshot(first.context)["attempts"] == 2


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
        provider_endpoint_identity=(
            "http://localhost/v1/chat/completions"
        ),
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

    def post(url, *, json, headers, timeout, allow_redirects):
        sent.update(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
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
    assert sent["allow_redirects"] is False
    assert result["metadata"]["provider_middleware"]["cache_hit"] is False


@pytest.mark.parametrize(
    ("status_code", "response_url"),
    (
        (302, ""),
        (200, "http://169.254.169.254/latest/meta-data"),
    ),
    ids=("redirect-status", "final-url-mismatch"),
)
def test_model_invocation_denies_redirect_without_following_target(
    monkeypatch,
    status_code: int,
    response_url: str,
) -> None:
    subject = ProviderInvocationMiddleware()
    monkeypatch.setattr(
        ModelInvocationService,
        "_provider_middleware",
        subject,
    )
    observed: dict[str, object] = {}

    response = type(
        "Response",
        (),
        {
            "status_code": status_code,
            "text": "",
            "url": response_url,
            "headers": {
                "Location": (
                    "http://169.254.169.254/latest/meta-data"
                )
            },
        },
    )()

    def post(url, *, json, headers, timeout, allow_redirects):
        observed.update(
            {
                "url": url,
                "allow_redirects": allow_redirects,
            }
        )
        return response

    monkeypatch.setattr(
        "agent.services.model_invocation_service.requests.post",
        post,
    )

    with pytest.raises(
        LLMUnavailableError,
        match="provider_redirect_denied",
    ):
        ModelInvocationService._make_single_chat_call(
            [{"role": "user", "content": "hello"}],
            tools=None,
            response_format=None,
            attempt={
                "provider": "local",
                "url": (
                    "http://localhost:11434/v1/chat/completions"
                ),
                "model": "model-a",
                "timeout": 3,
                "profile": None,
            },
            resolution_info={},
            provider_context=context(),
        )

    assert observed == {
        "url": "http://localhost:11434/v1/chat/completions",
        "allow_redirects": False,
    }


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
        provider="local",
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
