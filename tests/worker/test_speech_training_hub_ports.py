from __future__ import annotations

import io
import json

from tests.speech_adaptation_support import speech_job
from worker.speech_training.hub_ports import (
    HttpHubSpeechTrainingPorts,
    HubValidatedMockDatasetResolver,
)


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self._body


class _Connection:
    responses: list[_Response] = []
    requests: list[tuple] = []
    addresses: list[tuple[str, int]] = []

    def __init__(self, address: str, port: int, *, timeout: float) -> None:
        del timeout
        self.addresses.append((address, port))
        self.sock = None

    def request(self, method, path, *, body, headers) -> None:
        content = body.read() if hasattr(body, "read") else bytes(body)
        self.requests.append((method, path, content, dict(headers)))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def _ports(monkeypatch) -> HttpHubSpeechTrainingPorts:
    monkeypatch.setattr("worker.speech_training.hub_ports.http.client.HTTPConnection", _Connection)
    return HttpHubSpeechTrainingPorts(
        endpoint="http://hub:5000/internal/v1/speech-adaptation-control",
        allowed_endpoints=("http://hub:5000/internal/v1/speech-adaptation-control",),
        bearer_token="callback-token-with-at-least-32-characters",
        resolver=lambda _host, _port: ["10.8.0.2"],
    )


def test_hub_authority_and_artifact_clients_pin_address_and_bind_metadata(monkeypatch) -> None:
    _Connection.responses = [
        _Response(200, {"active": True, "reason_code": None}),
        _Response(
            201,
            {
                "artifact_id": "speech-adapter-test",
                "artifact_ref": "artifact://speech-adapters/test/speech-adapter-test",
                "sha256": "a" * 64,
                "size_bytes": 3,
            },
        ),
    ]
    _Connection.requests = []
    _Connection.addresses = []
    ports = _ports(monkeypatch)
    job = speech_job()
    assert ports.verify(job, phase="before_audio_access") == (True, None)
    receipt = ports.publish(
        job_id=job.job_id,
        attempt_id=job.attempt.attempt_id,
        fencing_digest=job.fencing.fencing_digest,
        binding_digest=job.binding_digest,
        target_id="speech-adapter-test",
        target_ref="artifact://speech-adapters/test/speech-adapter-test",
        sha256="a" * 64,
        size_bytes=3,
        media_type="application/vnd.ananta.speech-adapter",
        stream=io.BytesIO(b"abc"),
    )
    assert receipt.size_bytes == 3
    assert _Connection.addresses == [("10.8.0.2", 5000), ("10.8.0.2", 5000)]
    assert _Connection.requests[1][2] == b"abc"
    assert _Connection.requests[1][3]["Host"] == "hub:5000"
    assert "X-Ananta-Artifact-Metadata" in _Connection.requests[1][3]


def test_hub_client_fails_closed_on_mixed_public_dns_and_dataset_denial(tmp_path) -> None:
    ports = HttpHubSpeechTrainingPorts(
        endpoint="http://hub:5000/internal/v1/speech-adaptation-control",
        allowed_endpoints=("http://hub:5000/internal/v1/speech-adaptation-control",),
        bearer_token="callback-token-with-at-least-32-characters",
        resolver=lambda _host, _port: ["10.8.0.2", "203.0.113.9"],
    )
    active, reason = ports.verify(speech_job(), phase="before_audio_access")
    assert not active and reason == "speech_hub_callback_address_forbidden"

    class _Denied:
        def verify(self, _job, *, phase):
            assert phase == "before_audio_access"
            return False, "speech_consent_revoked"

    resolver = HubValidatedMockDatasetResolver(tmp_path, _Denied())
    import pytest

    with pytest.raises(Exception, match="Hub rejected the current dataset binding"):
        resolver.open_admitted(speech_job())
    assert list(tmp_path.rglob("*")) == []
