"""The real helper entry validates its closed protocol without loading a model."""

import base64
import io
import json
import sys
from types import SimpleNamespace

import pytest

from worker.meet_media.companion_execution_runtime import main


def invoke(monkeypatch, requests, *, reply="synthetic answer"):
    calls = []
    monkeypatch.setitem(sys.modules, "worker.meet_media.companion", SimpleNamespace(
        generate_reply=lambda text: calls.append(("answer", text)) or reply,
        synthesize_pcm=lambda text: calls.append(("speech", text)) or b"\x00\x01",
    ))
    incoming = b"".join(json.dumps(value).encode() + b"\n" for value in requests)
    outgoing = io.StringIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(incoming)))
    monkeypatch.setattr(sys, "stdout", outgoing)
    code = main()
    return code, outgoing.getvalue(), calls


def test_real_entry_executes_only_answer_and_speech_with_closed_receipts(monkeypatch):
    code, output, calls = invoke(monkeypatch, [{"kind": "answer", "text": "synthetic question"},
                                              {"kind": "speech", "text": "synthetic answer"}])
    records = [json.loads(line) for line in output.splitlines()]
    assert code == 0 and calls == [("answer", "synthetic question"), ("speech", "synthetic answer")]
    assert records[0] == {"kind": "answer", "result": "synthetic answer"}
    assert base64.b64decode(records[1]["result"], validate=True) == b"\x00\x01"


@pytest.mark.parametrize("payload", [
    {"kind": "delegate", "text": "synthetic"},
    {"kind": "answer", "text": "synthetic", "scope": "foreign"},
    {"kind": "speech", "text": "x" * 451},
    {"kind": "speech", "text": "synthetic", "model_execution": {}},
    {"kind": "answer", "text": "synthetic", "model_execution": {"backend": "shell"}},
])
def test_invalid_requests_execute_nothing_and_emit_no_content(monkeypatch, payload):
    code, output, calls = invoke(monkeypatch, [payload])
    assert code == 2 and output == "" and calls == []


def test_oversized_answer_is_not_emitted(monkeypatch):
    code, output, _calls = invoke(monkeypatch, [{"kind": "answer", "text": "synthetic"}], reply="x" * 451)
    assert code == 2 and output == ""
