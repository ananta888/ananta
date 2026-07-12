from __future__ import annotations

import time
from typing import Any, Mapping

from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    PathAiModeRule,
)
from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceRequest,
)
from agent.services.restricted_inference_port import ContractRestrictedInferencePort
from agent.services.voice_restricted_choice_service import VoiceRestrictedChoiceService


class _Transport:
    def __init__(self, scores: Mapping[str, float] | None = None, *, fail: bool = False) -> None:
        self.scores = dict(scores or {"candidate-a": 0.2, "candidate-b": 0.8})
        self.fail = fail
        self.request: RestrictedInferenceRequest | None = None

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.fail:
            raise RuntimeError("worker unavailable")
        request = RestrictedInferenceRequest.from_dict(envelope)
        self.request = request
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": request.request_id,
            "task_id": request.task_id,
            "operation": "score_choices",
            "status": "succeeded",
            "result": {
                "items": [{"choice": choice, "score": self.scores[choice]} for choice in request.payload["choices"]],
                "engine": "huggingface-transformers",
                "model_id": "fixture/choice-model",
                "manifest_digest": "d" * 64,
                "latency_ms": 1.0,
            },
            "error": None,
            "no_generation": True,
        }


def _base_result() -> dict[str, Any]:
    return {
        "text": "first transcript",
        "selected_candidate_id": "candidate-a",
        "warnings": [],
        "decision_trace": {"runtime": "voice"},
        "candidates": [
            {"candidate_id": "candidate-a", "text": "first transcript", "status": "succeeded"},
            {"candidate_id": "candidate-b", "text": "second transcript", "status": "succeeded"},
        ],
    }


def _configuration(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "correction_policy": "restricted_choice" if enabled else "deterministic",
        "feature_flags": {"restricted_worker": enabled},
    }


def _service(
    transport: _Transport | None,
    *,
    policy_service: PathAiModePolicyService | None = None,
    manifest_engine: str = "huggingface-transformers",
    device: str = "cpu",
) -> VoiceRestrictedChoiceService:
    return VoiceRestrictedChoiceService(
        inference_port=ContractRestrictedInferencePort(transport) if transport else None,
        manifest_resolver=lambda: "voice-choice-manifest-v1",
        manifest_engine_resolver=lambda: manifest_engine,
        device_resolver=lambda: device,
        policy_service=policy_service,
    )


def _apply(service: VoiceRestrictedChoiceService, base: Mapping[str, Any], *, enabled: bool = True):
    return service.apply(
        base,
        effective_configuration=_configuration(enabled=enabled),
        tenant_id="tenant@example.org",
        task_id="voice-restricted-choice",
        run_id="voice-run-1",
        request_id="voice-request-1",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
        policy_hash="e" * 64,
    )


def test_hub_selects_only_known_candidate_id_and_never_accepts_worker_text() -> None:
    transport = _Transport()
    base = _base_result()

    outcome = _apply(_service(transport), base)

    assert outcome.applied is True
    assert outcome.selected_candidate_id == "candidate-b"
    assert outcome.result["text"] == "second transcript"
    assert outcome.result["selected_candidate_id"] == "candidate-b"
    assert base["text"] == "first transcript"
    assert transport.request is not None
    assert list(transport.request.payload["choices"]) == ["candidate-a", "candidate-b"]
    assert transport.request.tenant_id.startswith("voice-tenant-")
    trace = outcome.result["decision_trace"]["restricted_choice"]
    assert trace["no_generation"] is True
    assert trace["manifest_digest"] == "d" * 64


def test_disabled_policy_does_not_call_worker_and_returns_same_object() -> None:
    transport = _Transport()
    base = _base_result()

    outcome = _apply(_service(transport), base, enabled=False)

    assert outcome.applied is False
    assert outcome.result is base
    assert transport.request is None


