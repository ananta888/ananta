"""Integration test against a real whisper.cpp-audio-decision ``whisper-server``.

Reported as SKIPPED (never as passed) unless a real server is available:

* ``VOICE_AUDIO_DECISION_IT_URL`` + ``VOICE_AUDIO_DECISION_IT_API_KEY``: an already running server, or
* ``VOICE_AUDIO_DECISION_IT_SERVER_BIN`` + ``VOICE_AUDIO_DECISION_IT_MODEL`` (ggml-base.en.bin) +
  ``VOICE_AUDIO_DECISION_IT_PROFILES_DIR``: the test starts the server on 127.0.0.1 itself.

Optional: ``VOICE_AUDIO_DECISION_IT_AUDIO`` (a speech clip <= 30 s, e.g. the fork's samples/jfk.wav).

Calibrated ok case (``speech-commands-en`` on ``ggml-base.en.bin``): ``VOICE_AUDIO_DECISION_IT_COMMAND_AUDIO``
is a single spoken command word, ideally a Speech Commands v0.02 clip from the holdout split (the
profile's calibration used the other speakers), and ``VOICE_AUDIO_DECISION_IT_COMMAND_LABEL`` its label
(default ``stop``). Without a clip the test is SKIPPED, never passed.

Stream sessions need a VAD model on the server (``--decision-vad-model``; for a self-started server
``VOICE_AUDIO_DECISION_IT_VAD_MODEL``); a server without one answers ``not_configured`` -> SKIPPED.
"""
from __future__ import annotations

import io
import os
import secrets
import socket
import subprocess
import time
import urllib.request
import wave
from pathlib import Path

import pytest

from agent.services.audio_decision_command_policy import VoiceCommandAudioDecisionPolicy
from agent.services.audio_decision_command_service import run_audio_decision_command
from agent.services.audio_decision_hub_gate import (
    AudioDecisionKind,
    HubAction,
    PolicyVerdict,
    gate_audio_decision,
    gate_stream_event,
)
from voice_runtime.backends.audio_decision import (
    FIELD_STATUSES,
    AudioDecisionConfig,
    AudioDecisionProvider,
    StreamOptions,
)
from voice_runtime.execution_control import BackendCancellationToken

pytestmark = pytest.mark.integration

PROFILES = ("speech-commands-en", "home-control-en", "confirm-en-de", "intent-semantic-experimental")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_ready(url: str, process: subprocess.Popen, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"whisper-server exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("whisper-server did not become ready")


@pytest.fixture(scope="module")
def server():
    url = os.environ.get("VOICE_AUDIO_DECISION_IT_URL", "").strip()
    key = os.environ.get("VOICE_AUDIO_DECISION_IT_API_KEY", "").strip()
    if url and key:
        yield url, key
        return
    binary = os.environ.get("VOICE_AUDIO_DECISION_IT_SERVER_BIN", "").strip()
    model = os.environ.get("VOICE_AUDIO_DECISION_IT_MODEL", "").strip()
    profiles = os.environ.get("VOICE_AUDIO_DECISION_IT_PROFILES_DIR", "").strip()
    available = binary and model and profiles
    if not (available and Path(binary).is_file() and Path(model).is_file() and Path(profiles).is_dir()):
        pytest.skip("no real whisper-server/model available (set VOICE_AUDIO_DECISION_IT_* to run)")
    key = secrets.token_urlsafe(24)
    port = _free_port()
    command = [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port), "--decision-profiles", profiles,
               "--decision-api-key", key, "--decision-workers", "2"]
    vad_model = os.environ.get("VOICE_AUDIO_DECISION_IT_VAD_MODEL", "").strip()
    if vad_model and Path(vad_model).is_file():
        command += ["--decision-vad-model", vad_model]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(url, process)
        yield url, key
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _provider(url: str, key: str) -> AudioDecisionProvider:
    config = AudioDecisionConfig(enabled=True, url=url, api_key=key, timeout_ms=30_000, profiles=PROFILES)
    return AudioDecisionProvider(config)


def _silence(duration_ms: int = 1000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * (16 * duration_ms))
    return buffer.getvalue()


