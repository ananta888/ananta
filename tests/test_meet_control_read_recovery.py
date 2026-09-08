"""Closed error classification and bounded clocks/futures; no network or human."""

import errno
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from tests.test_meet_dialog_control_exchange import ready, setup
from worker.meet_media.dialog_control_transport import ControlReadUnavailable, transient_control_read


@pytest.mark.parametrize(
    "error",
    [HTTPError("https://synthetic.invalid", status, "private reason", {}, None) for status in (502, 503, 504)]
    + [
        URLError(socket.gaierror(socket.EAI_AGAIN, "private DNS")),
        URLError(ConnectionRefusedError(errno.ECONNREFUSED, "private address")),
        ConnectionResetError(errno.ECONNRESET, "private endpoint"),
        TimeoutError("private timeout"),
        OSError(errno.EHOSTUNREACH, "private host"),
        OSError(errno.ENETUNREACH, "private network"),
    ],
)
def test_only_exchange_can_classify_closed_transient_transport_errors(error):
    assert transient_control_read("exchange", error)
    for action in ("chat", "audio", "transcript", "finish", "browser_finish", "start", None):
        assert not transient_control_read(action, error)
    assert str(ControlReadUnavailable()) == "meet_dialog_control_transport_unavailable"


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://synthetic.invalid", status, "private reason", {}, None)
        for status in (301, 400, 401, 403, 404, 409, 429, 500, True, "503")
    ]
    + [
        URLError(ssl.SSLCertVerificationError("private certificate")),
        ssl.SSLError("private TLS"),
        URLError(socket.gaierror(socket.EAI_NONAME, "private DNS")),
        URLError("private opaque reason"),
        OSError(errno.EACCES, "private permission"),
        ValueError("signature invalid"),
        ValueError("schema invalid"),
        RuntimeError("unknown error"),
    ],
)
def test_policy_tls_protocol_and_unknown_errors_remain_terminal(error):
    assert not transient_control_read("exchange", error)


def transient(exchange):
    exchange.pending.set_exception(ControlReadUnavailable())
    assert exchange.poll() is None


@pytest.mark.parametrize("established", [False, True])
def test_retry_requires_backoff_and_new_valid_state_without_refreshing_old_authority(established):
    clock, pool, exchange = setup()
    exchange.poll()
    if established:
        ready(exchange, {"version": 1})
        clock.now = 101.0
        exchange.poll()
    before = exchange.fresh_until
    transient(exchange)
    assert exchange.pending is None and exchange.fresh_until == before
    expected_calls = 2 if established else 1
    clock.now += 0.05
    exchange.refresh()  # A caller cannot skip the retry backoff.
    exchange.poll(refresh_marker="new-scope")
    assert pool.submit.call_count == expected_calls
    clock.now += 0.05
    exchange.poll()
    assert pool.submit.call_count == expected_calls + 1
    assert exchange.fresh_until == before
    ready(exchange, {"version": 2})
    assert exchange.fresh_until == clock.now + 2.5
    assert exchange.retry.attempts == 0 and exchange.retry.deadline is None
    exchange.close()


def test_only_two_retries_and_exhaustion_cannot_be_revived_by_refresh():
    clock, pool, exchange = setup()
    exchange.poll()
    transient(exchange)
    assert exchange.retry.deadline == 102.5
    clock.now = 100.1
    exchange.poll()
    transient(exchange)
    assert exchange.retry.deadline == 102.5
    clock.now = 100.2
    exchange.poll()
    assert pool.submit.call_count == 2
    clock.now = 100.31
    exchange.poll()
    exchange.pending.set_exception(ControlReadUnavailable())
    with pytest.raises(ValueError, match="recovery_exhausted"):
        exchange.poll()
    exchange.refresh()
    with pytest.raises(ValueError, match="recovery_exhausted"):
        exchange.poll()
    assert pool.submit.call_count == 3 and exchange.fresh_until is None
    exchange.close()


@pytest.mark.parametrize("established", [False, True])
def test_success_arriving_after_original_recovery_window_never_grants_freshness(established):
    clock, pool, exchange = setup()
    exchange.poll()
    if established:
        ready(exchange, {"version": 1})
        clock.now = 102.0
        exchange.poll()
    transient(exchange)
    assert exchange.retry.deadline == 102.5
    clock.now += 0.11
    exchange.poll()
    exchange.pending.set_result({"late": "valid-but-expired"})
    clock.now = 102.5
    with pytest.raises(ValueError, match="stale|recovery_exhausted"):
        exchange.poll()
    assert exchange.fresh_until == (102.5 if established else None)
    assert pool.submit.call_count == (3 if established else 2)
    exchange.close()


def test_obsolete_success_does_not_reset_retry_budget_or_freshness():
    clock, pool, exchange = setup()
    exchange.poll()
    transient(exchange)
    clock.now = 100.11
    exchange.poll()
    exchange.refresh()
    exchange.pending.set_result({"obsolete": True})
    assert exchange.poll() is None
    assert exchange.retry.attempts == 2 and exchange.retry.deadline == 102.5
    assert exchange.fresh_until is None and pool.submit.call_count == 2
    clock.now = 100.32
    exchange.poll()
    assert pool.submit.call_count == 3
    exchange.refresh()
    exchange.pending.set_result({"obsolete": "again"})
    with pytest.raises(ValueError, match="recovery_exhausted"):
        exchange.poll()
    assert pool.submit.call_count == 3 and exchange.fresh_until is None
    exchange.close()


def test_terminal_denial_during_recovery_and_close_never_schedule_another_read():
    clock, pool, exchange = setup()
    exchange.poll()
    transient(exchange)
    clock.now = 100.11
    exchange.poll()
    exchange.pending.set_exception(ValueError("policy-denied"))
    with pytest.raises(ValueError, match="policy-denied"):
        exchange.poll()
    exchange.close()
    with pytest.raises(ValueError, match="closed"):
        exchange.poll()
    assert pool.submit.call_count == 2


def test_no_retry_when_backoff_would_outlive_existing_authority():
    clock, pool, exchange = setup()
    exchange.poll()
    ready(exchange, {"version": 1})
    clock.now = 102.45
    exchange.poll()
    exchange.pending.set_exception(ControlReadUnavailable())
    with pytest.raises(ValueError, match="recovery_exhausted"):
        exchange.poll()
    assert exchange.fresh_until == 102.5 and pool.submit.call_count == 2
    exchange.close()
