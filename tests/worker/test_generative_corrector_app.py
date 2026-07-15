from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ananta_contracts.voice_corrector_worker import VoiceCorrectorWorkerRequest
from worker.runtime.generative_corrector_app import CORRECTOR_ENDPOINT, create_app
from worker.runtime.generative_corrector_engine import (
    EmbeddedTransformersGenerativeCorrectorEngine,
    GenerativeCorrectorEngineResult,
)

TOKEN = "corrector-secret-at-least-24-characters"


class _Engine:
    def __init__(self, corrected_text: str | Exception = "Hallo Welt.") -> None:
        self.corrected_text = corrected_text
        self.requests: list[VoiceCorrectorWorkerRequest] = []

    @property
    def engine_id(self) -> str:
        return "fixture-engine"

    @property
    def model_ids(self) -> tuple[str, ...]:
        return ("gemma-2b-it", "phi-3-mini-instruct")

    def correct(self, request: VoiceCorrectorWorkerRequest) -> GenerativeCorrectorEngineResult:
        self.requests.append(request)
        if isinstance(self.corrected_text, Exception):
            raise self.corrected_text
        return GenerativeCorrectorEngineResult(
            corrected_text=self.corrected_text,
            model_id=request.model_id,
            model_revision="sha256-fixture",
            engine_id=self.engine_id,
        )


class _SnapshotEngine(_Engine):
    @property
    def model_ids(self) -> tuple[str, ...]:
        raise AssertionError("health must not split an atomic engine snapshot")

    @property
    def provider_ids(self) -> tuple[str, ...]:
        raise AssertionError("health must not split an atomic engine snapshot")

    @property
    def ready_provider_ids(self) -> tuple[str, ...]:
        raise AssertionError("health must not split an atomic engine snapshot")

    def health_snapshot(self) -> dict[str, tuple[str, ...]]:
        return {
            "model_ids": ("lmstudio:org/model",),
            "provider_ids": ("lmstudio",),
            "ready_provider_ids": ("lmstudio",),
        }


def _payload(
    *,
    original_text: str = "hallo welt",
    model_id: str = "gemma-2b-it",
    max_edit_ratio: float = 0.5,
) -> dict[str, object]:
    return VoiceCorrectorWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        original_text=original_text,
        model_id=model_id,
        language="de",
        max_edit_ratio=max_edit_ratio,
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    ).to_dict()


def _client(engine: _Engine):
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


