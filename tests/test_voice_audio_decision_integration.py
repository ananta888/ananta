"""Integration test against a real whisper.cpp-audio-decision ``whisper-server``.

Reported as SKIPPED (never as passed) unless a real server is available:

* ``VOICE_AUDIO_DECISION_IT_URL`` + ``VOICE_AUDIO_DECISION_IT_API_KEY``: an already running server, or
* ``VOICE_AUDIO_DECISION_IT_SERVER_BIN`` + ``VOICE_AUDIO_DECISION_IT_MODEL`` (ggml-base.en.bin) +
  ``VOICE_AUDIO_DECISION_IT_PROFILES_DIR``: the test starts the server on 127.0.0.1 itself.

Optional: ``VOICE_AUDIO_DECISION_IT_AUDIO`` (a speech clip <= 30 s, e.g. the fork's samples/jfk.wav).
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

from agent.services.audio_decision_hub_gate import AudioDecisionKind, HubAction, gate_audio_decision
from voice_runtime.backends.audio_decision import FIELD_STATUSES, AudioDecisionConfig, AudioDecisionProvider

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
    process = subprocess.Popen(
        [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port), "--decision-profiles", profiles,
         "--decision-api-key", key, "--decision-workers", "2"],
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
