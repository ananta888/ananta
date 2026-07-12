from __future__ import annotations

from typing import Any, Mapping, cast

from agent.services.path_ai_mode_policy_service import PathAiModePolicyService
from agent.services.pre_model_context_config import MODE_PREFER_CONTEXT
from agent.services.pre_model_context_orchestrator import PreModelContextOrchestrator
from agent.services.restricted_inference_contract import CONTRACT_VERSION, RestrictedInferenceRequest
from agent.services.restricted_inference_port import ContractRestrictedInferencePort
from agent.services.restricted_model_inference_service import RestrictedModelInferenceService


class _RerankTransport:
    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        request = RestrictedInferenceRequest.from_dict(envelope)
        candidates = list(request.payload["candidates"])
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": request.request_id,
            "task_id": request.task_id,
            "operation": "rerank",
            "status": "succeeded",
            "result": {
                "items": [
                    {
                        "path": str(candidate["path"]),
                        "record_id": str(candidate["record_id"]),
                        "score": 0.9 if candidate["record_id"] == "a" else 0.1,
                        "confidence": 1.0,
                        "reason_code": "cross_encoder",
                    }
                    for candidate in candidates
                ],
                "engine": "sentence-transformers",
                "model_id": "fixture/cross-encoder",
                "manifest_digest": "c" * 64,
                "latency_ms": 3.0,
            },
            "error": None,
            "no_generation": True,
        }


def test_codecompass_rerank_uses_worker_port_and_traces_manifest_digest() -> None:
    inference = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(_RerankTransport()),
        manifest_resolver=lambda _operation: "cross-encoder-manifest-v1",
        legacy_local_enabled=False,
        use_mock_fallback=False,
        policy_service=PathAiModePolicyService(),
    )
    orchestrator = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/a.py", "record_id": "a", "excerpt": "auth token", "embedding_score": 0.1},
            {"path": "agent/b.py", "record_id": "b", "excerpt": "logging", "embedding_score": 0.9},
        ],
        restricted_inference_service=inference,
    )

    result = orchestrator.orchestrate(
        task_text="auth token",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "restricted_inference_rerank_enabled": True,
                "trace_scores": True,
                "fallback_without_model": True,
                "score_weights": {
                    "embedding_score": 0.0,
                    "graph_score": 0.0,
                    "symbol_score": 0.0,
                    "transformer_rerank_score": 1.0,
                    "policy_penalty": -0.2,
                },
            },
        },
    )

    assert result.context_package is not None
    candidates = result.context_package.to_dict()["candidates"]
    assert candidates[0]["record_id"] == "a"
    assert candidates[0]["transformer_manifest_digest"] == "c" * 64
    assert candidates[0]["score_trace"]["manifest_digest"] == "c" * 64


def test_codecompass_disabled_rerank_preserves_original_ranking_without_worker_call() -> None:
    class _FailIfCalled:
        def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
            raise AssertionError("worker must not be called when reranking is disabled")

    inference = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(_FailIfCalled()),
        manifest_resolver=lambda _operation: "cross-encoder-manifest-v1",
        legacy_local_enabled=False,
        use_mock_fallback=False,
    )
    orchestrator = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/a.py", "record_id": "a", "embedding_score": 0.1},
            {"path": "agent/b.py", "record_id": "b", "embedding_score": 0.9},
        ],
        restricted_inference_service=inference,
    )

    result = orchestrator.orchestrate(
        task_text="query",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "retrieval_strategy": "direct",
                "restricted_inference_rerank_enabled": False,
            },
        },
    )

    assert result.context_package is not None
    assert [item["record_id"] for item in result.context_package.to_dict()["candidates"]] == ["b", "a"]


def _fallback_result_for_failure(error: Exception):
    class _FailingReranker:
        def rerank(self, _query: str, _candidates: list[dict[str, Any]]):
            raise error

    orchestrator = PreModelContextOrchestrator(
        retrieve_fn=lambda _task, _domain, _workspace, _budget: [
            {"path": "agent/a.py", "record_id": "a", "embedding_score": 0.1},
            {"path": "agent/b.py", "record_id": "b", "embedding_score": 0.9},
        ],
        restricted_inference_service=cast(
            RestrictedModelInferenceService,
            _FailingReranker(),
        ),
    )
    return orchestrator.orchestrate(
        task_text="query",
        user_config={
            "pre_model_context": {"enabled": True, "mode": MODE_PREFER_CONTEXT},
            "codecompass_ranking": {
                "restricted_inference_rerank_enabled": True,
                "fallback_without_model": True,
            },
        },
    )


def test_codecompass_timeout_preserves_ranking_and_redacts_worker_error() -> None:
    result = _fallback_result_for_failure(TimeoutError("secret query text"))

    assert result.context_package is not None
    assert [item["record_id"] for item in result.context_package.to_dict()["candidates"]] == ["b", "a"]
    event = next(item for item in result.trace.events if item.event == "restricted_rerank_error")
    assert event.data == {"reason_code": "timeout"}
    assert "secret query text" not in repr(result.trace)


def test_codecompass_model_error_preserves_ranking_with_stable_reason() -> None:
    result = _fallback_result_for_failure(RuntimeError("sensitive model path"))

    assert result.context_package is not None
    assert [item["record_id"] for item in result.context_package.to_dict()["candidates"]] == ["b", "a"]
    event = next(item for item in result.trace.events if item.event == "restricted_rerank_error")
    assert event.data == {"reason_code": "model_error"}
    assert "sensitive model path" not in repr(result.trace)