def _speech() -> bytes:
    path = os.environ.get("VOICE_AUDIO_DECISION_IT_AUDIO", "").strip()
    if not path or not Path(path).is_file():
        pytest.skip("no speech clip available (set VOICE_AUDIO_DECISION_IT_AUDIO)")
    return Path(path).read_bytes()


def _allow(_proposal):
    return "allow"


def test_profiles_listing_is_filtered_to_the_allowlist(server):
    ids = {item["id"] for item in _provider(*server).profiles()}
    assert "speech-commands-en" in ids and ids <= set(PROFILES)


def test_silence_is_a_typed_non_value_with_provenance(server):
    outcome = _provider(*server).decide(
        filename="silence.wav", content=_silence(), profile="speech-commands-en", language="en"
    )
    assert outcome.ok, outcome.error_code
    assert outcome.provenance.model and outcome.provenance.profile_id == "speech-commands-en"
    assert outcome.provenance.profile_version and outcome.provenance.profile_status
    assert outcome.provenance.n_encode == 1
    command = outcome.fields["command"]
    assert command.status in FIELD_STATUSES
    assert command.status != "ok" and command.value is None
    hub = gate_audio_decision(outcome, field_name="command", policy=_allow)
    assert hub.action is HubAction.ASK_AGAIN and hub.value is None


def test_speech_with_transcription_fallback_reaches_system2(server):
    outcome = _provider(*server).decide(
        filename="speech.wav", content=_speech(), profile="speech-commands-en", language="en", fallback="transcribe"
    )
    assert outcome.ok, outcome.error_code
    command = outcome.fields["command"]
    if command.status == "ok":
        assert gate_audio_decision(outcome, field_name="command", policy=_allow).kind in {
            AudioDecisionKind.PROPOSAL,
            AudioDecisionKind.RANKING,
        }
        return
    assert outcome.fallback_used and outcome.transcript
    assert outcome.provenance.fallback_provenance
    hub = gate_audio_decision(outcome, field_name="command", policy=_allow)
    assert hub.value is None and hub.action in {HubAction.SYSTEM2, HubAction.ASK_AGAIN}


def test_semantic_profile_is_unsupported_and_routed_to_system2(server):
    provider = _provider(*server)
    if "intent-semantic-experimental" not in {item["id"] for item in provider.profiles()}:
        pytest.skip("intent-semantic-experimental profile not loaded by the server")
    outcome = provider.decide(
        filename="speech.wav", content=_speech(), profile="intent-semantic-experimental", language="en"
    )
    assert outcome.ok, outcome.error_code
    assert all(item.status == "unsupported" and item.value is None for item in outcome.fields.values())
    assert outcome.fallback_used and outcome.system2_required and outcome.transcript
    name = next(iter(outcome.fields))
    assert gate_audio_decision(outcome, field_name=name, policy=_allow).action is HubAction.SYSTEM2


def test_wrong_key_is_unauthorized_and_takes_the_normal_path(server):
    url, _key = server
    outcome = _provider(url, "wrong-key-" + secrets.token_hex(8)).decide(
        filename="silence.wav", content=_silence(), profile="speech-commands-en"
    )
    assert outcome.ok is False and outcome.error_code == "unauthorized"
    assert gate_audio_decision(outcome, field_name="command", policy=_allow).action is HubAction.NORMAL_PATH


# --- calibrated ok case (speech-commands-en on base.en) -------------------------------------------

CALIBRATED_MODEL = "base/v51864/l6/f1"  # ggml-base.en.bin, the profile's calibration entry


def _command_clip() -> tuple[bytes, str]:
    path = os.environ.get("VOICE_AUDIO_DECISION_IT_COMMAND_AUDIO", "").strip()
    if not path or not Path(path).is_file():
        pytest.skip(
            "no spoken command clip available (set VOICE_AUDIO_DECISION_IT_COMMAND_AUDIO to a Speech Commands "
            "clip, e.g. sc/stop/<speaker>_nohash_<n>.wav, and VOICE_AUDIO_DECISION_IT_COMMAND_LABEL)"
        )
    label = os.environ.get("VOICE_AUDIO_DECISION_IT_COMMAND_LABEL", "stop").strip()
    return Path(path).read_bytes(), label


