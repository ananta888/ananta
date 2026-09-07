"""Typed subprocess failures and signed HTTP diagnostics never carry provider text."""

import io
import runpy
import subprocess
import sys
import threading
import urllib.error
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_media_failure_transport import worker_failure
from agent.services.meet_media_transport import HttpMediaWorker
from ananta_contracts.meet_media_failures import (
    CODES,
    MediaCapabilityError,
    failure_exit,
    failure_from_exit,
    failure_reply,
    failure_signature,
    verify_failure,
)
from tests.test_meet_media import turn
from worker.meet_media.contract import encode
from worker.meet_media.server import TurnExecutor, create_server

KEY = b"synthetic-failure-protocol-key-000"


@pytest.mark.parametrize("code", CODES)
def test_closed_typed_exit_codes_roundtrip_but_plain_error_text_does_not(code):
    assert failure_from_exit(failure_exit(MediaCapabilityError(code))) == code
    assert failure_exit(ValueError(code)) == 1


@pytest.mark.parametrize("code", [0, 1, -9, 79, 85, 137, True, "80"])
def test_unknown_process_exits_never_become_capability_claims(code):
    assert failure_from_exit(code) is None


def test_unknown_error_details_cannot_enter_the_typed_failure_contract():
    with pytest.raises(ValueError, match="code_invalid"):
        MediaCapabilityError("private-provider-diagnostic")


@pytest.mark.parametrize("typed", [False, True])
def test_actual_runtime_entrypoint_emits_only_safe_exit_and_generic_stderr(monkeypatch, capsys, typed):
    error = MediaCapabilityError(CODES[0]) if typed else ValueError("PRIVATE provider message")
    monkeypatch.setattr("worker.meet_media.llm.answer", Mock(side_effect=error))
    monkeypatch.setattr(sys, "stdin", io.StringIO(encode(turn()).decode()))
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("worker.meet_media.local_runtime", run_name="__main__")
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "meet_local_media_execution_failed\n"
    assert exited.value.code == (80 if typed else 1)


@pytest.mark.parametrize("change", ["request", "key", "signature", "body", "scope", "extra", "duplicate"])
def test_tampered_replayed_or_expanded_errors_are_rejected(change):
    request = encode(turn())
    reply = failure_reply(turn(), CODES[0])
    raw = encode(reply)
    signed = failure_signature(KEY, request, raw)
    key = KEY
    if change == "request":
        request = encode(turn() | {"text": "another task's private prompt"})
    elif change == "key":
        key = b"different-key"
    elif change == "signature":
        signed = "0" * 64
    elif change == "body":
        raw += b" "
    else:
        if change == "scope":
            reply["task_id"] = "foreign"
        elif change == "extra":
            reply["error"]["detail"] = "private-model-path"
        raw = encode(reply)
        if change == "duplicate":
            raw = raw.replace(b'"task_id":', b'"task_id":"duplicate", "task_id":')
        signed = failure_signature(KEY, request, raw)
    with pytest.raises(ValueError):
        verify_failure(key, request, raw, signed)


@pytest.mark.parametrize("invalid", ["unsigned", "replayed", "oversize", "legacy"])
def test_unverified_http_errors_keep_the_existing_generic_result_and_close_body(invalid):
    request = encode(turn())
    raw = encode(failure_reply(turn(), CODES[0]))
    headers = {"X-Ananta-Media-Failure-Signature": failure_signature(KEY, request, raw)}
    if invalid == "unsigned":
        headers.clear()
    elif invalid == "replayed":
        request = encode(turn() | {"lease_id": "new-lease"})
    elif invalid == "oversize":
        raw = b"x" * 1025
    else:
        raw = b'{"error":{"code":"private-provider-message"}}'
    stream = io.BytesIO(raw)
    response = urllib.error.HTTPError("http://worker.test/v1/turns", 503, "private", headers, stream)
    failure = worker_failure(response, key=KEY, request_body=request)
    assert str(failure) == "meet_worker_unavailable"
    assert stream.closed


@pytest.mark.parametrize("code", CODES)
def test_real_worker_http_to_hub_path_preserves_only_authenticated_capability_code(monkeypatch, code):
    executor = Mock()
    executor.execute.side_effect = MediaCapabilityError(code)
    server = create_server(("127.0.0.1", 0), KEY, executor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("agent.services.meet_media_transport.pin_private_container_address", lambda *_: "127.0.0.1")
    try:
        worker = HttpMediaWorker(f"http://worker.test:{server.server_address[1]}/v1/turns", KEY)
        with pytest.raises(MeetError) as error:
            worker.execute(turn())
        assert str(error.value) == code
        executor.execute.assert_called_once()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("exit_code", [80, 84, 1])
def test_real_child_exit_maps_without_reading_private_stderr(tmp_path, monkeypatch, exit_code):
    original = subprocess.Popen

    def child(_command, **kwargs):
        return original([sys.executable, "-c", f"import sys; sys.stdin.read(); sys.exit({exit_code})"], **kwargs)

    monkeypatch.setattr("worker.meet_media.server.subprocess.Popen", child)
    with pytest.raises(ValueError) as error:
        TurnExecutor(tmp_path / "leases.sqlite")._run(turn())
    assert isinstance(error.value, MediaCapabilityError) == (exit_code != 1)
    assert str(error.value) == (failure_from_exit(exit_code) or "meet_local_media_execution_failed")
