"""AudioDecision stream sessions (``/v1/audio/decisions/sessions*``) with mocked HTTP."""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import logging
import threading
import time

import pytest

from agent.services.audio_decision_command_policy import VoiceCommandAudioDecisionPolicy
from agent.services.audio_decision_hub_gate import HubAction, gate_stream_event
from tests.test_voice_audio_decision_provider import KEY, _config, _error, _field, _result, _wav
from voice_runtime.backends.audio_decision import (
    AudioDecisionProvider,
    HttpClientTransport,
    StreamEvent,
    StreamOptions,
)
from voice_runtime.execution_control import BackendCancellationToken

SID = "0123456789abcdef0123456789abcdef"
MODEL = "base/v51864/l6/f1"
SECRET_TRANSCRIPT = "transfer all the money to my neighbour"
PROFILES = ("speech-commands-en", "home-control-en", "intent-semantic-experimental")


class ScriptedTransport:
    """Answers per (method, path-prefix); records every call."""

    def __init__(self, script: dict[tuple[str, str], list]):
        self.script = {key: list(values) for key, values in script.items()}
        self.calls: list[dict] = []
        self.on_request = None

    def request(self, method, url, *, body, headers, timeout_s):
        self.calls.append(
            {"method": method, "url": url, "body": body, "headers": dict(headers), "timeout_s": timeout_s}
        )
        if self.on_request is not None:
            self.on_request(method, url)
        path = url.split("8081", 1)[1]
        for (m, prefix), answers in sorted(self.script.items(), key=lambda item: -len(item[0][1])):
            if m == method and path.startswith(prefix) and answers:
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if isinstance(answer, Exception):
                    raise answer
                status, payload = answer
                return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        raise AssertionError(f"unexpected request {method} {path}")

    def methods(self) -> list[tuple[str, str]]:
        return [(call["method"], call["url"].split("8081", 1)[1]) for call in self.calls]


def _created(**extra) -> tuple[int, dict]:
    payload = {"object": "audio.decision.session", "id": SID, "sample_rate": 16000, "formats": ["s16le", "f32le"],
               "idle_timeout_ms": 60000, "max_audio_ms": 1800000, "max_chunk_bytes": 1 << 20}
    payload.update(extra)
    return 201, payload


def _decision(field: dict, **kwargs) -> dict:
    payload = _result({"command": field}, profile="speech-commands-en", **kwargs)
    payload["model"] = MODEL
    return payload


def _events(*events, position_ms=1000, in_speech=False, session=SID) -> tuple[int, dict]:
    return 200, {"object": "audio.decision.events", "session": session, "position_ms": position_ms,
                 "in_speech": in_speech, "events": list(events)}


def _event(kind: str, seq: int, result: dict | None = None, **extra) -> dict:
    payload = {"object": "audio.decision.event", "type": kind, "seq": seq, "t0_ms": 100, "t1_ms": 900}
    if result is not None:
        payload["result"] = result
    payload.update(extra)
    return payload


DELETED = (200, {"object": "audio.decision.session", "id": SID, "deleted": True})
SESSIONS = "/v1/audio/decisions/sessions"
AUDIO = f"{SESSIONS}/{SID}/audio"


def _provider(transport, **config) -> AudioDecisionProvider:
    return AudioDecisionProvider(_config(profiles=PROFILES, **config), transport=transport)


def _open(transport, **kwargs):
    kwargs.setdefault("profile", "speech-commands-en")
    result = _provider(transport).open_stream(**kwargs)
    return result


