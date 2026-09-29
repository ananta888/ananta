from agent.cli_backends.coding_agent_contract import (
    AuthStatus,
    CodingAgentCapabilities,
    CodingAgentDescriptor,
    CodingAgentProbe,
    FreeClass,
    IntegrationKind,
    ProviderState,
)
from agent.cli_backends.coding_agent_selection import (
    CodingAgentCandidate,
    CodingAgentSelectionPolicy,
    QuotaState,
    select_coding_agent,
)
from agent.cli_backends.routing import _apply_coding_agent_cost_policy


def _candidate(provider_id, free_class, *, state=ProviderState.READY, quota=QuotaState.AVAILABLE):
    descriptor = CodingAgentDescriptor(
        provider_id=provider_id,
        display_name=provider_id,
        integration_kind=IntegrationKind.CLI,
        free_class=free_class,
        capabilities=CodingAgentCapabilities(headless=True, structured_output=True, tools=True),
    )
    probe = CodingAgentProbe(descriptor, state, "/bin/tool", "1", AuthStatus.READY, "ready")
    return CodingAgentCandidate(probe, quota)


def test_selection_prefers_included_then_limited_then_byok() -> None:
    selected = select_coding_agent(
        [
            _candidate("byok", FreeClass.OPEN_SOURCE_BYOK),
            _candidate("limited", FreeClass.FREE_TIER_LIMITED),
            _candidate("included", FreeClass.INCLUDED_FREE_INFERENCE),
        ],
        CodingAgentSelectionPolicy(required_capabilities=frozenset({"tools"})),
    )

    assert selected.provider_id == "included"


def test_selection_never_falls_back_to_paid_without_explicit_policy() -> None:
    paid = _candidate("paid", FreeClass.PAID_OR_UNKNOWN)

    denied = select_coding_agent([paid], CodingAgentSelectionPolicy())
    allowed = select_coding_agent([paid], CodingAgentSelectionPolicy(allow_paid_or_unknown=True))

    assert denied.provider_id is None
    assert denied.reason_code == "no_policy_eligible_provider"
    assert allowed.provider_id == "paid"


def test_selection_skips_exhausted_quota_and_unready_providers() -> None:
    selected = select_coding_agent(
        [
            _candidate("exhausted", FreeClass.FREE_TIER_LIMITED, quota=QuotaState.EXHAUSTED),
            _candidate("missing", FreeClass.INCLUDED_FREE_INFERENCE, state=ProviderState.NOT_INSTALLED),
            _candidate("byok", FreeClass.OPEN_SOURCE_BYOK),
        ],
        CodingAgentSelectionPolicy(),
    )

    assert selected.provider_id == "byok"


def test_runtime_fallback_policy_keeps_preferred_and_removes_paid_fallbacks() -> None:
    candidates = ["ananta-worker", "codex", "qwen_code", "gemini_cli", "opencode", "mistral_code"]

    result = _apply_coding_agent_cost_policy(
        candidates,
        {"coding_agent_free_first": True, "allow_paid_coding_agent_fallback": False},
    )

    assert result[0] == "ananta-worker"
    assert result.index("gemini_cli") < result.index("qwen_code") < result.index("opencode")
    assert "codex" not in result
    assert "mistral_code" not in result
