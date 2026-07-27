from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ananta_contracts.provider_invocation import (
    ProviderBudgetDecision,
    ProviderInvocationBlocked,
)
from ananta_contracts.workflow_worker_gateway import PROVIDER_BUDGET_RECEIPT_SCHEMA
from worker.runtime.provider_text_generation import (
    HubBudgetedWorkerTextGeneration,
    WorkerProviderHttpTransport,
    build_hub_budgeted_worker_text_generation,
)


def _context(**overrides: Any) -> dict[str, Any]:
    value = {
        "tenant_id": "tenant-1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "step_id": "step-1",
        "plan_hash": "plan-hash-1",
        "policy_version": "policy-v1",
        "prompt_version": "prompt-v1",
        "correlation_id": "correlation-1",
        "external_egress_allowed": False,
        "max_attempts": 2,
        "max_total_tokens": 1_000,
        "max_cost_micros": 10_000,
        "max_completion_tokens_per_call": 64,
        "require_hub_provider_budget": True,
        "provider_transport_mode": "hub_bound",
        "provider_decision_reason": "hub_provider_policy_selected",
        "provider_binding_id": "binding-1",
        "selected_provider_id": "lmstudio",
        "selected_model_id": "model-1",
        "authorization_envelope": {"envelope_id": "envelope-1"},
        "attempt_id": "attempt-1",
        "fencing_token": 7,
    }
    value.update(overrides)
    return value


class _Budget:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reservations: list[dict[str, Any]] = []
        self.reconciliations: list[dict[str, Any]] = []

    def reserve(self, **values: Any) -> ProviderBudgetDecision:
        self.reservations.append(values)
        return ProviderBudgetDecision(
            self.allowed,
            "provider_budget_reserved" if self.allowed else "provider_token_budget_exceeded",
            1,
            80,
            0,
        )

    def reconcile(self, **values: Any) -> None:
        self.reconciliations.append(values)


class _Transport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post_json(self, **values: Any) -> dict[str, Any]:
        self.calls.append(values)
        return {
            "choices": [{"message": {"content": "worker response"}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }


def test_missing_worker_provider_configuration_blocks_before_budget_or_network() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={},
        budgets=budgets,
        transport=transport,
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="worker_provider_endpoint_not_configured",
    ):
        provider.generate_text(
            prompt="hello",
            provider="lmstudio",
            model="model-1",
            provider_context=_context(),
        )

    assert budgets.reservations == []
    assert transport.calls == []


def test_hub_budget_denial_blocks_before_provider_invocation() -> None:
    budgets = _Budget(allowed=False)
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"lmstudio": "http://host.docker.internal:1234/v1"},
        budgets=budgets,
        transport=transport,
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_token_budget_exceeded",
    ):
        provider.generate_text(
            prompt="hello",
            provider="lmstudio",
            model="model-1",
            provider_context=_context(),
        )

    assert len(budgets.reservations) == 1
    assert transport.calls == []


def test_external_endpoint_is_denied_by_hub_bound_egress_policy() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"openai": "https://api.openai.com/v1/chat/completions"},
        budgets=budgets,
        transport=transport,
        environment={"OPENAI_API_KEY": "secret"},
    )

    with pytest.raises(ProviderInvocationBlocked, match="provider_egress_denied"):
        provider.generate_text(
            prompt="hello",
            provider="openai",
            model="gpt-test",
            provider_context=_context(
                selected_provider_id="openai",
                selected_model_id="gpt-test",
                provider_endpoint_identity=(
                    "https://api.openai.com/v1/chat/completions"
                ),
            ),
        )

    assert budgets.reservations == []
    assert transport.calls == []


def test_configured_ollama_docker_service_is_allowed_without_external_egress() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"ollama": "http://ollama:11434"},
        budgets=budgets,
        transport=transport,
    )

    result = provider.generate_text(
        prompt="hello",
        provider="ollama",
        model="phi4-mini",
        provider_context=_context(
            selected_provider_id="ollama",
            selected_model_id="phi4-mini",
        ),
    )

    assert result["text"] == "worker response"
    assert transport.calls[0]["url"] == (
        "http://ollama:11434/v1/chat/completions"
    )
    assert budgets.reservations[0]["context"].provider_call_id


