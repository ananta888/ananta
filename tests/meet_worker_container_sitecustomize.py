"""Private container-only certificate seam; never packaged into the Worker image.

Python loads this as /test/sitecustomize.py in the test-owned server and its
delegated child. Only the ephemeral fixture certificate pin is added; sandbox,
capture policy, actual Worker runtime and signed Hub callbacks stay unchanged.
The separately opted-in browser profile substitutes only the public document
fetch port with static synthetic HTML; it is never public network evidence.
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import BrowserType

from worker.meet_media.dialog_chat import DialogChatPump

_pin = os.environ.get("MEET_TEST_BROWSER_SPKI", "")
if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", _pin):
    raise RuntimeError("test_worker_certificate_pin_required")
_native_launch = BrowserType.launch
_launch_codes = frozenset({"timeout", "sandbox", "resource", "executable", "crash", "unknown"})
_failure_codes = frozenset(
    {
        "meet_dialog_control_state_stale",
        "meet_dialog_control_request_stale",
        "meet_dialog_control_closed",
        "meet_dialog_hub_revoked_or_unavailable",
        "meet_machine_navigation_denied",
        "meet_speaker_permit_changed",
        "meet_dialog_speech_authority_changed",
        "meet_dialog_speech_state_stale",
        "meet_dialog_speech_input_revoked",
        "meet_speech_setup_timeout",
        "meet_speech_setup_failed",
    }
) | frozenset(
    "meet_dialog_session_changed meet_dialog_" + reason
    for reason in (
        "membership_lost",
        "lease_shape_changed",
        "lease_identity_changed",
        "lease_generation_changed",
        "lease_expiry_changed",
        "lease_lifetime_changed",
        "room_changed",
        "control_regressed",
    )
)
_failure_codes |= frozenset("test_worker_browser_launch_" + code for code in _launch_codes)


def _launch_failure(error):
    if type(error).__name__ == "TimeoutError":
        return "timeout"
    if type(error).__name__ not in {"Error", "TargetClosedError"}:
        return "unknown"
    message = getattr(error, "message", "")
    text = message[:32768] if isinstance(message, str) else ""
    for code, pattern in (
        ("sandbox", r"No usable sandbox|Failed to move to new namespace|Operation not permitted.*namespace"),
        ("resource", r"Resource temporarily unavailable|Cannot allocate memory|No space left on device"),
        ("executable", r"Executable doesn't exist|browser executable.*not found"),
        ("crash", r"Received signal 11|SIGSEGV"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            return code
    return "unknown"


def _fixture_launch(self, *args, **kwargs):
    expected = {"headless": True, "chromium_sandbox": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
    if args or kwargs != expected:
        raise RuntimeError("test_worker_browser_contract_changed")
    try:
        return _native_launch(
            self, **(kwargs | {"args": kwargs["args"] + ["--ignore-certificate-errors-spki-list=" + _pin]})
        )
    except Exception as error:
        raise ValueError("test_worker_browser_launch_" + _launch_failure(error)) from None


BrowserType.launch = _fixture_launch

if os.environ.get("MEET_TEST_FORCE_RELAY_URL"):
    from meet_worker_relay_context import install_relay_context
    from playwright.sync_api import Browser

    install_relay_context(Browser, os.environ["MEET_TEST_FORCE_RELAY_URL"], "/test/forced-relay.js")

_native_chat_update = DialogChatPump.update


def _fixture_chat_update(self, receipt, control):
    result = _native_chat_update(self, receipt, control)
    try:
        Path("/state/dialog-chat-ready.json").write_text(
            json.dumps(
                {
                    "open": self.opened is not None,
                    "control_revision": control["revision"],
                    "receive_revision": receipt["receiveRevision"],
                }
            )
        )
    except OSError:
        pass
    return result


DialogChatPump.update = _fixture_chat_update

_trace_functions = {
    "/app/worker/meet_media/dialog_runtime.py": frozenset({"run", "_run_joined"}),
    "/app/worker/meet_media/dialog_speech_output.py": frozenset({"accept", "require_current", "tick"}),
    "/app/worker/meet_media/speaker_permit.py": frozenset({"accept", "deadline"}),
}


def _runtime_trace(frame, event, arg):
    """Test-only closed exception projection, never locals, messages or tracebacks."""
    if event == "call":
        if frame.f_code.co_name not in _trace_functions.get(frame.f_code.co_filename, ()):
            return None
        frame.f_trace_lines = False
        return _runtime_trace
    if event == "exception":
        _, error, _ = arg
        code = str(error) if isinstance(error, ValueError) else ""
        record = {
            "line": frame.f_lineno,
            "kind": type(error).__name__
            if type(error).__name__ in {"ValueError", "Error", "TimeoutError"}
            else "other",
            "code": code if code in _failure_codes else "redacted",
        }
        try:
            Path("/state/dialog-diagnostic.json").write_text(json.dumps(record))
        except OSError:
            pass  # Diagnostic storage must not replace the actual Worker failure.
    return _runtime_trace


sys.settrace(_runtime_trace)

if os.environ.get("MEET_TEST_CONTROL_DIAGNOSTICS", "0") == "1":
    from types import SimpleNamespace

    from meet_dialog_control_observer import DialogControlObserver
    from meet_dialog_rpc_observer import DialogRpcObserver

    from worker.meet_media.dialog_control_exchange import DialogControlExchange

    _setter = SimpleNamespace(setattr=setattr)
    _controls = DialogControlObserver(_setter)
    _rpcs = DialogRpcObserver(_setter)
    _observed_poll = DialogControlExchange.poll

    def _control_failure(exchange, **kwargs):
        try:
            return _observed_poll(exchange, **kwargs)
        except Exception:
            try:
                Path("/state/dialog-control-failure.json").write_text(
                    json.dumps(
                        {
                            "control": _controls.report(),
                            "rpc": _rpcs.report()[-4:],
                            "history": _controls.history()[-4:],
                        }
                    )
                )
            except OSError:
                pass
            raise

    DialogControlExchange.poll = _control_failure


if os.environ.get("MEET_TEST_PUBLIC_DOCUMENT", "0") == "1":
    import time

    from ananta_contracts.browser_public_fetch import validate_fetch_request
    from worker.meet_media.browser_public_fetch_process import PublicDocumentFetch
    from worker.meet_media.dialog_browser_screen import DialogBrowserScreen

    def _synthetic_public_fetch(self, request, *, deadline):
        value = validate_fetch_request(request)
        self.require_current()
        if value["url"] != "https://example.com/docs" or time.monotonic() >= deadline:
            raise ValueError("test_browser_document_request_invalid")
        return "<html><body><h1>Synthetic packaged public document</h1><p>Isolated browser Task.</p></body></html>"

    PublicDocumentFetch.fetch = _synthetic_public_fetch
    _native_browser_update = DialogBrowserScreen.update

    def _fixture_browser_update(self, *args, **kwargs):
        result = _native_browser_update(self, *args, **kwargs)
        try:
            Path("/state/dialog-browser-ready.json").write_text(
                json.dumps(
                    {
                        "revision": self.revision,
                        "mode": self.mode,
                        "loaded": self.workspace is not None,
                        "failed": self.failed,
                    }
                )
            )
        except OSError:
            pass
        return result

    DialogBrowserScreen.update = _fixture_browser_update