def test_worker_requires_hub_auth_and_exposes_allowlisted_models() -> None:
    client = _client(_Engine())

    assert client.get("/health").json["model_ids"] == ["gemma-2b-it", "phi-3-mini-instruct"]
    assert (
        client.post(
            CORRECTOR_ENDPOINT,
            json=_payload(),
            headers={"Origin": "http://ai-agent-hub:5000"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            CORRECTOR_ENDPOINT,
            json=_payload(),
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://ai-agent-hub:5001"},
        ).status_code
        == 403
    )


def test_worker_health_reads_one_atomic_engine_snapshot() -> None:
    response = _client(_SnapshotEngine()).get("/health")

    assert response.status_code == 200
    assert response.json["status"] == "ready"
    assert response.json["model_ids"] == ["lmstudio:org/model"]
    assert response.json["provider_ids"] == ["lmstudio"]
    assert response.json["ready_provider_ids"] == ["lmstudio"]


def test_worker_returns_original_corrected_text_edits_and_model_provenance() -> None:
    engine = _Engine()
    response = _client(engine).post(CORRECTOR_ENDPOINT, json=_payload(), headers=_headers())

    assert response.status_code == 200
    assert response.json["original_text"] == "hallo welt"
    assert response.json["corrected_text"] == "Hallo Welt."
    assert response.json["status"] == "corrected"
    assert response.json["model_id"] == "gemma-2b-it"
    assert response.json["model_revision"] == "sha256-fixture"
    assert response.json["edits"]
    assert len(engine.requests) == 1


def test_worker_rejects_unknown_models_large_rewrites_and_changed_numbers() -> None:
    unknown = _client(_Engine()).post(
        CORRECTOR_ENDPOINT,
        json=_payload(model_id="unknown-model"),
        headers=_headers(),
    )
    large = _client(_Engine("ganz anderer inhalt")).post(
        CORRECTOR_ENDPOINT,
        json=_payload(max_edit_ratio=0.1),
        headers=_headers(),
    )
    number = _client(_Engine("Version 43 ist fertig.")).post(
        CORRECTOR_ENDPOINT,
        json=_payload(original_text="Version 42 ist fertig", max_edit_ratio=0.5),
        headers=_headers(),
    )

    assert unknown.status_code == 422
    assert unknown.json["reason_code"] == "model_not_allowlisted"
    assert large.status_code == 422
    assert large.json["reason_code"] == "edit_ratio_exceeded"
    assert number.status_code == 422
    assert number.json["reason_code"] == "protected_token_changed"


def test_embedded_engine_selects_only_a_catalog_model_and_parses_strict_json(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "gemma"
    model_path.mkdir(parents=True)
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "model.safetensors").write_bytes(b"fixture")
    catalog_path = tmp_path / "manifests" / "model-catalog.json"
    catalog_path.parent.mkdir()
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": "ananta.generative-corrector-model-catalog.v1",
                "models": [
                    {
                        "id": "gemma-2b-it",
                        "path": "models/gemma",
                        "revision": "sha256-fixture",
                        "family": "gemma",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = EmbeddedTransformersGenerativeCorrectorEngine(
        model_root=str(tmp_path),
        catalog_path=str(catalog_path),
    )

    class _Tensor:
        shape = (1, 3)

    class _Generated:
        def __getitem__(self, _key):
            return "generated-tokens"

    class _Tokenizer:
        eos_token_id = 0
        prompt = ""
        add_special_tokens = None

        def __call__(self, prompt, **kwargs):
            self.prompt = prompt
            self.add_special_tokens = kwargs.get("add_special_tokens")
            return {"input_ids": _Tensor()}

        def decode(self, _tokens, **_kwargs):
            return '{"schema_version":"1.0","corrected_text":"Hallo Welt."}'

    class _Model:
        def generate(self, **_kwargs):
            return [_Generated()]

    tokenizer = _Tokenizer()
    engine._tokenizer = tokenizer
    engine._model = _Model()
    engine._loaded_model_id = "gemma-2b-it"
    request_envelope = VoiceCorrectorWorkerRequest.from_dict(_payload())

    outcome = engine.correct(request_envelope)

    assert engine.model_ids == ("gemma-2b-it",)
    assert outcome.corrected_text == "Hallo Welt."
    assert outcome.model_revision == "sha256-fixture"
    assert "Original transcript:" in tokenizer.prompt
    assert '{"schema_version":"1.0","corrected_text":"Corrected transcript goes here"}' in tokenizer.prompt
    assert 'JSON string "1.0", never a number' in tokenizer.prompt
    assert tokenizer.prompt.endswith("JSON:")
    assert tokenizer.add_special_tokens is True

    class _ChatTokenizer(_Tokenizer):
        chat_template = "fixture-chat-template"
        messages = None

        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert tokenize is False
            assert add_generation_prompt is True
            if any(message["role"] == "system" for message in messages):
                raise ValueError("Gemma template rejects system messages")
            self.messages = messages
            return "<chat-generation-prompt>"

    chat_tokenizer = _ChatTokenizer()
    engine._tokenizer = chat_tokenizer
    chat_outcome = engine.correct(request_envelope)
    assert chat_outcome.corrected_text == "Hallo Welt."
    assert chat_tokenizer.prompt == "<chat-generation-prompt>"
    assert chat_tokenizer.add_special_tokens is False
    assert len(chat_tokenizer.messages) == 1
    assert chat_tokenizer.messages[0]["role"] == "user"
    assert "Do not summarize" in chat_tokenizer.messages[0]["content"]
    assert 'JSON string "1.0", never a number' in chat_tokenizer.messages[0]["content"]
    with pytest.raises(ValueError, match="invalid JSON"):
        engine._parse_output("Hallo Welt.")
    with pytest.raises(ValueError, match="response values"):
        engine._parse_output('{"schema_version":1,"corrected_text":"Hallo Welt."}')
