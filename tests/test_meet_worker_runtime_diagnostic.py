"""The private container seam cannot export arbitrary exception contents."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.timeout(45)


def load_seam(monkeypatch, *, control=False):
    browser = type("Browser", (), {"launch": Mock()})
    chat = type("Chat", (), {"update": Mock()})
    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", SimpleNamespace(BrowserType=browser))
    monkeypatch.setitem(
        __import__("sys").modules, "worker.meet_media.dialog_chat", SimpleNamespace(DialogChatPump=chat)
    )
    monkeypatch.setattr("sys.settrace", Mock())
    monkeypatch.setenv("MEET_TEST_BROWSER_SPKI", "a" * 43 + "=")
    monkeypatch.setenv("MEET_TEST_CONTROL_DIAGNOSTICS", "1" if control else "0")
    if control:
        for name, symbol in [
            ("meet_dialog_control_observer", "DialogControlObserver"),
            ("meet_dialog_rpc_observer", "DialogRpcObserver"),
        ]:
            observer = Mock()
            observer.report.return_value = {} if "control" in name else []
            observer.history.return_value = []
            monkeypatch.setitem(
                __import__("sys").modules, name, SimpleNamespace(**{symbol: Mock(return_value=observer)})
            )
        exchange = type("Exchange", (), {"poll": Mock()})
        monkeypatch.setitem(
            __import__("sys").modules,
            "worker.meet_media.dialog_control_exchange",
            SimpleNamespace(DialogControlExchange=exchange),
        )
    written = Mock()
    monkeypatch.setattr(Path, "write_text", written)
    seam = runpy.run_path(str(Path(__file__).with_name("meet_worker_container_sitecustomize.py")))
    return seam, written


@pytest.mark.parametrize("storage_failed", [False, True])
def test_optional_control_timing_seam_preserves_native_return_and_exception(monkeypatch, storage_failed):
    seam, written = load_seam(monkeypatch, control=True)
    native, observe = seam["_observed_poll"], seam["_control_failure"]
    exchange, value = object(), object()
    native.return_value = value
    assert observe(exchange, refresh_marker=2) is value
    native.assert_called_once_with(exchange, refresh_marker=2)
    written.assert_not_called()
    failure = ValueError("private-do-not-serialize")
    native.side_effect = failure
    if storage_failed:
        written.side_effect = OSError("private-storage")
    with pytest.raises(ValueError) as caught:
        observe(exchange)
    assert caught.value is failure
    assert json.loads(written.call_args.args[0]) == {"control": {}, "rpc": [], "history": []}


@pytest.mark.parametrize(
    "message,expected",
    [
        ("meet_dialog_control_state_stale", "meet_dialog_control_state_stale"),
        (
            "meet_dialog_session_changed meet_dialog_lease_generation_changed",
            "meet_dialog_session_changed meet_dialog_lease_generation_changed",
        ),
        ("meet_secret_canary", "redacted"),
        ("Bearer secret-canary", "redacted"),
    ],
)
def test_only_closed_failure_codes_and_line_are_written(monkeypatch, message, expected):
    seam, written = load_seam(monkeypatch)
    frame = SimpleNamespace(f_lineno=93)
    seam["_runtime_trace"](frame, "exception", (ValueError, ValueError(message), None))
    assert json.loads(written.call_args.args[0]) == {"line": 93, "kind": "ValueError", "code": expected}


def test_trace_is_limited_to_the_delegated_runtime_function(monkeypatch):
    seam, written = load_seam(monkeypatch)
    trace = seam["_runtime_trace"]
    frame = SimpleNamespace(
        f_code=SimpleNamespace(co_name="run", co_filename="/app/worker/meet_media/dialog_runtime.py")
    )
    assert trace(frame, "call", None) is trace
    assert frame.f_trace_lines is False
    frame.f_code.co_filename = "/app/worker/meet_media/server.py"
    assert trace(frame, "call", None) is None
    written.assert_not_called()


@pytest.mark.parametrize(
    "filename,name",
    [
        ("dialog_runtime.py", "_run_joined"),
        ("dialog_speech_output.py", "accept"),
        ("dialog_speech_output.py", "require_current"),
        ("speaker_permit.py", "deadline"),
    ],
)
def test_private_speech_diagnostic_observes_only_fixed_delegated_functions(monkeypatch, filename, name):
    seam, written = load_seam(monkeypatch)
    trace = seam["_runtime_trace"]
    frame = SimpleNamespace(f_code=SimpleNamespace(co_name=name, co_filename="/app/worker/meet_media/" + filename))
    assert trace(frame, "call", None) is trace and frame.f_trace_lines is False
    frame.f_code.co_name = "unrelated"
    assert trace(frame, "call", None) is None
    written.assert_not_called()


def test_diagnostic_storage_failure_cannot_replace_worker_failure(monkeypatch):
    seam, written = load_seam(monkeypatch)
    written.side_effect = OSError("synthetic-storage-error")
    seam["_runtime_trace"](SimpleNamespace(f_lineno=93), "exception", (ValueError, ValueError("private"), None))


def test_chat_readiness_contains_only_actual_open_state_and_numeric_revisions(monkeypatch):
    seam, written = load_seam(monkeypatch)
    receiver = SimpleNamespace(opened={"private": "secret-canary"})
    receipt, control = {"receiveRevision": 7, "grants": "secret-canary"}, {"revision": 3}
    native = seam["_native_chat_update"]
    assert seam["_fixture_chat_update"](receiver, receipt, control) is native.return_value
    native.assert_called_once_with(receiver, receipt, control)
    assert json.loads(written.call_args.args[0]) == {"open": True, "control_revision": 3, "receive_revision": 7}
    native.side_effect = ValueError("synthetic-native-denial")
    written.reset_mock()
    with pytest.raises(ValueError, match="native-denial"):
        seam["_fixture_chat_update"](receiver, receipt, control)
    written.assert_not_called()


@pytest.mark.parametrize(
    "message,code",
    [
        ("PRIVATE Resource temporarily unavailable", "resource"),
        ("PRIVATE No usable sandbox", "sandbox"),
        ("PRIVATE Executable doesn't exist", "executable"),
        ("PRIVATE Received signal 11", "crash"),
        ("PRIVATE unknown", "unknown"),
    ],
)
def test_launch_diagnostic_only_exports_closed_reason_and_never_retries(monkeypatch, message, code):
    seam, _ = load_seam(monkeypatch)
    error = type("TargetClosedError", (Exception,), {})(message)
    error.message = message
    seam["_native_launch"].side_effect = error
    with pytest.raises(ValueError, match="^test_worker_browser_launch_" + code + "$"):
        seam["_fixture_launch"](
            Mock(), headless=True, chromium_sandbox=True, args=["--autoplay-policy=no-user-gesture-required"]
        )
    seam["_native_launch"].assert_called_once()