def test_worker_failure_tie_and_missing_port_preserve_exact_base_result() -> None:
    base = _base_result()
    failed = _apply(_service(_Transport(fail=True)), base)
    tied = _apply(_service(_Transport({"candidate-a": 0.5, "candidate-b": 0.5})), base)
    unavailable = _apply(_service(None), base)

    assert failed.result is base and failed.reason_code == "restricted_choice_failed"
    assert tied.result is base and tied.reason_code == "ambiguous_choice_result"
    assert unavailable.result is base and unavailable.reason_code == "restricted_worker_unavailable"


def test_confirmed_base_candidate_is_not_rewritten() -> None:
    base = _base_result()
    outcome = _apply(_service(_Transport({"candidate-a": 0.8, "candidate-b": 0.2})), base)

    assert outcome.result is base
    assert outcome.applied is False
    assert outcome.reason_code == "base_candidate_confirmed"


def test_path_policy_blocks_voice_restricted_choice_before_worker_dispatch() -> None:
    transport = _Transport()
    policy = PathAiModePolicyService(
        [
            PathAiModeRule(
                path_glob="__voice__/**",
                blocked_ai_modes=frozenset({AI_MODE_RESTRICTED_TRANSFORMER}),
            )
        ]
    )

    outcome = _apply(_service(transport, policy_service=policy), _base_result())

    assert outcome.reason_code == "restricted_choice_policy_blocked"
    assert outcome.result is not None
    assert transport.request is None


def test_path_policy_enforces_logits_engine_and_request_limits() -> None:
    base = _base_result()

    logits_transport = _Transport()
    logits_policy = PathAiModePolicyService(
        [PathAiModeRule(path_glob="__voice__/**", allow_logits=False)]
    )
    logits = _apply(_service(logits_transport, policy_service=logits_policy), base)
    assert logits.reason_code == "restricted_choice_logits_blocked"
    assert logits_transport.request is None

    engine_transport = _Transport()
    engine_policy = PathAiModePolicyService(
        [
            PathAiModeRule(
                path_glob="__voice__/**",
                allowed_model_engines=frozenset({"onnxruntime"}),
            )
        ]
    )
    engine = _apply(_service(engine_transport, policy_service=engine_policy), base)
    assert engine.reason_code == "restricted_choice_engine_blocked"
    assert engine_transport.request is None

    batch_transport = _Transport()
    batch_policy = PathAiModePolicyService(
        [PathAiModeRule(path_glob="__voice__/**", max_batch_size=1)]
    )
    batch = _apply(_service(batch_transport, policy_service=batch_policy), base)
    assert batch.reason_code == "restricted_choice_batch_limit"
    assert batch_transport.request is None

    input_transport = _Transport()
    input_policy = PathAiModePolicyService(
        [PathAiModeRule(path_glob="__voice__/**", max_input_chars=8)]
    )
    bounded_input = _apply(_service(input_transport, policy_service=input_policy), base)
    assert bounded_input.reason_code == "restricted_choice_input_limit"
    assert input_transport.request is None


def test_path_policy_limits_are_forwarded_in_effective_execution_policy() -> None:
    transport = _Transport()
    policy = PathAiModePolicyService(
        [
            PathAiModeRule(
                path_glob="__voice__/**",
                allowed_model_engines=frozenset({"huggingface-transformers"}),
                max_batch_size=4,
                max_input_chars=4_096,
            )
        ]
    )

    outcome = _apply(
        _service(transport, policy_service=policy, device="cuda:0"),
        _base_result(),
    )

    assert outcome.applied is True
    assert transport.request is not None
    assert transport.request.paths == ("__voice__/transcript",)
    assert transport.request.execution_policy["max_batch_size"] == 4
    assert transport.request.execution_policy["max_input_chars"] == 4_096
    assert transport.request.execution_policy["device"] == "cuda:0"
    assert transport.request.execution_policy["allow_attention"] is False
    assert transport.request.execution_policy["allow_hidden_states"] is False
