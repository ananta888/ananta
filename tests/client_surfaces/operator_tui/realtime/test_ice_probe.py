from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.ice_probe import _parse_stun_url, _parse_turn_url, _redact_ip


class TestIceProbeHelpers(unittest.TestCase):
    def test_stun_url_defaults_to_3478(self):
        self.assertEqual(_parse_stun_url("stun:webrtc.ananta.de"), ("webrtc.ananta.de", 3478))

    def test_turn_url_strips_transport_query(self):
        self.assertEqual(
            _parse_turn_url("turn:webrtc.ananta.de:5349?transport=udp"),
            ("webrtc.ananta.de", 5349),
        )

    def test_error_ip_addresses_are_redacted(self):
        self.assertEqual(_redact_ip("connect 192.168.1.10 failed"), "connect [redacted] failed")


if __name__ == "__main__":
    unittest.main()
