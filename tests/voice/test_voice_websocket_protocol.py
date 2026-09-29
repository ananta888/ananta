from __future__ import annotations

import base64
import json
from typing import Any, Mapping

from agent.ws_voice import VOICE_WS_PATH, VOICE_WS_SCHEMA_VERSION, VoiceWebSocketSession


class _HubClient:
    def __init__(self) -> None:
        self.created: list[tuple[dict[str, object], str]] = []
        self.pushed: list[tuple[str, int, bytes]] = []
        self.finalized: list[str] = []
        self.cancelled: list[str] = []

    def create(self, payload: Mapping[str, object], *, idempotency_key: str) -> dict[str, Any]:
        self.created.append((dict(payload), idempotency_key))
        return {"stream": {"session_id": "hub-stream-1", "state": "created"}}

    def push(self, session_id: str, chunk_sequence: int, content: bytes) -> dict[str, Any]:
        self.pushed.append((session_id, chunk_sequence, content))
        return {
            "event": {
                "schema_version": VOICE_WS_SCHEMA_VERSION,
                "sequence": chunk_sequence + 1,
                "event_type": "partial",
                "payload": {"text": "teil"},
            }
        }

    def finalize(self, session_id: str) -> dict[str, Any]:
        self.finalized.append(session_id)
        return {
            "result": {"text": "fertig", "candidates": []},
            "result_ref": "voice-result-1",
            "event": {"event_type": "final"},
        }

    def cancel(self, session_id: str) -> dict[str, Any]:
        self.cancelled.append(session_id)
        return {"deleted": True}


def _message(sequence: int, message_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "schema_version": VOICE_WS_SCHEMA_VERSION,
            "sequence": sequence,
            "type": message_type,
            "payload": payload,
        },
        sort_keys=True,
    )


def test_voice_websocket_start_partial_replay_and_final_are_monotone() -> None:
    hub = _HubClient()
    session = VoiceWebSocketSession(hub)
    start = _message(
        0,
        "start",
        {
            "idempotency_key": "ws-create-1",
            "filename": "voice.pcm",
            "profile_id": "profile-a",
            "media_type": "audio/pcm;rate=16000;channels=1",
            "deadline_seconds": 30,
        },
    )
    chunk = _message(
        1,
        "audio_chunk",
        {"chunk_sequence": 0, "data_base64": base64.b64encode(b"\0\0" * 80).decode()},
    )

    ack = session.receive(start)
    partial = session.receive(chunk)
    replay = session.receive(chunk)
    final = session.receive(_message(2, "end", {}))

    assert [ack["type"], partial["type"], final["type"]] == ["ack", "partial", "final"]
    assert [ack["sequence"], partial["sequence"], final["sequence"]] == [0, 1, 2]
    assert replay == partial
    assert hub.pushed == [("hub-stream-1", 0, b"\0\0" * 80)]
    assert final["payload"]["result_ref"] == "voice-result-1"
    assert final["payload"]["result"]["text"] == "fertig"
    assert session.terminal is True


def test_voice_websocket_rejects_version_sequence_and_replay_conflicts() -> None:
    hub = _HubClient()
    session = VoiceWebSocketSession(hub)
    unsupported = json.dumps(
        {"schema_version": "future", "sequence": 0, "type": "start", "payload": {}}
    )
    assert session.receive(unsupported)["payload"]["error"]["code"] == "voice_stream.unsupported_version"

    gap = session.receive(_message(1, "start", {"idempotency_key": "gap"}))
    assert gap["payload"]["error"]["code"] == "voice_stream.sequence_gap"

    start = _message(0, "start", {"idempotency_key": "create"})
    assert session.receive(start)["type"] == "ack"
    conflict = session.receive(_message(0, "start", {"idempotency_key": "different"}))
    assert conflict["payload"]["error"]["code"] == "voice_stream.message_conflict"


def test_voice_websocket_bounds_message_count_and_replay_history() -> None:
    hub = _HubClient()
    session = VoiceWebSocketSession(
        hub,
        max_input_messages=4,
        replay_window_messages=1,
    )
    assert session.receive(_message(0, "start", {"idempotency_key": "bounded"}))["type"] == "ack"
    assert session.receive(
        _message(
            1,
            "audio_chunk",
            {"chunk_sequence": 0, "data_base64": base64.b64encode(b"a").decode()},
        )
    )["type"] == "partial"
    assert session.receive(
        _message(
            2,
            "audio_chunk",
            {"chunk_sequence": 1, "data_base64": base64.b64encode(b"b").decode()},
        )
    )["type"] == "partial"

    expired = session.receive(
        _message(
            1,
            "audio_chunk",
            {"chunk_sequence": 0, "data_base64": base64.b64encode(b"a").decode()},
        )
    )
    exhausted = session.receive(_message(4, "end", {}))

    assert expired["payload"]["error"]["code"] == "voice_stream.replay_window_expired"
    assert exhausted["payload"]["error"]["code"] == "voice_stream.message_limit_exceeded"


def test_voice_websocket_disconnect_cancels_active_hub_session() -> None:
    hub = _HubClient()
    session = VoiceWebSocketSession(hub)
    assert session.receive(_message(0, "start", {"idempotency_key": "disconnect"}))["type"] == "ack"

    session.disconnect()

    assert hub.cancelled == ["hub-stream-1"]
    assert session.terminal is True


def test_voice_websocket_route_is_registered_on_the_hub(app) -> None:
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert VOICE_WS_PATH in rules
