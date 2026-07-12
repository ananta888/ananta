from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

import pytest

from agent.services.generative_judge_worker_port import (
    GenerativeJudgeWorkerTransportError,
    HttpGenerativeJudgeWorkerPort,
)
from ananta_contracts.generative_judge_worker import (
    CONTRACT_VERSION,
    GenerativeJudgeCandidate,
    GenerativeJudgeContractError,
    GenerativeJudgeWorkerRequest,
)

ENDPOINT = "http://generative-judge-worker:8092/internal/v1/generative-judge"


class _Response:
    def __init__(self, payload: dict[str, Any], *, padding: int = 0) -> None:
        self.body = json.dumps(payload).encode() + b" " * padding
        self.offset = 0
        self.headers = {"Content-Type": "application/json"}

    def read(self, count: int) -> bytes:
        chunk = self.body[self.offset : self.offset + count]
        self.offset += len(chunk)
        return chunk


class _Opener:
    def __init__(self, *, task_id: str | None = None, padding: int = 0) -> None:
        self.task_id = task_id
        self.padding = padding
        self.request = None
        self.timeout = 0.0

    def open(self, request, timeout: float):
        self.request = request
        self.timeout = timeout
        envelope = json.loads(request.data)
        return _Response(
            {
                "contract_version": CONTRACT_VERSION,
                "request_id": envelope["request_id"],
                "task_id": self.task_id or envelope["task_id"],
                "status": "selected",
                "choice_id": "candidate-001",
                "reason_code": None,
                "engine_id": "fixture-engine",
                "execution_owner": "worker",
            },
            padding=self.padding,
        )


def _request() -> GenerativeJudgeWorkerRequest:
    return GenerativeJudgeWorkerRequest(
        request_id="request-1",
        task_id="task-1",
        region_id="full-transcript",
        candidates=(
            GenerativeJudgeCandidate("candidate-000", "baseline"),
            GenerativeJudgeCandidate("candidate-001", "candidate"),
        ),
        baseline_choice_id="candidate-000",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def _port(opener: _Opener, *, resolver=lambda _host, _port: ("10.77.0.3",)):
    return HttpGenerativeJudgeWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token="internal-secret-at-least-24-characters",
        hub_origin="http://ai-agent-hub:5000",
        resolver=resolver,
        opener=opener,
    )


def test_http_port_pins_private_dns_and_sends_auth_origin_and_host() -> None:
    opener = _Opener()
    response = _port(opener).execute(_request())

    assert response.choice_id == "candidate-001"
    assert opener.request.full_url.startswith("http://10.77.0.3:8092/")
    assert opener.request.get_header("Authorization") == "Bearer internal-secret-at-least-24-characters"
    assert opener.request.get_header("Origin") == "http://ai-agent-hub:5000"
    assert opener.request.get_header("Host") == "generative-judge-worker:8092"
    assert opener.timeout > 0


def test_http_port_disables_environment_proxies(monkeypatch) -> None:
    opener = _Opener()
    handlers: tuple[object, ...] = ()

    def build_opener(*values: object) -> _Opener:
        nonlocal handlers
        handlers = values
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    HttpGenerativeJudgeWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token="internal-secret-at-least-24-characters",
        hub_origin="http://ai-agent-hub:5000",
    )

    proxy_handlers = [
        handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "169.254.169.254"])
def test_http_port_blocks_cloud_loopback_and_link_local_resolution(address: str) -> None:
    with pytest.raises(GenerativeJudgeWorkerTransportError, match="private container address"):
        _port(_Opener(), resolver=lambda _host, _port: (address,)).execute(_request())


def test_http_port_requires_exact_endpoint_allowlist_and_response_correlation() -> None:
    with pytest.raises(ValueError, match="exactly allowlisted"):
        HttpGenerativeJudgeWorkerPort(
            endpoint=ENDPOINT,
            allowed_endpoints=("http://other:8092/internal/v1/generative-judge",),
            bearer_token="internal-secret-at-least-24-characters",
            hub_origin="http://ai-agent-hub:5000",
        )

    with pytest.raises(GenerativeJudgeContractError, match="correlation"):
        _port(_Opener(task_id="different-task")).execute(_request())


def test_http_port_rejects_oversized_worker_response() -> None:
    with pytest.raises(GenerativeJudgeWorkerTransportError, match="byte limit"):
        _port(_Opener(padding=70_000)).execute(_request())
