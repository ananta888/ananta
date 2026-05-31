from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.signaling_client import (
    SignalingClient,
    SignalingNotAllowedError,
)


class TestSignalingClientAllowlist(unittest.TestCase):
    def test_exact_endpoint_is_allowed(self):
        client = SignalingClient(
            server_url="wss://webrtc.ananta.de/signaling",
            allowed_servers=["wss://webrtc.ananta.de/signaling"],
            session_nonce="nonce",
        )
        client._check_allowlist()

    def test_origin_entry_allows_same_origin_paths(self):
        client = SignalingClient(
            server_url="wss://webrtc.ananta.de/signaling",
            allowed_servers=["wss://webrtc.ananta.de"],
            session_nonce="nonce",
        )
        client._check_allowlist()

    def test_prefix_sibling_host_is_rejected(self):
        client = SignalingClient(
            server_url="wss://webrtc.ananta.de.evil.example/signaling",
            allowed_servers=["wss://webrtc.ananta.de"],
            session_nonce="nonce",
        )
        with self.assertRaises(SignalingNotAllowedError):
            client._check_allowlist()

    def test_endpoint_path_must_match(self):
        client = SignalingClient(
            server_url="wss://webrtc.ananta.de/other",
            allowed_servers=["wss://webrtc.ananta.de/signaling"],
            session_nonce="nonce",
        )
        with self.assertRaises(SignalingNotAllowedError):
            client._check_allowlist()

    def test_scheme_must_match(self):
        client = SignalingClient(
            server_url="ws://webrtc.ananta.de/signaling",
            allowed_servers=["wss://webrtc.ananta.de/signaling"],
            session_nonce="nonce",
        )
        with self.assertRaises(SignalingNotAllowedError):
            client._check_allowlist()


if __name__ == "__main__":
    unittest.main()
