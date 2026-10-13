"""Closed real entry with injected local renderer ports, no media service."""

import base64
import hashlib
import io
import json
import sys
from types import SimpleNamespace

import pytest

from worker.meet_media.companion_media_runtime import main


def invoke(monkeypatch, requests):
    calls = []
    data = b"\x00\x00\x00\x10ftypisom0000"

    def clip(repeat):
        return {"mp4": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest(),
                "frames": 2, "repeatMode": repeat, "originKind": "generated", "classification": "synthetic"}

    monkeypatch.setitem(sys.modules, "worker.meet_media.companion", SimpleNamespace(
        avatar_idle=lambda state: calls.append(("idle", state)) or clip("loop"),
        lipsync_client=lambda: None))

    class Publisher:
        def __init__(self, *_args, **_kwargs):
            pass

        def prepare(self, pcm):
            calls.append(("speaking", pcm))
            return None, [clip("hold_last")]

    monkeypatch.setitem(sys.modules, "worker.meet_media.companion_media", SimpleNamespace(
        AvatarPorts=lambda *_args: None, SpeechAvatarPublisher=Publisher))
    incoming = b"".join(json.dumps(request).encode() + b"\n" for request in requests)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(incoming)))
    monkeypatch.setattr(sys, "stdout", output)
    return main(), output.getvalue(), calls


def test_real_entry_prepares_only_closed_idle_and_speaking_commands(monkeypatch):
    pcm = b"\x00\x01"
    code, output, calls = invoke(monkeypatch, [{"kind": "idle", "value": "thinking"},
        {"kind": "speaking", "value": base64.b64encode(pcm).decode()}])
    assert code == 0 and calls == [("idle", "thinking"), ("speaking", pcm)]
    assert [json.loads(line)["kind"] for line in output.splitlines()] == ["idle", "speaking"]


@pytest.mark.parametrize("payload", [
    {"kind": "delegate", "value": "foreign"}, {"kind": "idle", "value": "speaking"},
    {"kind": "idle", "value": "idle", "policy": True}, {"kind": "speaking", "value": "invalid"},
    {"kind": "speaking", "value": "AA=="},
])
def test_invalid_requests_execute_nothing_and_emit_no_payload(monkeypatch, payload):
    code, output, calls = invoke(monkeypatch, [payload])
    assert code == 2 and output == "" and calls == []
