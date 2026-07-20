from __future__ import annotations

import json

import pytest

from agent.services.speech_adaptation_worker_port import (
    HttpSpeechAdaptationWorkerPort,
    SpeechAdaptationWorkerTransportError,
    normalize_speech_worker_endpoint,
)
from tests.speech_adaptation_support import speech_job


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode()

    def read(self, maximum: int) -> bytes:
        return self._payload[:maximum]


class _Connection:
    response = None
    requests = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def request(self, method, path, body=None, headers=None):
        self.requests.append((self.host, self.port, method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        return None


def _port(resolver=lambda _host, _port: ["172.20.0.7"]):
    endpoint = "http://speech-worker:8097/internal/v1/speech-training"
    return HttpSpeechAdaptationWorkerPort(
        endpoint=endpoint,
        allowed_endpoints=(endpoint,),
        bearer_token="speech-worker-test-token-00000001",
        resolver=resolver,
    )


def test_worker_endpoint_is_exact_and_redirect_free() -> None:
    assert (
        normalize_speech_worker_endpoint("http://speech-worker:8097/internal/v1/speech-training/")
        == "http://speech-worker:8097/internal/v1/speech-training"
    )
    for invalid in (
        "https://speech-worker:8097/internal/v1/speech-training",
        "http://speech-worker:8097/internal/v1/speech-training?next=public",
        "http://user@speech-worker:8097/internal/v1/speech-training",
    ):
        with pytest.raises(ValueError):
            normalize_speech_worker_endpoint(invalid)


def test_submit_connects_to_pinned_private_ip_and_binds_response(monkeypatch) -> None:
    job = speech_job()
    _Connection.requests = []
    _Connection.response = _Response(
        202,
        {
            "contract_version": job.contract_version,
            "job_id": job.job_id,
            "attempt_id": job.attempt.attempt_id,
            "status": "accepted",
        },
    )
    monkeypatch.setattr("agent.services.speech_adaptation_worker_port.http.client.HTTPConnection", _Connection)
    result = _port().submit(job)
    assert result.job_id == job.job_id
    host, _, method, path, _, headers = _Connection.requests[0]
    assert host == "172.20.0.7"
    assert method == "POST"
    assert path == "/internal/v1/speech-training/jobs"
    assert headers["Host"] == "speech-worker:8097"
    assert headers["Authorization"].startswith("Bearer ")


def test_public_dns_or_redirect_fails_closed(monkeypatch) -> None:
    with pytest.raises(SpeechAdaptationWorkerTransportError) as captured:
        _port(lambda _host, _port: ["8.8.8.8"]).submit(speech_job())
    assert captured.value.reason_code == "worker_address_forbidden"

    _Connection.response = _Response(302, {})
    monkeypatch.setattr("agent.services.speech_adaptation_worker_port.http.client.HTTPConnection", _Connection)
    with pytest.raises(SpeechAdaptationWorkerTransportError) as captured:
        _port().submit(speech_job())
    assert captured.value.reason_code == "speech_worker_redirect_forbidden"
