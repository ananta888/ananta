"""Signed exact replies, bounded framing and one-shot optional transport."""

import io
import json
import time
from email.message import Message
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_dialog_diagnostics import request_signature, response_signature
from tests.test_meet_dialog_diagnostics_contract import observation
from worker.meet_media.dialog_diagnostics_client import report_terminal

URL = "https://hub.test/api/meet/v1/internal/dialog"
KEY = b"synthetic-diagnostics-key"
IDS = {"task_id": "task", "lease_id": "lease", "runtime_id": "runtime"}


def opener(failure=None):
    def open(request, timeout):
        assert request.full_url == URL + "/diagnostics"
        assert request.get_method() == "POST" and 0 < timeout <= 1
        assert request.get_header("X-ananta-dialog-diagnostics-signature") == request_signature(KEY, request.data)
        value = {
            "schema": "ananta.meet-dialog-diagnostics-accepted.v1",
            "nonce": json.loads(request.data)["nonce"],
            "accepted": True,
        }
        if failure == "nonce":
            value["nonce"] = "b" * 32
        elif failure == "type":
            value["accepted"] = 1
        elif failure == "extra":
            value["status"] = "completed"
        raw = json.dumps(value).encode()
        response = io.BytesIO(raw[:-1] if failure == "truncated" else raw)
        response.headers = Message()
        if failure != "missing-length":
            response.headers["Content-Length"] = "9999" if failure == "oversized" else str(len(raw))
        if failure == "duplicate-length":
            response.headers["Content-Length"] = str(len(raw))
        if failure == "transfer":
            response.headers["Transfer-Encoding"] = "chunked"
        response.headers["X-Ananta-Dialog-Diagnostics-Signature"] = (
            "bad" if failure == "signature" else response_signature(KEY, request.data, raw)
        )
        return response

    return Mock(open=Mock(side_effect=OSError("private") if failure == "transport" else open))


def test_only_exact_signed_reply_is_a_receipt_not_a_task_completion():
    client = opener()
    assert report_terminal(URL, KEY, IDS, observation(), time.monotonic() + 20, opener=client)
    client.open.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        "signature",
        "nonce",
        "type",
        "extra",
        "truncated",
        "oversized",
        "missing-length",
        "duplicate-length",
        "transfer",
        "transport",
    ],
)
def test_optional_reporting_failure_never_retries_or_accepts_bad_receipt(failure):
    client = opener(failure)
    assert not report_terminal(URL, KEY, IDS, observation(), time.monotonic() + 20, opener=client)
    client.open.assert_called_once()


@pytest.mark.parametrize("deadline", [None, True, float("nan"), float("inf"), -10])
def test_invalid_or_elapsed_original_cleanup_deadline_never_opens(deadline):
    client = Mock()
    assert not report_terminal(URL, KEY, IDS, observation(), deadline, opener=client)
    client.open.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "https://hub.test/other",
        URL + "?token=private",
        URL + "#fragment",
        "https://user:secret@hub.test/api/meet/v1/internal/dialog",
    ],
)
def test_report_cannot_change_configured_hub_path_or_add_credentials(url):
    client = Mock()
    assert not report_terminal(url, KEY, IDS, observation(), time.monotonic() + 20, opener=client)
    client.open.assert_not_called()
