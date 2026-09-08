"""HTTP framing checks preserve exact-length reads without copying a reader."""

import time
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_worker_response_body import read_worker_body


def response(lengths=(), *, chunks=(b"{}", b"")):
    headers = Message()
    for value in lengths:
        headers["Content-Length"] = value
    return SimpleNamespace(headers=headers, read1=Mock(side_effect=chunks))


@pytest.mark.parametrize("length", ["2", "002", " \t2\t "])
def test_exact_framing_uses_existing_reader_and_never_waits_for_eof(length):
    stream = response([length], chunks=[b"{}"])
    assert read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1) == b"{}"
    stream.read1.assert_called_once_with(2)


def test_missing_length_retains_bounded_eof_compatibility():
    stream = response()
    assert read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1) == b"{}"
    assert stream.read1.call_count == 2


@pytest.mark.parametrize("length", ["", "-1", "+2", "2, 2", "٢", "1.0", "9" * 10000])
def test_malformed_lengths_fail_before_reading(length):
    stream = response([length])
    with pytest.raises(ValueError):
        read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1)
    stream.read1.assert_not_called()


@pytest.mark.parametrize("lengths", [["2", "2"], ["2", "3"]])
def test_duplicate_lengths_never_choose_an_ambiguous_first_value(lengths):
    stream = response(lengths)
    with pytest.raises(ValueError):
        read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1)
    stream.read1.assert_not_called()


def test_announced_oversize_preserves_existing_media_overflow_code_without_reading():
    stream = response(["11"])
    with pytest.raises(ValueError, match="^persona_http_body_too_large$"):
        read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1)
    stream.read1.assert_not_called()


def test_premature_eof_cannot_turn_a_partial_signed_body_into_a_complete_response():
    stream = response(["3"])
    with pytest.raises(ValueError, match="^persona_http_body_incomplete_or_expired$"):
        read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1)
    assert stream.read1.call_count == 2


def test_transfer_encoding_rejection_precedes_length_parsing_and_reading():
    stream = response(["2"])
    stream.headers["Transfer-Encoding"] = "chunked"
    with pytest.raises(ValueError, match="^meet_worker_transfer_encoding_unsupported$"):
        read_worker_body(stream, maximum=10, deadline=time.monotonic() + 1)
    stream.read1.assert_not_called()
