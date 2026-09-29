from __future__ import annotations

import io
import wave

import pytest

from voice_runtime.app import create_app
from voice_runtime.config import VoiceRuntimeConfig


def _client(*, token: str | None = None):
    app = create_app(
        VoiceRuntimeConfig(
            enable_streaming=True,
            internal_service_token=token,
            backend_fallback_order=("mock",),
        )
    )
    app.config.update(TESTING=True)
    return app.test_client()


def _wav_bytes(*, duration_ms: int = 10) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x00\x00" * (16_000 * duration_ms // 1_000))
    return output.getvalue()


def test_streaming_api_orders_chunks_and_returns_final_envelope():
    client = _client(token="internal-token-value")
    headers = {"X-Ananta-Internal-Token": "internal-token-value", "X-Request-ID": "request-1"}

    created = client.post(
        "/v1/audio/streams",
        json={"filename": "sample.wav", "media_type": "audio/wav", "language": "de"},
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.get_json()["session_id"]

    chunk = client.put(f"/v1/audio/streams/{session_id}/chunks/0", data=_wav_bytes(), headers=headers)
    final = client.post(f"/v1/audio/streams/{session_id}/finalize", headers=headers)
    snapshot = client.get(f"/v1/audio/streams/{session_id}", headers=headers)

    assert chunk.status_code == 202
    assert final.status_code == 200
    assert final.get_json()["event"]["event_type"] == "final"
    assert snapshot.get_json()["schema_version"] == "ananta.voice-stream.v1"
    assert snapshot.get_json()["state"] == "final"


def test_runtime_internal_auth_is_fail_closed_when_configured():
    client = _client(token="internal-token-value")

    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/audio/streams", json={}).status_code == 401


def test_streaming_api_reports_sequence_gap_without_leaking_details():
    client = _client()
    created = client.post("/v1/audio/streams", json={"filename": "sample.webm", "media_type": "audio/webm"})
    session_id = created.get_json()["session_id"]

    response = client.put(f"/v1/audio/streams/{session_id}/chunks/2", data=b"audio")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "stream.sequence_gap"


def test_streaming_api_rejects_non_positive_audio_budget():
    client = _client()

    response = client.post(
        "/v1/audio/streams",
        json={
            "filename": "sample.pcm",
            "media_type": "audio/pcm;rate=16000;channels=1",
            "max_audio_seconds": 0,
        },
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "stream.invalid_audio_budget"


def test_streaming_api_does_not_upgrade_small_container_duration_claim():
    client = _client()
    created = client.post(
        "/v1/audio/streams",
        json={
            "filename": "sample.wav",
            "media_type": "audio/wav",
            "max_audio_seconds": 0.001,
        },
    )

    assert created.status_code == 201
    assert created.get_json()["max_audio_seconds"] == 0.001
    session_id = created.get_json()["session_id"]
    assert client.put(f"/v1/audio/streams/{session_id}/chunks/0", data=_wav_bytes()).status_code == 202

    final = client.post(f"/v1/audio/streams/{session_id}/finalize")
    snapshot = client.get(f"/v1/audio/streams/{session_id}")

    assert final.status_code == 413
    assert final.get_json()["error"]["code"] == "stream.audio_duration_exceeded"
    assert snapshot.get_json()["state"] == "failed"
    assert snapshot.get_json()["result"] is None


@pytest.mark.parametrize(
    "requested_session_id",
    (
        "runtime-session-without-prefix",
        "vs_too-short",
        f"vs_{'A' * 21}",
        f"vs_{'A' * 129}",
        "vs_token/with/path-separator",
        42,
    ),
)
def test_streaming_api_rejects_invalid_requested_session_id(requested_session_id):
    client = _client()

    response = client.post(
        "/v1/audio/streams",
        json={
            "filename": "sample.webm",
            "media_type": "audio/webm",
            "requested_session_id": requested_session_id,
        },
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "stream.invalid_session_id"


def test_streaming_api_returns_exact_requested_session_id_and_rejects_duplicate():
    client = _client()
    requested_session_id = f"vs_{'hub-provisional-token-' * 2}"
    payload = {
        "filename": "sample.webm",
        "media_type": "audio/webm",
        "requested_session_id": requested_session_id,
    }

    created = client.post("/v1/audio/streams", json=payload)
    duplicate = client.post("/v1/audio/streams", json=payload)

    assert created.status_code == 201
    assert created.get_json()["session_id"] == requested_session_id
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["code"] == "stream.session_id_conflict"
