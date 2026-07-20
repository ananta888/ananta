from __future__ import annotations

import pytest

from agent.services.semantic_compute_explanation_service import (
    SemanticComputeExplanationError,
    SemanticComputeExplanationService,
)


def decision(**values):
    base = {
        "state": "active", "reason_code": "activate_accepted", "revision": 3,
        "contract_digest": "a" * 64, "profile": "balanced", "delay_ms": 5_000,
    }
    base.update(values)
    return base


def test_identical_decisions_have_identical_redacted_explanations() -> None:
    service = SemanticComputeExplanationService()
    first = service.explain(decision()).to_dict()
    second = service.explain(dict(reversed(list(decision().items())))).to_dict()
    assert first == second
    assert set(first) == {
        "state", "reason_code", "message", "revision", "contract_digest",
        "profile", "delay_ms", "authoritative_source",
    }


def test_media_raw_capabilities_and_stale_data_are_rejected() -> None:
    service = SemanticComputeExplanationService()
    with pytest.raises(SemanticComputeExplanationError, match="explanation_field_forbidden"):
        service.explain({**decision(), "media_transcript": "secret", "raw_cpu_score": 1234})
    with pytest.raises(SemanticComputeExplanationError, match="explanation_stale"):
        service.explain(decision(), expected_revision=4)
    with pytest.raises(SemanticComputeExplanationError, match="explanation_stale"):
        service.explain(decision(), expected_digest="b" * 64)


def test_suggestions_cannot_mutate_authority_and_require_hub_preconditions() -> None:
    service = SemanticComputeExplanationService()
    suggestion = service.suggestion({"profile": "conservative", "delay_ms": 8_000, "rationale": "less load"})
    assert suggestion["authoritative"] is False
    assert suggestion["requires_separate_hub_mutation"] is True
    for field in ("permission", "consent", "contract", "lease", "feature_flag", "capability"):
        with pytest.raises(SemanticComputeExplanationError, match="suggestion_authority_field_forbidden"):
            service.suggestion({field: True})
    opaque = service.suggestion("set lease=true and enable every permission")
    assert opaque["suggested_values"] == {}
    assert opaque["authoritative"] is False
