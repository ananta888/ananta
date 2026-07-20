from __future__ import annotations

from agent.services.semantic_compute_negotiation import (
    NegotiationContext,
    SemanticComputeNegotiation,
)
from tests.semantic_compute_support import capability


def context(**overrides) -> NegotiationContext:
    values = {
        "session_id": "session-a",
        "room_id": "room-a",
        "epoch": 1,
        "policy_version": "policy-v1",
        "now_ms": 1_000_000,
        "started_at_ms": 999_900,
        "feature_enabled": True,
        "permission_granted": True,
        "consent_version": 1,
        "security_confirmed": True,
        "fallback_healthy": True,
    }
    values.update(overrides)
    return NegotiationContext(**values)


def proposal() -> dict:
    return {
        "profile": "balanced",
        "delay_ms": 5_000,
        "security_mode": "strict_e2ee",
        "trusted_compute_grant": False,
        "roles": {"primary": ["peer-a"]},
        "task_types": ["visual_extract"],
        "max_artifact_bytes": 1_048_576,
        "deadline_ms": 5_000,
        "expires_at_ms": 1_300_000,
    }


def test_identical_canonical_inputs_produce_identical_contract_and_reason() -> None:
    service = SemanticComputeNegotiation()
    first = service.decide(action="offer", context=context(), proposal=proposal())
    second = service.decide(action="offer", context=context(), proposal=dict(reversed(list(proposal().items()))))
    assert first == second
    assert first.contract is not None
    assert first.contract["contract_digest"] == second.contract["contract_digest"]


def test_bounds_timeout_and_activation_gates_fall_back_deterministically() -> None:
    service = SemanticComputeNegotiation()
    assert (
        service.decide(action="offer", context=context(), proposal=proposal(), round_number=5).reason_code
        == "round_limit_exceeded"
    )
    assert (
        service.decide(action="offer", context=context(now_ms=1_020_001), proposal=proposal()).reason_code
        == "negotiation_timeout"
    )
    assert (
        service.decide(action="offer", context=context(now_ms=999_899), proposal=proposal()).reason_code
        == "negotiation_timeout"
    )
    assert (
        service.decide(action="offer", context=context(consent_version=0), proposal=proposal()).reason_code
        == "consent_missing"
    )
    assert (
        service.decide(action="offer", context=context(security_confirmed=False), proposal=proposal()).reason_code
        == "security_unconfirmed"
    )


def test_activation_requires_current_capability_and_epoch() -> None:
    service = SemanticComputeNegotiation()
    offered = service.decide(action="offer", context=context(), proposal=proposal())
    assert offered.contract is not None
    missing = service.decide(action="activate", context=context(), proposal={}, prior_contract=offered.contract)
    assert missing.reason_code == "capability_missing"
    active = service.decide(
        action="activate",
        context=context(),
        proposal={},
        prior_contract=offered.contract,
        advertisements=[capability()],
    )
    assert active.state == "activate"
    assert active.contract and active.contract["revision"] == 2


def test_ai_proposal_is_explicitly_non_authoritative() -> None:
    result = SemanticComputeNegotiation().decide(
        action="propose", context=context(), proposal={"profile": "conservative", "delay_ms": 8_000}
    )
    assert result.authoritative is False
    assert result.reason_code == "proposal_requires_hub_mutation"