def test_worker_uses_exact_ollama_native_generate_target() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={
            "ollama": "http://ollama:11434/api/generate"
        },
        budgets=budgets,
        transport=transport,
    )

    provider.generate_text(
        prompt="hello",
        provider="ollama",
        model="phi4-mini",
        provider_context=_context(
            selected_provider_id="ollama",
            selected_model_id="phi4-mini",
            provider_endpoint_identity=(
                "http://ollama:11434/api/generate"
            ),
        ),
    )

    assert transport.calls[0]["url"] == (
        "http://ollama:11434/api/generate"
    )
    assert transport.calls[0]["payload"] == {
        "model": "phi4-mini",
        "prompt": "hello",
        "stream": False,
        "options": {"num_predict": 64},
    }


def test_worker_denies_ambiguous_custom_provider_path_before_budget() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={
            "ollama": "http://ollama:11434/custom/chat"
        },
        budgets=budgets,
        transport=transport,
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_identity_invalid",
    ):
        provider.generate_text(
            prompt="hello",
            provider="ollama",
            model="phi4-mini",
            provider_context=_context(
                selected_provider_id="ollama",
                selected_model_id="phi4-mini",
            ),
        )

    assert budgets.reservations == []
    assert transport.calls == []


def test_signed_worker_endpoint_allows_exact_lan_and_rejects_path_or_port_change() -> None:
    endpoint_identity = (
        "http://192.168.50.25:11434/v1/chat/completions"
    )
    budgets = _Budget()
    transport = _Transport()
    exact = HubBudgetedWorkerTextGeneration(
        provider_urls={"ollama": "http://192.168.50.25:11434/v1"},
        budgets=budgets,
        transport=transport,
    )
    result = exact.generate_text(
        prompt="hello",
        provider="ollama",
        model="phi4-mini",
        provider_context=_context(
            selected_provider_id="ollama",
            selected_model_id="phi4-mini",
            provider_endpoint_identity=endpoint_identity,
        ),
    )
    assert result["text"] == "worker response"

    for changed_endpoint in (
        "http://192.168.50.25:11435/v1",
        "http://192.168.50.25:11434/api/generate",
    ):
        changed_budgets = _Budget()
        changed_transport = _Transport()
        changed = HubBudgetedWorkerTextGeneration(
            provider_urls={"ollama": changed_endpoint},
            budgets=changed_budgets,
            transport=changed_transport,
        )
        with pytest.raises(
            ProviderInvocationBlocked,
            match="provider_endpoint_binding_mismatch",
        ):
            changed.generate_text(
                prompt="hello",
                provider="ollama",
                model="phi4-mini",
                provider_context=_context(
                    selected_provider_id="ollama",
                    selected_model_id="phi4-mini",
                    provider_endpoint_identity=endpoint_identity,
                ),
            )
        assert changed_budgets.reservations == []
        assert changed_transport.calls == []


def test_worker_metadata_endpoint_is_denied_even_when_signed() -> None:
    budgets = _Budget()
    transport = _Transport()
    endpoint = "http://169.254.169.254/v1/chat/completions"
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"ollama": endpoint},
        budgets=budgets,
        transport=transport,
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_target_denied",
    ):
        provider.generate_text(
            prompt="hello",
            provider="ollama",
            model="phi4-mini",
            provider_context=_context(
                selected_provider_id="ollama",
                selected_model_id="phi4-mini",
                provider_endpoint_identity=endpoint,
                external_egress_allowed=True,
            ),
        )

    assert budgets.reservations == []
    assert transport.calls == []


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://127.0.0.1:9999/v1/chat/completions",
        "http://10.0.0.5:9999/v1/chat/completions",
        "http://100.64.0.1:9999/v1/chat/completions",
        "http://[fc00::1]:9999/v1/chat/completions",
    ),
    ids=("loopback", "rfc1918", "cgnat", "ipv6-ula"),
)
def test_worker_cloud_provider_cannot_use_private_literal(
    endpoint: str,
) -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"openai": endpoint},
        budgets=budgets,
        transport=transport,
        environment={"OPENAI_API_KEY": "secret"},
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="provider_endpoint_non_global_literal_denied",
    ):
        provider.generate_text(
            prompt="hello",
            provider="openai",
            model="model-a",
            provider_context=_context(
                selected_provider_id="openai",
                selected_model_id="model-a",
                provider_endpoint_identity=endpoint,
                external_egress_allowed=True,
            ),
        )

    assert budgets.reservations == []
    assert transport.calls == []


