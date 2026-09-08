"""Private container-only certificate seam; never packaged into the Worker image.

Python loads this as /test/sitecustomize.py in the test-owned server and its
delegated child. Only the ephemeral fixture certificate pin is added; sandbox,
capture policy, actual Worker runtime and signed Hub callbacks stay unchanged.
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import BrowserType

_pin = os.environ.get("MEET_TEST_BROWSER_SPKI", "")
if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", _pin):
    raise RuntimeError("test_worker_certificate_pin_required")
_native_launch = BrowserType.launch
_failure_codes = frozenset(
    {
        "meet_dialog_control_state_stale",
        "meet_dialog_control_request_stale",
        "meet_dialog_control_closed",
        "meet_dialog_hub_revoked_or_unavailable",
        "meet_machine_navigation_denied",
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


def _fixture_launch(self, *args, **kwargs):
    expected = {"headless": True, "chromium_sandbox": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
    if args or kwargs != expected:
        raise RuntimeError("test_worker_browser_contract_changed")
    return _native_launch(
        self, **(kwargs | {"args": kwargs["args"] + ["--ignore-certificate-errors-spki-list=" + _pin]})
    )


BrowserType.launch = _fixture_launch


def _runtime_trace(frame, event, arg):
    """Test-only closed exception projection, never locals, messages or tracebacks."""
    if event == "call":
        if frame.f_code.co_name != "run" or frame.f_code.co_filename != "/app/worker/meet_media/dialog_runtime.py":
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
