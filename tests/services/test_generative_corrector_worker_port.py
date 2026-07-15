from __future__ import annotations

import json
import time
from typing import Any

import pytest

from agent.services.generative_corrector_worker_port import (
    GenerativeCorrectorWorkerTransportError,
    HttpGenerativeCorrectorWorkerPort,
)
from ananta_contracts.voice_corrector_worker import (
    CONTRACT_VERSION,
    VoiceCorrectorWorkerRequest,
    VoiceCorrectorWorkerResponse,
    build_edits,
)

ENDPOINT = "http://generative-corrector-worker:8093/internal/v1/voice-corrector"


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.body = json.dumps(payload).encode()
        self.offset = 0
        self.headers = {"Content-Type": "application/json"}

    def read(self, count: int) -> bytes:
        chunk = self.body[self.offset : self.offset + count]
        self.offset += len(chunk)
        return chunk


class _Opener:
    def __init__(self, *, health_payload: dict[str, Any] | None = None) -> None:
        self.health_payload = health_payload
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        if request.data is None:
            return _Response(
                self.health_payload
                or {
                    "service": "generative-corrector-worker",
                    "status": "ready",
                    "contract_version": CONTRACT_VERSION,
                    "auth_configured": True,
                    "origin_allowlist_configured": True,
                    "engine_configured": True,
                    "model_ids": ["gemma-2b-it"],
                }
            )
        envelope = VoiceCorrectorWorkerRequest.from_dict(json.loads(request.data))
        corrected = "Hallo Welt."
        return _Response(
            VoiceCorrectorWorkerResponse(
                request_id=envelope.request_id,
                task_id=envelope.task_id,
                status="corrected",
                original_text=envelope.original_text,
                corrected_text=corrected,
                edits=build_edits(envelope.original_text, corrected),
                reason_code=None,
                model_id=envelope.model_id,
                model_revision="sha256-fixture",
                engine_id="fixture-engine",
                prompt_version="prompt-v1",
            ).to_dict()
        )


def _request(model_id: str = "gemma-2b-it") -> VoiceCorrectorWorkerRequest:
    return VoiceCorrectorWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        original_text="hallo welt",
        model_id=model_id,
        language="de",
        max_edit_ratio=0.5,
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def _port(opener: _Opener, *, resolver=lambda _host, _port: ("10.77.0.4",)):
    return HttpGenerativeCorrectorWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token="internal-secret-at-least-24-characters",
        hub_origin="http://ai-agent-hub:5000",
        resolver=resolver,
        opener=opener,
    )


def test_http_port_pins_execution_and_health_to_the_same_private_origin() -> None:
    opener = _Opener()
    port = _port(opener)

    response = port.execute(_request())
    health = port.health(timeout_ms=500)

    assert response.corrected_text == "Hallo Welt."
    assert health["model_ids"] == ["gemma-2b-it"]
    assert opener.requests[0][0].full_url.startswith("http://10.77.0.4:8093/internal/")
    assert opener.requests[1][0].full_url == "http://10.77.0.4:8093/health"
    assert all(
        item[0].get_header("Host") == "generative-corrector-worker:8093"
        for item in opener.requests
    )


def test_http_port_accepts_qualified_models_and_bounded_provider_health_metadata() -> None:
    opener = _Opener(
        health_payload={
            "service": "generative-corrector-worker",
            "status": "ready",
            "contract_version": CONTRACT_VERSION,
            "auth_configured": True,
            "origin_allowlist_configured": True,
            "engine_configured": True,
            "model_ids": ["ollama:qwen2.5:7b", "lmstudio:org/model"],
            "provider_ids": ["ollama", "lmstudio"],
            "ready_provider_ids": ["ollama", "lmstudio"],
        }
    )
    port = _port(opener)

    response = port.execute(_request("ollama:qwen2.5:7b"))
    health = port.health()

    assert response.model_id == "ollama:qwen2.5:7b"
    assert health["model_ids"] == ["ollama:qwen2.5:7b", "lmstudio:org/model"]
    assert health["provider_ids"] == ["ollama", "lmstudio"]
    assert health["ready_provider_ids"] == ["ollama", "lmstudio"]


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "169.254.169.254"])
def test_http_port_rejects_non_private_worker_resolution(address: str) -> None:
    with pytest.raises(GenerativeCorrectorWorkerTransportError, match="private container address"):
        _port(_Opener(), resolver=lambda _host, _port: (address,)).health()


def test_health_contract_rejects_unversioned_or_unbounded_model_metadata() -> None:
    opener = _Opener(
        health_payload={
            "service": "generative-corrector-worker",
            "status": "ready",
            "contract_version": "unknown",
            "auth_configured": True,
            "origin_allowlist_configured": True,
            "engine_configured": True,
            "model_ids": ["invented"],
        }
    )

    with pytest.raises(GenerativeCorrectorWorkerTransportError, match="health response"):
        _port(opener).health()


def test_health_contract_rejects_invalid_provider_metadata() -> None:
    opener = _Opener(
        health_payload={
            "service": "generative-corrector-worker",
            "status": "ready",
            "contract_version": CONTRACT_VERSION,
            "auth_configured": True,
            "origin_allowlist_configured": True,
            "engine_configured": True,
            "model_ids": ["ollama:qwen2.5:7b"],
            "provider_ids": ["ollama", "https://attacker.invalid"],
        }
    )

    with pytest.raises(GenerativeCorrectorWorkerTransportError, match="provider metadata"):
        _port(opener).health()


def test_health_contract_rejects_ready_provider_without_configured_endpoint() -> None:
    opener = _Opener(
        health_payload={
            "service": "generative-corrector-worker",
            "status": "ready",
            "contract_version": CONTRACT_VERSION,
            "auth_configured": True,
            "origin_allowlist_configured": True,
            "engine_configured": True,
            "model_ids": ["gemma-2b-it"],
            "provider_ids": ["ollama"],
            "ready_provider_ids": ["lmstudio"],
        }
    )

    with pytest.raises(GenerativeCorrectorWorkerTransportError, match="ready-provider metadata"):
        _port(opener).health()