@pytest.mark.parametrize(
    "redirect_target",
    (
        "http://169.254.169.254/latest/meta-data",
        "https://public.example/v1/chat/completions",
    ),
    ids=("metadata", "public"),
)
def test_worker_http_transport_never_follows_provider_redirect(
    monkeypatch,
    redirect_target: str,
) -> None:
    import urllib.error

    source_url = "http://ollama:11434/v1/chat/completions"

    class _Opener:
        @staticmethod
        def open(request, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": redirect_target},
                None,
            )

    def build_opener(handler):
        assert (
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {"Location": redirect_target},
                redirect_target,
            )
            is None
        )
        return _Opener()

    monkeypatch.setattr(
        "worker.runtime.provider_text_generation.urllib.request.build_opener",
        build_opener,
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="worker_provider_http_error",
    ):
        WorkerProviderHttpTransport().post_json(
            url=source_url,
            headers={"Content-Type": "application/json"},
            payload={"model": "phi4-mini"},
            timeout_seconds=5,
        )


def test_arbitrary_single_label_endpoint_is_not_treated_as_local() -> None:
    budgets = _Budget()
    transport = _Transport()
    provider = HubBudgetedWorkerTextGeneration(
        provider_urls={"ollama": "http://arbitrary-single-label:11434"},
        budgets=budgets,
        transport=transport,
    )

    with pytest.raises(ProviderInvocationBlocked, match="provider_egress_denied"):
        provider.generate_text(
            prompt="hello",
            provider="ollama",
            model="phi4-mini",
            provider_context=_context(
                selected_provider_id="ollama",
                selected_model_id="phi4-mini",
                provider_endpoint_identity=(
                    "http://arbitrary-single-label:11434"
                    "/v1/chat/completions"
                ),
            ),
        )

    assert budgets.reservations == []
    assert transport.calls == []


class _HubClient:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    def command(self, command: str, **values: Any) -> dict[str, Any]:
        self.commands.append((command, values))
        return {
            "schema": PROVIDER_BUDGET_RECEIPT_SCHEMA,
            "reservation_id": values["reservation_id"],
            "attempts": 1,
            "tokens": (
                int(values.get("actual_total_tokens") or 6)
                if command == "provider_budget_reconcile"
                else int(values["reserved_tokens"])
            ),
            "cost_micros": int(values.get("reserved_cost_micros") or 0),
            "reserved_tokens": int(values.get("reserved_tokens") or 80),
            "reserved_cost_micros": int(values.get("reserved_cost_micros") or 0),
            "maximum_attempts": 2,
            "maximum_tokens": 1_000,
            "maximum_cost_micros": 10_000,
            "reconciled": command == "provider_budget_reconcile",
            "reason_code": "provider_budget_reconciled",
        }


def test_production_builder_reserves_and_reconciles_budget_only_via_hub() -> None:
    client = _HubClient()
    transport = _Transport()
    provider = build_hub_budgeted_worker_text_generation(
        client=client,  # type: ignore[arg-type]
        provider_urls={"lmstudio": "http://host.docker.internal:1234/v1"},
        transport=transport,
    )

    result = provider.generate_text(
        prompt="hello",
        provider="lmstudio",
        model="model-1",
        provider_context=_context(),
    )

    assert result["text"] == "worker response"
    assert transport.calls[0]["url"] == (
        "http://host.docker.internal:1234/v1/chat/completions"
    )
    assert [command for command, _values in client.commands] == [
        "provider_budget_reserve",
        "provider_budget_reconcile",
    ]
    reserve_values = client.commands[0][1]
    assert reserve_values["binding"]["tenant_id"] == "tenant-1"
    assert reserve_values["binding"]["authorization_envelope"] == {
        "envelope_id": "envelope-1"
    }
    assert reserve_values["provider_binding_id"] == "binding-1"
    assert reserve_values["provider_id"] == "lmstudio"
    assert reserve_values["model_id"] == "model-1"
    assert reserve_values["provider_profile_id"] == ""


def test_profile_routing_fails_closed_without_injected_worker_port(
    monkeypatch,
) -> None:
    from agent.services.workflow_runtime.security import (
        RuntimeAuthorizationEnvelope,
    )
    from worker.runtime.provider_text_generation import (
        HubProfileRoutedWorkerTextGeneration,
    )

    monkeypatch.setattr(
        RuntimeAuthorizationEnvelope,
        "from_mapping",
        lambda _value: SimpleNamespace(provider_attempt_plan=()),
    )
    provider = HubProfileRoutedWorkerTextGeneration(
        direct=SimpleNamespace(
            generate_text=lambda **values: pytest.fail(
                "profile routing must not fall back to direct generation"
            )
        )
    )

    with pytest.raises(
        ProviderInvocationBlocked,
        match="worker_model_routing_unavailable",
    ):
        provider.generate_text(
            prompt="hello",
            provider="lmstudio",
            model="model-1",
            provider_context=_context(),
            provider_contexts_by_profile_id={
                "profile-1": {
                    **_context(),
                    "provider_profile_id": "profile-1",
                }
            },
        )
