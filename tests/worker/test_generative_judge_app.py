from __future__ import annotations

import time
from pathlib import Path

import pytest

from ananta_contracts.generative_judge_worker import (
    GenerativeJudgeCandidate,
    GenerativeJudgeWorkerRequest,
)
from worker.runtime.generative_judge_app import JUDGE_ENDPOINT, create_app
from worker.runtime.generative_judge_engine import EmbeddedTransformersGenerativeJudgeEngine

TOKEN = "worker-secret-at-least-24-characters"


class _Engine:
    def __init__(self, selection: str | Exception = "candidate-001") -> None:
        self.selection = selection
        self.requests: list[GenerativeJudgeWorkerRequest] = []

    @property
    def engine_id(self) -> str:
        return "fixture-engine"

    def select(self, request: GenerativeJudgeWorkerRequest) -> str:
        self.requests.append(request)
        if isinstance(self.selection, Exception):
            raise self.selection
        return self.selection


def _payload() -> dict[str, object]:
    return GenerativeJudgeWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        candidates=(
            GenerativeJudgeCandidate("candidate-000", "baseline"),
            GenerativeJudgeCandidate("candidate-001", "alternative"),
        ),
        baseline_choice_id="candidate-000",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    ).to_dict()


def _client(engine: _Engine | None = None):
    return create_app(
        engine=engine,
        auth_token=TOKEN,
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Origin": "http://ai-agent-hub:5000",
    }


def test_worker_requires_auth_and_exact_hub_origin() -> None:
    client = _client(_Engine())

    unauthorized = client.post(
        JUDGE_ENDPOINT,
        json=_payload(),
        headers={"Origin": "http://ai-agent-hub:5000"},
    )
    assert unauthorized.status_code == 401
    forbidden = client.post(
        JUDGE_ENDPOINT,
        json=_payload(),
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://ai-agent-hub:5001"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json["reason_code"] == "hub_origin_forbidden"


def test_worker_executes_only_injected_engine_and_returns_candidate_id() -> None:
    engine = _Engine()
    response = _client(engine).post(JUDGE_ENDPOINT, json=_payload(), headers=_headers())

    assert response.status_code == 200
    assert response.json["execution_owner"] == "worker"
    assert response.json["choice_id"] == "candidate-001"
    assert "text" not in response.json
    assert len(engine.requests) == 1


def test_worker_fails_closed_without_engine_or_for_unknown_engine_choice() -> None:
    unavailable_app = create_app(
        engine=None,
        auth_token=TOKEN,
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    )
    assert unavailable_app.test_client().get("/health").json["status"] == "degraded"
    assert unavailable_app.test_client().post(JUDGE_ENDPOINT, json=_payload(), headers=_headers()).status_code == 503

    invalid = _client(_Engine("invented")).post(JUDGE_ENDPOINT, json=_payload(), headers=_headers())
    assert invalid.status_code == 503
    assert invalid.json["status"] == "failed"
    assert invalid.json["choice_id"] is None
    assert "text" not in invalid.json

    weak_auth = create_app(
        engine=_Engine(),
        auth_token="short",
        allowed_hub_origins=("http://ai-agent-hub:5000",),
    ).test_client()
    assert weak_auth.get("/health").json["status"] == "degraded"
    assert weak_auth.post(JUDGE_ENDPOINT, json=_payload(), headers=_headers()).status_code == 503


def test_worker_maps_engine_deadline_and_releases_its_bounded_slot() -> None:
    engine = _Engine(TimeoutError("deadline"))
    client = _client(engine)

    timed_out = client.post(JUDGE_ENDPOINT, json=_payload(), headers=_headers())
    engine.selection = "candidate-001"
    recovered = client.post(JUDGE_ENDPOINT, json=_payload(), headers=_headers())

    assert timed_out.status_code == 504
    assert timed_out.json["reason_code"] == "judge_engine_timeout"
    assert recovered.status_code == 200


def test_worker_request_body_is_bounded() -> None:
    client = create_app(
        engine=_Engine(),
        auth_token=TOKEN,
        allowed_hub_origins=("http://ai-agent-hub:5000",),
        max_request_bytes=1024,
    ).test_client()
    response = client.post(
        JUDGE_ENDPOINT,
        data=b"{" + b"x" * 2048,
        headers={**_headers(), "Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json["reason_code"] == "request_too_large"


def test_embedded_transformers_engine_executes_in_process_and_returns_only_known_id(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "model.safetensors").write_bytes(b"fixture")
    engine = EmbeddedTransformersGenerativeJudgeEngine(
        model_path=str(model_path),
        model_root=str(tmp_path),
    )

    class _Tensor:
        shape = (1, 3)

    class _Generated:
        def __getitem__(self, _key):
            return "generated-tokens"

    class _Tokenizer:
        eos_token_id = 0

        def __call__(self, _prompt, **_kwargs):
            return {"input_ids": _Tensor()}

        def decode(self, _tokens, **_kwargs):
            return "candidate-001"

    class _Model:
        kwargs = None

        def generate(self, **_kwargs):
            self.kwargs = _kwargs
            return [_Generated()]

    engine._tokenizer = _Tokenizer()
    model = _Model()
    engine._model = model
    request_envelope = GenerativeJudgeWorkerRequest.from_dict(_payload())

    assert engine.select(request_envelope) == "candidate-001"
    assert model.kwargs["max_time"] > 0
    assert model.kwargs["max_new_tokens"] == 32


def test_embedded_engine_checks_absolute_deadline_after_tokenization(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "model.safetensors").write_bytes(b"fixture")
    engine = EmbeddedTransformersGenerativeJudgeEngine(
        model_path=str(model_path),
        model_root=str(tmp_path),
    )

    class _Tensor:
        shape = (1, 3)

    class _SlowTokenizer:
        eos_token_id = 0

        def __call__(self, _prompt, **_kwargs):
            time.sleep(0.01)
            return {"input_ids": _Tensor()}

    class _NeverCalledModel:
        def generate(self, **_kwargs):
            raise AssertionError("generation must not start after the deadline")

    engine._tokenizer = _SlowTokenizer()
    engine._model = _NeverCalledModel()
    payload = _payload()
    payload["deadline_epoch_ms"] = time.time_ns() // 1_000_000 + 1
    request_envelope = GenerativeJudgeWorkerRequest.from_dict(payload)

    with pytest.raises(TimeoutError, match="deadline"):
        engine.select(request_envelope)


def test_embedded_engine_rejects_pickle_style_or_symlinked_model_snapshots(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "pytorch_model.bin").write_bytes(b"pickle-like")

    with pytest.raises(ValueError, match="safetensors"):
        EmbeddedTransformersGenerativeJudgeEngine(
            model_path=str(unsafe),
            model_root=str(tmp_path),
        )

    safe = tmp_path / "safe"
    safe.mkdir()
    (safe / "model.safetensors").write_bytes(b"fixture")
    (safe / "escape").symlink_to(unsafe / "pytorch_model.bin")
    with pytest.raises(ValueError, match="non-symlink"):
        EmbeddedTransformersGenerativeJudgeEngine(
            model_path=str(safe),
            model_root=str(tmp_path),
        )