@contextlib.contextmanager
def decision_log():
    """Capture the provider logger even if the hub test app disabled existing loggers."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("voice_runtime.backends.audio_decision")
    previous_level, previous_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.disabled = previous_disabled


def _pcm(ms: int) -> bytes:
    return b"\x10\x00" * (16 * ms)


# --- open ----------------------------------------------------------------------------------------


def test_open_sends_validated_request_and_returns_a_session():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()]})
    result = _open(transport, fields=("command",), language="en", fallback="transcribe",
                   options=StreamOptions(min_silence_ms=400, partial_every_ms=300, threshold=0.6))
    assert result.ok and result.session is not None and result.session.session_id == SID
    call = transport.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["body"]) == {
        "profile": "speech-commands-en",
        "options": {"language": "en", "fallback": "transcribe"},
        "fields": ["command"],
        "stream": {"threshold": 0.6, "min_silence_ms": 400, "partial_every_ms": 300},
    }


def test_semantic_profile_streams_always_request_a_transcript():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()]})
    assert _open(transport, profile="intent-semantic-experimental").ok
    assert json.loads(transport.calls[0]["body"])["options"]["fallback"] == "transcribe"


@pytest.mark.parametrize(
    "options",
    [StreamOptions(partial_every_ms=100), StreamOptions(threshold=1.5), StreamOptions(max_speech_ms=60_000),
     StreamOptions(min_speech_ms=True)],
)
def test_invalid_stream_options_are_rejected_locally(options):
    transport = ScriptedTransport({})
    result = _open(transport, options=options)
    assert result.ok is False and result.error_code == "invalid_request" and transport.calls == []


def test_profile_outside_the_allowlist_never_leaves_the_process():
    transport = ScriptedTransport({})
    result = _open(transport, profile="confirm-en-de")
    assert result.ok is False and result.error_code == "profile_not_allowed" and transport.calls == []


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ((501, {"object": "error", "error": {"code": "not_configured", "message": "x"}}), "not_configured"),
        ((429, _error("too_many_sessions")), "too_many_sessions"),
        ((401, _error("unauthorized")), "unauthorized"),
        ((201, {"object": "audio.decision.session", "id": "../../etc"}), "bad_response"),
        ((201, b"not json"), "bad_response"),
        ((200, [1, 2]), "bad_response"),
        (TimeoutError(), "deadline_exceeded"),
        (ConnectionRefusedError(), "unavailable"),
    ],
)
def test_open_failures_are_typed_and_carry_no_session(answer, code):
    transport = ScriptedTransport({("POST", SESSIONS): [answer]})
    result = _open(transport)
    assert result.ok is False and result.session is None and result.error_code == code


def test_open_with_wrong_sample_rate_closes_the_server_session():
    transport = ScriptedTransport({("POST", SESSIONS): [_created(sample_rate=8000)], ("DELETE", SESSIONS): [DELETED]})
    result = _open(transport)
    assert result.ok is False and result.error_code == "bad_response"
    assert ("DELETE", f"{SESSIONS}/{SID}") in transport.methods()


def test_open_with_cancelled_token_sends_nothing():
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 10)
    token.cancel()
    transport = ScriptedTransport({})
    result = _open(transport, cancellation_token=token)
    assert result.ok is False and result.error_code == "cancelled" and transport.calls == []


# --- push / events ------------------------------------------------------------------------------


def _session(transport):
    result = _open(transport)
    assert result.ok
    return result.session


def test_push_returns_typed_events_and_only_partials_finals_carry_outcomes():
    final = _decision(_field("ok", "stop", calibrated=True, confidence=0.97))
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()],
        ("POST", AUDIO): [_events(
            _event("speech_start", 0),
            _event("partial", 1, _decision(_field("ok", "go"))),
            _event("final", 2, final, debounced=False, supersedes=[1]),
            _event("revoked", 3, supersedes=[1]),
            _event("dropped", 4),
            _event("mystery", 5, final),
            {"object": "audio.decision.event", "type": "final", "result": final},  # no seq
            "garbage",
            position_ms=1520, in_speech=True,
        )],
    })
    session = _session(transport)
    pushed = session.push(_pcm(500))
    assert pushed.ok and pushed.position_ms == 1520 and pushed.in_speech is True
    assert [event.type for event in pushed.events] == ["speech_start", "partial", "final", "revoked", "dropped"]
    start, partial, final_event, revoked, dropped = pushed.events
    assert start.outcome is None and revoked.outcome is None and dropped.outcome is None
    assert revoked.supersedes == (1,)
    assert partial.outcome.ok and partial.outcome.fields["command"].calibrated is False
    assert final_event.is_final and final_event.outcome.value("command") == "stop"
    assert final_event.outcome.provenance.model == MODEL
    call = transport.calls[-1]
    assert call["url"].endswith(f"{AUDIO}?format=s16le")
    assert call["headers"]["Content-Type"] == "application/octet-stream" and len(call["body"]) == 16000


def test_final_without_or_with_broken_result_is_no_decision():
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()],
        ("POST", AUDIO): [_events(
            _event("final", 1),
            _event("final", 2, _error("deadline_exceeded")),
            _event("final", 3, _decision(_field("ok", "stop"), api_version="audio.decision.v2")),
            _event("final", 4, {**_decision(_field("ok", "stop")), "profile": {"id": "home-control-en"}}),
        )],
    })
    events = _session(transport).push(_pcm(200)).events
    codes = [event.outcome.error_code for event in events]
    assert all(event.outcome.ok is False and event.outcome.value("command") is None for event in events)
    assert codes == ["bad_response", "deadline_exceeded", "api_version_mismatch", "bad_response"]
    policy = VoiceCommandAudioDecisionPolicy()
    assert {gate_stream_event(e, field_name="command", policy=policy).action for e in events} == {HubAction.NORMAL_PATH}


def test_stream_gate_only_acts_on_non_debounced_finals():
    calibrated = _decision(_field("ok", "stop", calibrated=True, confidence=0.97))
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()],
        ("POST", AUDIO): [_events(
            _event("partial", 1, calibrated),
            _event("final", 2, calibrated),
            _event("final", 3, calibrated, debounced=True),
            _event("final", 4, _decision(_field("abstain", reasons=["out_of_set"]),
                                         fallback={"used": True, "transcript": SECRET_TRANSCRIPT,
                                                   "system2_required": True})),
            _event("final", 5, _decision(_field("ok", "stop"))),
        )],
    })
    events = _session(transport).push(_pcm(200)).events
    policy = VoiceCommandAudioDecisionPolicy()
    gated = [gate_stream_event(event, field_name="command", policy=policy) for event in events]
    assert gated[0] is None and gated[2] is None
    assert gated[1].action is HubAction.ACT and gated[1].grants_permission is False
    assert gated[3].action is HubAction.SYSTEM2 and gated[3].system2_transcript == SECRET_TRANSCRIPT
    assert gated[4].action is HubAction.CONFIRM


def test_stream_gate_treats_a_final_without_outcome_as_no_decision():
    event = StreamEvent(type="final", seq=1, t0_ms=0, t1_ms=10)
    assert gate_stream_event(event, field_name="command", policy=lambda _p: "allow").action is HubAction.NORMAL_PATH


def test_large_pushes_are_chunked_and_flush_marks_only_the_last_chunk():
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created(max_chunk_bytes=10_001)],
        ("POST", AUDIO): [_events(_event("speech_start", 0)), _events(), _events(_event("final", 1, _decision(
            _field("ok", "stop"))))],
    })
    session = _session(transport)
    pushed = session.push(_pcm(1000)[:25_000], flush=True)
    assert pushed.ok and [event.type for event in pushed.events] == ["speech_start", "final"]
    audio_calls = [call for call in transport.calls if "/audio?" in call["url"]]
    assert [len(call["body"]) for call in audio_calls] == [10_000, 10_000, 5_000]
    assert [call["url"].endswith("&flush=1") for call in audio_calls] == [False, False, True]


def test_flush_sends_an_empty_final_push():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("POST", AUDIO): [_events()]})
    assert _session(transport).flush().ok
    assert transport.calls[-1]["body"] == b"" and transport.calls[-1]["url"].endswith("format=s16le&flush=1")


def test_invalid_pcm_and_session_limit_are_rejected_locally():
    transport = ScriptedTransport({("POST", SESSIONS): [_created(max_audio_ms=1000)]})
    session = _session(transport)
    assert session.push(b"\x00\x00\x00").error_code == "invalid_audio"
    assert session.push(b"").error_code == "invalid_audio"
    assert session.push(_pcm(1500)).error_code == "session_audio_limit"
    assert len(transport.calls) == 1 and not session.closed


def test_cancelled_token_closes_the_session_without_pushing():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("DELETE", SESSIONS): [DELETED]})
    session = _session(transport)
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 10)
    token.cancel()
    result = session.push(_pcm(100), cancellation_token=token)
    assert result.ok is False and result.error_code == "cancelled" and result.closed and result.events == ()
    assert transport.methods()[1:] == [("DELETE", f"{SESSIONS}/{SID}")]
    assert session.push(_pcm(100)).error_code == "session_closed"
    assert len(transport.calls) == 2


def test_cancel_during_push_discards_events_and_closes():
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()],
        ("POST", AUDIO): [_events(_event("final", 1, _decision(_field("ok", "stop", calibrated=True,
                                                                        confidence=0.99))))],
        ("DELETE", SESSIONS): [DELETED],
    })
    session = _session(transport)
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 10)
    transport.on_request = lambda method, url: token.cancel() if url.endswith("format=s16le") else None
    result = session.push(_pcm(100), cancellation_token=token)
    assert result.ok is False and result.error_code == "cancelled" and result.events == () and session.closed
    assert transport.methods()[-1] == ("DELETE", f"{SESSIONS}/{SID}")


def test_expired_deadline_is_reported_as_deadline_exceeded():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("DELETE", SESSIONS): [DELETED]})
    session = _session(transport)
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() - 1)
    assert session.push(_pcm(100), cancellation_token=token).error_code == "deadline_exceeded"


def test_push_timeout_is_bounded_by_the_token_deadline():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("POST", AUDIO): [_events()]})
    session = _session(transport)
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 0.5)
    assert session.push(_pcm(100), cancellation_token=token).ok
    assert transport.calls[-1]["timeout_s"] <= 0.5


@pytest.mark.parametrize(
    ("answer", "code", "closed", "deleted"),
    [
        (TimeoutError(), "deadline_exceeded", True, True),
        (ConnectionResetError(), "unavailable", True, True),
        ((404, {"object": "error", "error": {"code": "not_found", "message": "x"}}), "session_expired", True, False),
        ((409, {"object": "error", "error": {"code": "busy", "message": "x"}}), "busy", False, False),
        ((429, _error("queue_full")), "queue_full", False, False),
        ((400, _error("invalid_request")), "invalid_request", True, True),
        ((200, b"{broken"), "bad_response", True, True),
        (_events(session="f" * 32), "bad_response", True, True),
    ],
)
def test_push_failures_are_typed_without_events(answer, code, closed, deleted):
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()], ("POST", AUDIO): [answer], ("DELETE", SESSIONS): [DELETED],
    })
    session = _session(transport)
    result = session.push(_pcm(100))
    assert result.ok is False and result.error_code == code and result.events == ()
    assert session.closed is closed
    assert (("DELETE", f"{SESSIONS}/{SID}") in transport.methods()) is deleted


def test_close_is_idempotent_and_context_manager_closes():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("DELETE", SESSIONS): [DELETED]})
    with _session(transport) as session:
        pass
    assert session.closed and session.close() is True
    assert [m for m, _p in transport.methods()].count("DELETE") == 1


def test_close_survives_transport_errors():
    transport = ScriptedTransport({("POST", SESSIONS): [_created()], ("DELETE", SESSIONS): [OSError("down")]})
    session = _session(transport)
    assert session.close() is False and session.closed


def test_decode_pcm_uses_ananta_limits():
    provider = _provider(ScriptedTransport({}))
    pcm = provider.decode_pcm(filename="clip.wav", content=_wav(500))
    assert isinstance(pcm, bytes) and len(pcm) == 16_000
    assert provider.decode_pcm(filename="clip.wav", content=b"").error_code == "invalid_audio"
    assert provider.decode_pcm(filename="clip.wav", content=_wav(31_000)).error_code == "audio_too_long"


def test_stream_logs_carry_no_audio_transcript_label_or_key():
    result_payload = _decision(_field("abstain", reasons=["out_of_set"]),
                               fallback={"used": True, "transcript": SECRET_TRANSCRIPT, "system2_required": True})
    transport = ScriptedTransport({
        ("POST", SESSIONS): [_created()],
        ("POST", AUDIO): [_events(_event("final", 1, result_payload),
                                  _event("final", 2, _decision(_field("ok", "stop"))))],
        ("DELETE", SESSIONS): [DELETED],
    })
    with decision_log() as log:
        with _session(transport) as session:
            assert session.push(_pcm(100)).ok
    text = log.getvalue()
    assert "audio decision stream push session=01234567 ok=True error=None events=2 finals=2" in text
    assert "audio decision stream close" in text
    assert SECRET_TRANSCRIPT not in text and KEY not in text and SID not in text and "stop" not in text


# --- real stdlib transport keeps the query string ------------------------------------------------


def test_http_transport_forwards_the_query_string():
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen.append(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, _body = HttpClientTransport().request(
            "POST", f"http://127.0.0.1:{port}{AUDIO}?format=s16le&flush=1", body=b"\x00\x00", headers={},
            timeout_s=2,
        )
    finally:
        server.shutdown()
    assert status == 200 and seen == [f"{AUDIO}?format=s16le&flush=1"]
