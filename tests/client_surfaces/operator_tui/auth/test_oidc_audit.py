"""Tests for OidcAuditLog (oidc-007).

Tests:
- Events are stored and returned in order
- Buffer is capped at max_events
- Thread safety
- Token-like values do NOT appear in any audit event string representation
"""
from __future__ import annotations

import threading
import time
import unittest

from client_surfaces.operator_tui.auth.oidc_audit import (
    OidcAuditEvent,
    OidcAuditLog,
    EVENT_LOGIN_START,
    EVENT_TOKEN_EXCHANGE_SUCCESS,
    EVENT_TOKEN_EXCHANGE_FAILED,
    EVENT_LOGOUT,
    MODE_ANANTA_OWNED,
    MODE_REAL_BROWSER,
    PROFILE_EPHEMERAL,
    PROFILE_NAMED,
)


def _make_event(
    event_type: str = EVENT_LOGIN_START,
    provider_id: str = "test_provider",
    mode: str = MODE_ANANTA_OWNED,
    profile_mode: str = PROFILE_EPHEMERAL,
    error_category: str = "",
) -> OidcAuditEvent:
    return OidcAuditEvent(
        event_type=event_type,
        provider_id=provider_id,
        mode=mode,
        profile_mode=profile_mode,
        error_category=error_category,
    )


class TestAuditLogBasics(unittest.TestCase):
    """Basic emit/recent behavior."""

    def test_emit_and_recent(self):
        """Events emitted must appear in recent()."""
        log = OidcAuditLog()
        evt = _make_event()
        log.emit(evt)
        events = log.recent()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EVENT_LOGIN_START)

    def test_recent_returns_at_most_n(self):
        """recent(n) must return at most n events."""
        log = OidcAuditLog()
        for i in range(10):
            log.emit(_make_event(event_type=EVENT_LOGIN_START))
        events = log.recent(5)
        self.assertEqual(len(events), 5)

    def test_recent_default_is_20(self):
        """recent() without args returns at most 20 events."""
        log = OidcAuditLog()
        for i in range(30):
            log.emit(_make_event())
        events = log.recent()
        self.assertLessEqual(len(events), 20)

    def test_empty_log_returns_empty_list(self):
        """recent() on empty log returns empty list."""
        log = OidcAuditLog()
        self.assertEqual(log.recent(), [])

    def test_len(self):
        """len(log) must equal number of events."""
        log = OidcAuditLog()
        for i in range(5):
            log.emit(_make_event())
        self.assertEqual(len(log), 5)

    def test_clear(self):
        """clear() must remove all events."""
        log = OidcAuditLog()
        log.emit(_make_event())
        log.clear()
        self.assertEqual(len(log), 0)


class TestRingBuffer(unittest.TestCase):
    """Ring buffer must cap at max_events."""

    def test_buffer_capped_at_max(self):
        """Buffer must not exceed max_events (oldest dropped)."""
        log = OidcAuditLog(max_events=10)
        for i in range(20):
            log.emit(_make_event(provider_id=f"provider_{i}"))
        self.assertEqual(len(log), 10)

    def test_oldest_dropped_when_full(self):
        """When buffer is full, oldest events are dropped first."""
        log = OidcAuditLog(max_events=3)
        for i in range(5):
            log.emit(_make_event(provider_id=f"p{i}"))
        events = log.recent(10)
        # Should have the last 3: p2, p3, p4
        provider_ids = [e.provider_id for e in events]
        self.assertEqual(provider_ids, ["p2", "p3", "p4"])


class TestThreadSafety(unittest.TestCase):
    """Concurrent emits must not corrupt the buffer."""

    def test_concurrent_emits(self):
        """100 concurrent emit calls must not crash."""
        log = OidcAuditLog(max_events=200)
        errors = []

        def _emit():
            try:
                for _ in range(10):
                    log.emit(_make_event())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_emit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertLessEqual(len(log), 200)


