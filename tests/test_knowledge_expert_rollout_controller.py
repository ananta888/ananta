from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.knowledge_expert_rollout_controller import (
    KnowledgeExpertRolloutAdmission,
    KnowledgeExpertRolloutController,
    KnowledgeExpertRolloutObservation,
    KnowledgeExpertRolloutPolicy,
)


class GenerationSwitch:
    def __init__(self) -> None:
        self.current = "generation-old"
        self.calls: list[tuple[str, str, str]] = []

    def switch(self, *, bank_id, expected_generation_id, target_generation_id):
        self.calls.append((bank_id, expected_generation_id, target_generation_id))
        if self.current == target_generation_id:
            return True
        if self.current != expected_generation_id:
            return False
        self.current = target_generation_id
        return True


def _admission(**overrides):
    values = {
        "bank_id": "bank-1",
        "candidate_generation_id": "generation-new",
        "last_good_generation_id": "generation-old",
        "research_reproduction_passed": True,
        "runtime_capability_passed": True,
        "security_passed": True,
        "benchmark_passed": True,
        "operations_passed": True,
    }
    values.update(overrides)
    return KnowledgeExpertRolloutAdmission(**values)


def _observation(identifier: str, stage: str):
    return KnowledgeExpertRolloutObservation(
        observation_id=identifier,
        stage=stage,
        success=True,
        quality_delta=0.1,
        latency_ms=10,
    )


def _controller(tmp_path, switch):
    return KnowledgeExpertRolloutController(
        tmp_path / "rollout.sqlite3",
        generation_switch=switch,
        policy=KnowledgeExpertRolloutPolicy(
            minimum_shadow_observations=2,
            minimum_canary_observations=2,
            canary_basis_points=1000,
        ),
    )


def test_controller_promotes_headlessly_and_assigns_canary_deterministically(tmp_path):
    switch = GenerationSwitch()
    controller = _controller(tmp_path, switch)
    assert controller.admit(scope_id="tenant:repo", admission=_admission())["stage"] == "shadow"
    controller.observe(scope_id="tenant:repo", observation=_observation("shadow-1", "shadow"))
    state = controller.observe(scope_id="tenant:repo", observation=_observation("shadow-2", "shadow"))

    assert state["stage"] == "canary"
    assert switch.current == "generation-new"
    first = controller.assignment(scope_id="tenant:repo", request_id="request-1")
    assert first == controller.assignment(scope_id="tenant:repo", request_id="request-1")

    controller.observe(scope_id="tenant:repo", observation=_observation("canary-1", "canary"))
    state = controller.observe(scope_id="tenant:repo", observation=_observation("canary-2", "canary"))
    assert state["stage"] == "ga"
    assert controller.assignment(scope_id="tenant:repo", request_id="request-2")["result_affecting"] is True


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"conflict_detected": True}, "knowledge_expert_conflict_detected"),
        ({"hallucination_detected": True}, "knowledge_expert_hallucination_detected"),
        ({"oom_detected": True}, "knowledge_expert_oom_detected"),
        ({"cache_error": True}, "knowledge_expert_cache_error"),
        ({"security_event": True}, "knowledge_expert_security_event"),
        ({"scope_violation": True}, "knowledge_expert_scope_violation"),
    ],
)
def test_controller_rolls_back_canary_automatically_and_persists(tmp_path, change, reason):
    switch = GenerationSwitch()
    path = tmp_path / "rollout.sqlite3"
    controller = _controller(tmp_path, switch)
    controller.admit(scope_id="tenant:repo", admission=_admission())
    controller.observe(scope_id="tenant:repo", observation=_observation("shadow-1", "shadow"))
    controller.observe(scope_id="tenant:repo", observation=_observation("shadow-2", "shadow"))

    state = controller.observe(
        scope_id="tenant:repo",
        observation=replace(_observation(reason, "canary"), **change),
    )

    assert state["stage"] == "off"
    assert state["reason_code"] == reason
    assert switch.current == "generation-old"
    assert path.exists()
    restored = _controller(tmp_path, switch)
    assert restored.snapshot(scope_id="tenant:repo")["reason_code"] == reason


def test_controller_rejects_unproven_admission_without_waiting_for_a_person(tmp_path):
    controller = _controller(tmp_path, GenerationSwitch())

    state = controller.admit(
        scope_id="tenant:repo",
        admission=_admission(research_reproduction_passed=False),
    )

    assert state["stage"] == "off"
    assert state["reason_code"] == "knowledge_expert_rollout_admission_failed"
