from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.research_training_rollout_service import (
    ResearchTrainingRolloutPolicy,
    ResearchTrainingRolloutService,
)

ROOT = Path(__file__).resolve().parents[2]


def policy(**overrides: object) -> ResearchTrainingRolloutPolicy:
    raw = json.loads((ROOT / "config/research-training/rollout.v1.json").read_text(encoding="utf-8"))
    raw.update(overrides)
    return ResearchTrainingRolloutPolicy.from_mapping(raw)


def test_default_rollout_is_disabled_and_kill_switched_without_human_wait() -> None:
    decision = ResearchTrainingRolloutService(policy()).evaluate({})

    assert decision["advanced"] is False
    assert decision["reason_code"] == "research_rollout_disabled"
    assert decision["production_routes_enabled"] is False
    assert decision["human_intervention_required"] is False


def test_hub_policy_automatically_advances_only_after_every_phase_gate() -> None:
    rollout = ResearchTrainingRolloutService(
        policy(enabled=True, kill_switch=False, automatic_progression_enabled=True)
    )

    blocked = rollout.evaluate({"schema_contracts": True, "dry_run": True})
    assert blocked["advanced"] is False
    assert blocked["missing_gates"] == ["boundary_security"]

    advanced = rollout.evaluate({"schema_contracts": True, "dry_run": True, "boundary_security": True})
    assert advanced["advanced"] is True
    assert advanced["target_phase"] == "phase_1"
    assert advanced["human_intervention_required"] is False


def test_rollout_kill_switch_and_rollback_do_not_change_adapter_training() -> None:
    rollout = ResearchTrainingRolloutService(policy(enabled=True, kill_switch=True, automatic_progression_enabled=True))
    decision = rollout.evaluate({"schema_contracts": True, "dry_run": True, "boundary_security": True})
    rollback = rollout.rollback(reason_code="research_quality_regression")

    assert decision["reason_code"] == "research_rollout_kill_switch_active"
    assert rollback["research_training_enabled"] is False
    assert rollback["adapter_training_changed"] is False
    assert rollback["production_routes_changed"] is False
    assert rollback["human_intervention_required"] is False


def test_rollout_rejects_production_routes_and_automatic_upstream_sync() -> None:
    raw = json.loads((ROOT / "config/research-training/rollout.v1.json").read_text(encoding="utf-8"))
    raw["production_routes_enabled"] = True
    with pytest.raises(ValueError, match="production_routes_forbidden"):
        ResearchTrainingRolloutPolicy.from_mapping(raw)

    raw["production_routes_enabled"] = False
    raw["upstream_watch"]["automatic_code_sync"] = True
    with pytest.raises(ValueError, match="upstream_watch_invalid"):
        ResearchTrainingRolloutPolicy.from_mapping(raw)