def test_calibrated_ok_command_is_a_policy_checked_proposal(server):
    clip, label = _command_clip()
    outcome = _provider(*server).decide(
        filename="command.wav", content=clip, profile="speech-commands-en", fields=("command",), language="en"
    )
    assert outcome.ok, outcome.error_code
    if outcome.provenance.model != CALIBRATED_MODEL:
        pytest.skip(f"server model {outcome.provenance.model} has no calibration entry (needs ggml-base.en.bin)")
    command = outcome.fields["command"]
    assert command.status == "ok", command.reasons
    assert command.value == label
    assert command.calibrated is True and command.confidence is not None and 0.0 < command.confidence <= 1.0
    assert outcome.provenance.profile_id == "speech-commands-en" and outcome.provenance.n_encode == 1

    policy = VoiceCommandAudioDecisionPolicy()
    hub = gate_audio_decision(outcome, field_name="command", policy=policy)
    assert hub.kind is AudioDecisionKind.PROPOSAL and hub.value == label and hub.grants_permission is False
    # The same calibrated value is still only a proposal: the hub policy decides.
    assert gate_audio_decision(outcome, field_name="command", policy=lambda _p: "deny").action is HubAction.DENY
    action = policy.action_for("speech-commands-en", "command", label)
    assert action is not None
    confident = command.confidence >= policy.allow_min_confidence
    expected = HubAction.ACT if action.direct and confident else HubAction.CONFIRM
    assert hub.action is expected

    flow = run_audio_decision_command(
        _provider(*server), filename="command.wav", content=clip, profile="speech-commands-en",
        field_name="command", language="en", fallback="transcribe", policy=policy,
    ).as_response()
    assert flow["decision_kind"] == "proposal" and flow["hub_action"] == expected.value
    assert flow["action"]["type"] == action.action_type and flow["grants_permission"] is False
    assert flow["policy"]["verdict"] in {PolicyVerdict.ALLOW.value, PolicyVerdict.CONFIRM.value}


# --- stream sessions ---------------------------------------------------------------------------------


def _open_stream(provider: AudioDecisionProvider, **kwargs):
    opened = provider.open_stream(profile="speech-commands-en", fields=("command",), language="en", **kwargs)
    if not opened.ok and opened.error_code == "not_configured":
        pytest.skip("server has no --decision-vad-model; stream sessions are not configured")
    assert opened.ok, opened.error_code
    return opened.session


def test_stream_session_finalizes_the_command_clip(server):
    clip, label = _command_clip()
    provider = _provider(*server)
    pcm = provider.decode_pcm(filename="command.wav", content=clip)
    assert isinstance(pcm, bytes), getattr(pcm, "error_code", None)
    silence = b"\x00\x00" * 16_000  # 1 s
    with _open_stream(provider, options=StreamOptions(min_silence_ms=300)) as session:
        pushed = session.push(silence + pcm + silence, flush=True)
        assert pushed.ok, pushed.error_code
        finals = [event for event in pushed.events if event.is_final]
        assert finals, [event.type for event in pushed.events]
        final = finals[0]
        assert final.outcome is not None and final.outcome.ok, final.outcome
        assert final.outcome.provenance.profile_id == "speech-commands-en"
        hub = gate_stream_event(final, field_name="command", policy=VoiceCommandAudioDecisionPolicy())
        assert hub is not None and hub.grants_permission is False
        if final.outcome.fields["command"].status == "ok":
            assert hub.value == label and hub.action in {HubAction.ACT, HubAction.CONFIRM}
        else:
            assert hub.value is None and hub.action in {HubAction.ASK_AGAIN, HubAction.SYSTEM2}
    assert session.closed
    # A closed session refuses further pushes locally (remote deletion: see the cancellation test).
    assert session.push(silence).error_code == "session_closed"


def test_stream_session_cancellation_closes_the_server_session(server):
    provider = _provider(*server)
    session = _open_stream(provider)
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 10)
    token.cancel()
    result = session.push(b"\x00\x00" * 1600, cancellation_token=token)
    assert result.ok is False and result.error_code == "cancelled" and result.events == () and session.closed
    status, _body = provider._send(
        "GET", f"/v1/audio/decisions/sessions/{session.session_id}", body=None, content_type=None, timeout_s=5
    )
    assert status == 404
