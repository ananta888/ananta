"""Hub-owned WebSocket facade for versioned Voice streaming.

The facade deliberately calls the Hub's authenticated HTTP streaming API. It
never addresses the Voice Runtime itself, so RBAC, tenant binding,
idempotency, artifact persistence and audit retain one control-plane owner.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qs

from agent.auth import authenticate_provided_token

try:
    from flask_sock import Sock
except ImportError:  # pragma: no cover - optional in minimal installations
    Sock = None  # type: ignore[assignment]

VOICE_WS_SCHEMA_VERSION = "ananta.voice-stream.v1"
VOICE_WS_PATH = "/ws/voice/v1"
_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
_MAX_AUDIO_CHUNK_BYTES = 1024 * 1024
_MAX_INPUT_MESSAGES = 65_540
_REPLAY_WINDOW_MESSAGES = 256
_LOGGER = logging.getLogger("agent.ws_voice")


@dataclass(frozen=True)
class HubVoiceStreamError(Exception):
    code: str
    message: str
    status_code: int
    retriable: bool = False


class HubVoiceStreamClient(Protocol):
    def create(self, payload: Mapping[str, object], *, idempotency_key: str) -> dict[str, Any]: ...

    def push(self, session_id: str, chunk_sequence: int, content: bytes) -> dict[str, Any]: ...

    def finalize(self, session_id: str) -> dict[str, Any]: ...

    def cancel(self, session_id: str) -> dict[str, Any]: ...


class FlaskHubVoiceStreamClient:
    """Small adapter around existing Hub routes; no voice policy is duplicated."""

    def __init__(self, app: Any, *, authorization: str) -> None:
        self._app = app
        self._authorization = authorization

    def create(self, payload: Mapping[str, object], *, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/voice/streams",
            json_payload=dict(payload),
            headers={"Idempotency-Key": idempotency_key},
        )

    def push(self, session_id: str, chunk_sequence: int, content: bytes) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/v1/voice/streams/{session_id}/chunks/{chunk_sequence}",
            data=content,
            content_type="application/octet-stream",
        )

    def finalize(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/voice/streams/{session_id}/finalize")

    def cancel(self, session_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v1/voice/streams/{session_id}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, object] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Authorization": self._authorization, **dict(headers or {})}
        with self._app.test_client() as client:
            response = client.open(
                path,
                method=method,
                json=json_payload,
                data=data,
                content_type=content_type,
                headers=request_headers,
            )
        payload = response.get_json(silent=True)
        envelope = payload if isinstance(payload, dict) else {}
        data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        if response.status_code >= 400:
            error = data_payload.get("error") if isinstance(data_payload.get("error"), dict) else {}
            raise HubVoiceStreamError(
                code=str(error.get("code") or "voice_stream.hub_error"),
                message=str(error.get("message") or "voice stream request failed"),
                status_code=int(response.status_code),
                retriable=bool(error.get("retriable")),
            )
        return dict(data_payload)


class VoiceWebSocketSession:
    """Deterministic protocol state machine independent of the socket library."""

    def __init__(
        self,
        client: HubVoiceStreamClient,
        *,
        max_input_messages: int = _MAX_INPUT_MESSAGES,
        replay_window_messages: int = _REPLAY_WINDOW_MESSAGES,
    ) -> None:
        self._client = client
        self._max_input_messages = max(1, max_input_messages)
        self._replay_window_messages = max(
            1,
            min(replay_window_messages, self._max_input_messages),
        )
        self._session_id: str | None = None
        self._next_input_sequence = 0
        self._next_event_sequence = 0
        self._terminal = False
        self._replays: dict[int, tuple[str, dict[str, Any]]] = {}

    @property
    def terminal(self) -> bool:
        return self._terminal

    def receive(self, raw_message: str | bytes) -> dict[str, Any]:
        try:
            message, digest = _decode_message(raw_message)
            input_sequence = _required_int(message, "sequence", minimum=0)
            if input_sequence >= self._max_input_messages:
                raise HubVoiceStreamError(
                    "voice_stream.message_limit_exceeded",
                    "stream exceeds its message-count budget",
                    413,
                )
            replay = self._replays.get(input_sequence)
            if replay is not None:
                if replay[0] != digest:
                    raise HubVoiceStreamError("voice_stream.message_conflict", "replayed message differs", 409)
                return dict(replay[1])
            if input_sequence < self._next_input_sequence:
                raise HubVoiceStreamError(
                    "voice_stream.replay_window_expired",
                    "replayed message is outside the bounded replay window",
                    409,
                )
            if input_sequence != self._next_input_sequence:
                raise HubVoiceStreamError(
                    "voice_stream.sequence_gap",
                    f"expected message {self._next_input_sequence}",
                    409,
                    True,
                )
            if message.get("schema_version") != VOICE_WS_SCHEMA_VERSION:
                raise HubVoiceStreamError("voice_stream.unsupported_version", "unsupported stream version", 422)
            event = self._dispatch(message)
            self._replays[input_sequence] = (digest, event)
            self._next_input_sequence += 1
            oldest_retained = max(0, self._next_input_sequence - self._replay_window_messages)
            for replay_sequence in tuple(self._replays):
                if replay_sequence < oldest_retained:
                    self._replays.pop(replay_sequence, None)
            return event
        except HubVoiceStreamError as exc:
            return self._event(
                "error",
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retriable": exc.retriable,
                        "status": exc.status_code,
                    }
                },
            )
        except (TypeError, ValueError, binascii.Error, UnicodeError, json.JSONDecodeError):
            return self._event(
                "error",
                {
                    "error": {
                        "code": "voice_stream.invalid_message",
                        "message": "stream message is invalid",
                        "retriable": False,
                        "status": 422,
                    }
                },
            )

    def disconnect(self) -> None:
        if self._session_id and not self._terminal:
            try:
                self._client.cancel(self._session_id)
            except Exception:
                _LOGGER.warning("voice websocket cleanup failed", extra={"operation": "cancel"})
        self._terminal = True

    def _dispatch(self, message: Mapping[str, Any]) -> dict[str, Any]:
        message_type = str(message.get("type") or "")
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        if self._terminal:
            raise HubVoiceStreamError("voice_stream.invalid_state", "stream is already terminal", 409)
        if self._session_id is None:
            if message_type != "start":
                raise HubVoiceStreamError("voice_stream.start_required", "start must be the first message", 409)
            idempotency_key = str(payload.get("idempotency_key") or "").strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise HubVoiceStreamError(
                    "voice_stream.idempotency_required", "start requires an idempotency key", 422
                )
            created = self._client.create(
                {
                    "filename": str(payload.get("filename") or "stream.pcm")[:255],
                    "language": str(payload.get("language") or "")[:32] or None,
                    "media_type": str(
                        payload.get("media_type") or "audio/pcm;rate=16000;channels=1"
                    )[:128],
                    "deadline_seconds": payload.get("deadline_seconds"),
                    "profile_id": str(payload.get("profile_id") or "default")[:128],
                },
                idempotency_key=idempotency_key,
            )
            stream = created.get("stream") if isinstance(created.get("stream"), dict) else {}
            self._session_id = str(stream.get("session_id") or "")
            if not self._session_id:
                raise HubVoiceStreamError("voice_stream.invalid_hub_response", "Hub returned no session", 502)
            return self._event(
                "ack",
                {"state": str(stream.get("state") or "created"), "next_chunk_sequence": 0},
            )
        if message_type == "audio_chunk":
            chunk_sequence = _required_int(payload, "chunk_sequence", minimum=0)
            encoded = payload.get("data_base64")
            if not isinstance(encoded, str) or len(encoded) > (_MAX_AUDIO_CHUNK_BYTES * 4 // 3) + 8:
                raise HubVoiceStreamError("voice_stream.invalid_chunk", "audio chunk is invalid", 422)
            content = base64.b64decode(encoded, validate=True)
            if not content or len(content) > _MAX_AUDIO_CHUNK_BYTES:
                raise HubVoiceStreamError("voice_stream.invalid_chunk", "audio chunk is invalid", 422)
            result = self._client.push(self._session_id, chunk_sequence, content)
            runtime_event = result.get("event") if isinstance(result.get("event"), dict) else {}
            event_type = "partial" if runtime_event.get("event_type") == "partial" else "ack"
            return self._event(
                event_type,
                {
                    "chunk_sequence": chunk_sequence,
                    "runtime_event": runtime_event,
                },
            )
        if message_type == "end":
            result = self._client.finalize(self._session_id)
            self._terminal = True
            return self._event(
                "final",
                {
                    "result": result.get("result") if isinstance(result.get("result"), dict) else {},
                    "result_ref": result.get("result_ref"),
                    "runtime_event": result.get("event") if isinstance(result.get("event"), dict) else {},
                },
            )
        if message_type == "cancel":
            self._client.cancel(self._session_id)
            self._terminal = True
            return self._event("cancelled", {"state": "cancelled"})
        raise HubVoiceStreamError("voice_stream.unknown_message_type", "unknown stream message type", 422)

    def _event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "schema_version": VOICE_WS_SCHEMA_VERSION,
            "session_id": self._session_id,
            "sequence": self._next_event_sequence,
            "type": event_type,
            "payload": payload,
        }
        self._next_event_sequence += 1
        return event


def register_ws_voice(app: Any) -> None:
    if Sock is None:
        _LOGGER.warning("flask-sock not installed, %s endpoint disabled", VOICE_WS_PATH)
        return
    sock = Sock(app)

    @sock.route(VOICE_WS_PATH)
    def ws_voice(ws: Any) -> None:
        authorization = _authorization_from_environ(getattr(ws, "environ", {}) or {})
        supplied_token = authorization.removeprefix("Bearer ") if authorization else None
        authenticated, _auth_mode = authenticate_provided_token(supplied_token)
        if not authorization or not authenticated:
            ws.send(
                json.dumps(
                    {
                        "schema_version": VOICE_WS_SCHEMA_VERSION,
                        "session_id": None,
                        "sequence": 0,
                        "type": "error",
                        "payload": {
                            "error": {
                                "code": "voice_stream.authentication_required",
                                "message": "authentication is required",
                                "retriable": False,
                                "status": 401,
                            }
                        },
                    }
                )
            )
            return
        session = VoiceWebSocketSession(FlaskHubVoiceStreamClient(app, authorization=authorization))
        try:
            while not session.terminal:
                message = ws.receive()
                if message is None:
                    break
                ws.send(json.dumps(session.receive(message), separators=(",", ":")))
        finally:
            session.disconnect()


def _authorization_from_environ(environ: Mapping[str, Any]) -> str | None:
    header = str(environ.get("HTTP_AUTHORIZATION") or "").strip()
    if header.startswith("Bearer ") and len(header) > len("Bearer "):
        return header
    query = parse_qs(str(environ.get("QUERY_STRING") or ""))
    token = str((query.get("token") or [""])[0]).strip()
    return f"Bearer {token}" if token else None


def _decode_message(raw_message: str | bytes) -> tuple[dict[str, Any], str]:
    if isinstance(raw_message, bytes):
        raw = raw_message
    elif isinstance(raw_message, str):
        raw = raw_message.encode("utf-8")
    else:
        raise TypeError("stream message must be text or bytes")
    if not raw or len(raw) > _MAX_MESSAGE_BYTES:
        raise ValueError("stream message size is invalid")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("stream message must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def _required_int(payload: Mapping[str, Any], field: str, *, minimum: int) -> int:
    raw = payload.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ValueError(f"{field} must be an integer")
    return raw
