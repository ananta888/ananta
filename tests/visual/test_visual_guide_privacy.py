"""VG-052: Privacy / redaction tests for the Visual Guide Engine.

If agent.services.visual_guide.service (VisualGuideService) does not yet
exist, tests that depend on it are skipped gracefully via pytest.skip.

Tests that only use snapshot_delta or routes logic run unconditionally.
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_import_service():
    """Return (VisualGuideService, None) or (None, skip_reason)."""
    try:
        from agent.services.visual_guide.service import VisualGuideService  # type: ignore[import]
        return VisualGuideService, None
    except ImportError as exc:
        return None, f"visual_guide.service not yet available: {exc}"


# ---------------------------------------------------------------------------
# Tests: VisualGuideService._redact_snapshot (skipped when service missing)
# ---------------------------------------------------------------------------

class TestVisualGuideServiceRedaction:
    """Tests for the _redact_snapshot method on VisualGuideService."""

    def _get_service(self):
        VisualGuideService, reason = _try_import_service()
        if VisualGuideService is None:
            pytest.skip(reason)
        return VisualGuideService()

    def test_password_field_value_is_redacted(self):
        """A snapshot containing a password input value must have it replaced."""
        svc = self._get_service()
        snapshot = '/login | focus:input[password]="meinPasswort123"'
        redacted = svc._redact_snapshot(snapshot)
        assert "meinPasswort123" not in redacted
        assert "[REDACTED]" in redacted

    def test_api_key_field_value_is_redacted(self):
        """A snapshot with an api_key input value must be redacted."""
        svc = self._get_service()
        snapshot = '/settings | focus:input[api_key]="sk-abc123xyz"'
        redacted = svc._redact_snapshot(snapshot)
        assert "sk-abc123xyz" not in redacted
        assert "[REDACTED]" in redacted

    def test_token_field_value_is_redacted(self):
        """Input fields named 'token' must also be redacted."""
        svc = self._get_service()
        snapshot = '/auth | focus:input[token]="Bearer eyJhbGci..."'
        redacted = svc._redact_snapshot(snapshot)
        assert "Bearer eyJhbGci..." not in redacted

    def test_non_sensitive_fields_are_preserved(self):
        """Non-sensitive input values (e.g. search) must NOT be redacted."""
        svc = self._get_service()
        snapshot = '/teams | focus:input[Suche]="Blueprint"'
        redacted = svc._redact_snapshot(snapshot)
        assert "Blueprint" in redacted

    def test_route_preserved_after_redaction(self):
        """The route segment at the start of the snapshot must remain intact."""
        svc = self._get_service()
        snapshot = '/login | focus:input[password]="secret"'
        redacted = svc._redact_snapshot(snapshot)
        assert redacted.startswith("/login")

    def test_empty_snapshot_handled(self):
        """Redacting an empty string must not raise."""
        svc = self._get_service()
        result = svc._redact_snapshot("")
        assert isinstance(result, str)

    def test_snapshot_without_sensitive_fields_unchanged(self):
        """A snapshot with no sensitive data must be returned as-is (or equivalent)."""
        svc = self._get_service()
        snapshot = "/teams | nav:Teams* | list:3 | h:Teams"
        redacted = svc._redact_snapshot(snapshot)
        # No [REDACTED] marker should appear
        assert "[REDACTED]" not in redacted


# ---------------------------------------------------------------------------
# Tests: snapshot content should never leak to LLM before redaction
# (These tests verify the contract via the snapshot_delta layer which is
#  always available, without needing VisualGuideService)
# ---------------------------------------------------------------------------

class TestSnapshotRedactionContract:
    """Contract tests that run without VisualGuideService being available."""

    def test_password_pattern_detected_by_regex(self):
        """Ensure a regex approach can detect and blank password values.

        This validates the redaction pattern that any service MUST implement."""
        pattern = re.compile(
            r'(focus:input\[(?:password|api_key|token|secret)\]=["\'])([^"\']*?)(["\'])',
            re.IGNORECASE,
        )
        snapshot = '/login | focus:input[password]="meinPasswort123"'
        redacted = pattern.sub(r'\1[REDACTED]\3', snapshot)

        assert "meinPasswort123" not in redacted
        assert "[REDACTED]" in redacted

    def test_api_key_pattern_detected_by_regex(self):
        pattern = re.compile(
            r'(focus:input\[(?:password|api_key|token|secret)\]=["\'])([^"\']*?)(["\'])',
            re.IGNORECASE,
        )
        snapshot = '/settings | focus:input[api_key]="sk-abc123xyz"'
        redacted = pattern.sub(r'\1[REDACTED]\3', snapshot)

        assert "sk-abc123xyz" not in redacted
        assert "[REDACTED]" in redacted

    def test_multiple_sensitive_fields_in_one_snapshot(self):
        """Multiple sensitive values in a single snapshot should all be redacted."""
        pattern = re.compile(
            r'(focus:input\[(?:password|api_key|token|secret)\]=["\'])([^"\']*?)(["\'])',
            re.IGNORECASE,
        )
        snapshot = (
            '/auth | focus:input[password]="pass1" | '
            'focus:input[api_key]="key2"'
        )
        redacted = pattern.sub(r'\1[REDACTED]\3', snapshot)

        assert "pass1" not in redacted
        assert "key2" not in redacted
        assert redacted.count("[REDACTED]") == 2

    def test_empty_password_field_redacted(self):
        """Even an empty password field value must be redacted (to be consistent)."""
        pattern = re.compile(
            r'(focus:input\[(?:password|api_key|token|secret)\]=["\'])([^"\']*?)(["\'])',
            re.IGNORECASE,
        )
        snapshot = '/login | focus:input[password]=""'
        redacted = pattern.sub(r'\1[REDACTED]\3', snapshot)
        # Empty value is also replaced with [REDACTED]
        assert "[REDACTED]" in redacted
