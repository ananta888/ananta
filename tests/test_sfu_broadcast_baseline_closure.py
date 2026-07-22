from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_activation_boundary import (
    ActivationRequest,
    FeaturePolicySnapshot,
    LimitsSnapshot,
    ParentReadinessSnapshot,
    RuntimeCapabilitySnapshot,
    SfuBroadcastActivationBoundary,
)
from agent.services.sfu_broadcast_baseline_limits import (
    BaselineLimitCatalog,
    BaselineLimitError,
    REQUIRED_BUDGET_IDS,
    REQUIRED_PROFILE_IDS,
    load_baseline_limit_catalog,
    qualify_baseline_run,
)
from agent.services.sfu_broadcast_rollback_service import (
    HubSfuBroadcastRollbackService,
    RollbackCommand,
    RollbackStepResult,
)
from agent.services.sfu_broadcast_source_grounding import SourceGroundingRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_limit_catalog_covers_every_activation_profile_and_budget() -> None:
    catalog = load_baseline_limit_catalog(
        ROOT / "config/sfu_broadcast_baseline_limits.v1.json"
    )

    assert REQUIRED_PROFILE_IDS.issubset(catalog.run_profiles)
    assert REQUIRED_BUDGET_IDS.issubset(catalog.budgets)
    assert catalog.activation_default == "no_go"
    assert catalog.hard_limits["admitted_sfu_nodes"].maximum == 0


@pytest.mark.parametrize("unsafe_value", [-1, float("nan"), float("inf")])
def test_limit_catalog_rejects_unsafe_numeric_values(unsafe_value: float) -> None:
    raw = {
        "schema_version": "1",
        "policy_version": 1,
        "activation_default": "no_go",
        "hard_limits": {
            "cap": {
                "unit": "count",
                "minimum": 0,
                "maximum": unsafe_value,
                "missing_metric_behavior": "block",
            }
        },
        "budget_definitions": {},
        "budget_sets": {},
        "run_profiles": [],
    }

    with pytest.raises(BaselineLimitError):
        BaselineLimitCatalog.from_mapping(raw)


def test_incomplete_run_remains_no_go_without_grounded_evidence() -> None:
    catalog = load_baseline_limit_catalog(
        ROOT / "config/sfu_broadcast_baseline_limits.v1.json"
    )

    result = qualify_baseline_run(
        catalog,
        {"profile_id": "sfu_direct_network", "metrics": {}},
        grounding_verified=False,
    )

    assert result.status == "no_go"
    assert "evidence_not_grounded" in result.reason_codes
    assert "required_metric_missing" in result.reason_codes


def test_activation_preserves_parent_no_go_and_disables_every_feature() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    grounding = SourceGroundingRegistry(()).verify(source_ids=(), run_ids=())

    result = SfuBroadcastActivationBoundary().evaluate(
        now=now,
        request=ActivationRequest(0, 1, 1, {"semantic_media_broadcast": True}),
        parent=ParentReadinessSnapshot(
            "no_go", "observe_only", 0, None, None, None
        ),
        runtime=RuntimeCapabilitySnapshot(
            "livekit_control_api",
            "unsupported",
            "livekit_native",
            "missing",
            None,
            None,
            {},
        ),
        feature_policy=FeaturePolicySnapshot(
            1, True, False, {"semantic_media_broadcast": False}
        ),
        limits=LimitsSnapshot("blocked", 1, 0),
        grounding=grounding,
    )

    assert result.status == "no_go"
    assert result.effective_mode == "unsupported"
    assert result.participant_cap == 0
    assert not any(result.effective_features.values())


class _SuccessfulRollbackPort:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _record(self, name: str) -> RollbackStepResult:
        self.calls.append(name)
        return RollbackStepResult(True, "ok")

    def enable_security_fence(self, command: RollbackCommand) -> RollbackStepResult:
        return self._record("enable_security_fence")

    def stop_new_admission(self, command: RollbackCommand) -> RollbackStepResult:
        return self._record("stop_new_admission")

    def disable_optional_features(
        self, command: RollbackCommand
    ) -> RollbackStepResult:
        return self._record("disable_optional_features")

    def project_parent_fallback(
        self, command: RollbackCommand
    ) -> RollbackStepResult:
        return self._record("project_parent_fallback")

    def request_graceful_drain(
        self, command: RollbackCommand
    ) -> RollbackStepResult:
        return self._record("request_graceful_drain")

    def verify_quiesced(self, command: RollbackCommand) -> RollbackStepResult:
        return self._record("verify_quiesced")


def test_hub_rollback_is_finite_ordered_and_idempotency_keyed() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    port = _SuccessfulRollbackPort()
    command = RollbackCommand(
        operation_id="rollback-test-operation",
        fencing_token=1,
        actor="test-operator",
        reason="test rollback",
        expected_policy_version=1,
        deadline=now + timedelta(minutes=1),
    )

    result = HubSfuBroadcastRollbackService(port, clock=lambda: now).execute(command)

    assert result.status == "completed"
    assert tuple(port.calls) == result.completed_steps