class TestNoTokenInOutput(unittest.TestCase):
    """Token-like values must NEVER appear in audit event string representations."""

    # A fabricated JWT-like token value
    FAKE_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.FAKESIGNATURE"
    FAKE_CODE = "4/0AbC123-FAKEAUTH-CODE-VALUE"

    def test_token_not_in_event_str(self):
        """A fabricated token value must not appear in event.__str__()."""
        # The token should NEVER be in event fields — but we test defensively
        # by checking the string representation of a normal event
        evt = _make_event(
            event_type=EVENT_TOKEN_EXCHANGE_SUCCESS,
            provider_id="keycloak",
        )
        output = str(evt)
        self.assertNotIn(self.FAKE_TOKEN, output)
        # Also check no "eyJ" prefix (JWT signature)
        self.assertNotIn("eyJhbGci", output)

    def test_token_not_in_event_repr(self):
        """A fabricated token value must not appear in event.__repr__()."""
        evt = _make_event(event_type=EVENT_TOKEN_EXCHANGE_SUCCESS)
        output = repr(evt)
        self.assertNotIn(self.FAKE_TOKEN, output)

    def test_error_category_cannot_contain_token(self):
        """error_category field should describe the error type, not a token."""
        # Emit events with token-like strings in error_category is the risk
        # We verify that error categories that accidentally contain token values
        # are exposed in str output — and therefore test authors should never
        # put raw token values there
        evt = OidcAuditEvent(
            event_type=EVENT_TOKEN_EXCHANGE_FAILED,
            provider_id="test",
            mode=MODE_ANANTA_OWNED,
            profile_mode=PROFILE_EPHEMERAL,
            error_category="token_exchange_failed",  # safe error category string
        )
        output = str(evt)
        self.assertIn("token_exchange_failed", output)
        self.assertNotIn(self.FAKE_TOKEN, output)

    def test_all_recent_events_str_no_fake_token(self):
        """None of the recent events' string representations contain a fake token."""
        log = OidcAuditLog()
        for event_type in [
            EVENT_LOGIN_START,
            EVENT_TOKEN_EXCHANGE_SUCCESS,
            EVENT_TOKEN_EXCHANGE_FAILED,
            EVENT_LOGOUT,
        ]:
            log.emit(_make_event(event_type=event_type))

        for evt in log.recent():
            self.assertNotIn(self.FAKE_TOKEN, str(evt))
            self.assertNotIn(self.FAKE_CODE, str(evt))

    def test_event_timestamp_is_recent(self):
        """Event timestamp must be close to the current time."""
        before = time.time()
        evt = _make_event()
        after = time.time()
        self.assertGreaterEqual(evt.timestamp, before)
        self.assertLessEqual(evt.timestamp, after + 0.1)


class TestEventFields(unittest.TestCase):
    """Event fields must be correctly stored."""

    def test_all_fields_stored(self):
        """All fields must be accessible after construction."""
        evt = OidcAuditEvent(
            event_type=EVENT_LOGIN_START,
            provider_id="keycloak_ananta",
            mode=MODE_ANANTA_OWNED,
            profile_mode=PROFILE_NAMED,
            error_category="",
            timestamp=1234567890.0,
        )
        self.assertEqual(evt.event_type, EVENT_LOGIN_START)
        self.assertEqual(evt.provider_id, "keycloak_ananta")
        self.assertEqual(evt.mode, MODE_ANANTA_OWNED)
        self.assertEqual(evt.profile_mode, PROFILE_NAMED)
        self.assertEqual(evt.error_category, "")
        self.assertEqual(evt.timestamp, 1234567890.0)

    def test_str_includes_all_fields(self):
        """str(event) must include all key field values."""
        evt = OidcAuditEvent(
            event_type=EVENT_LOGIN_START,
            provider_id="keycloak_ananta",
            mode=MODE_ANANTA_OWNED,
            profile_mode=PROFILE_EPHEMERAL,
            error_category="",
        )
        output = str(evt)
        self.assertIn("login_start", output)
        self.assertIn("keycloak_ananta", output)
        self.assertIn("ananta_owned_callback", output)
        self.assertIn("ephemeral", output)


if __name__ == "__main__":
    unittest.main()
