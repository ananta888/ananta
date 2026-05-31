from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.webrtc_audit import (
    EVENT_ERROR,
    WebRtcAuditEvent,
    WebRtcAuditLog,
)


class TestWebRtcAuditLog(unittest.TestCase):
    def test_token_and_ip_values_are_scrubbed(self):
        log = WebRtcAuditLog()
        log.emit(WebRtcAuditEvent(
            event_type=EVENT_ERROR,
            session_id="session-192.168.1.10",
            peer_id_hash="eyJabc.def.ghi",
            error_category="failed from 10.0.0.5",
        ))
        rendered = " ".join(str(event) for event in log.recent())
        self.assertNotIn("192.168.1.10", rendered)
        self.assertNotIn("10.0.0.5", rendered)
        self.assertNotIn("eyJabc.def.ghi", rendered)
        self.assertIn("[redacted_ip]", rendered)
        self.assertIn("[REDACTED_TOKEN]", rendered)

    def test_ring_buffer_keeps_recent_entries(self):
        log = WebRtcAuditLog(max_entries=2)
        log.emit(WebRtcAuditEvent(event_type=EVENT_ERROR, session_id="one"))
        log.emit(WebRtcAuditEvent(event_type=EVENT_ERROR, session_id="two"))
        log.emit(WebRtcAuditEvent(event_type=EVENT_ERROR, session_id="three"))
        self.assertEqual([event.session_id for event in log.recent(10)], ["two", "three"])


if __name__ == "__main__":
    unittest.main()
