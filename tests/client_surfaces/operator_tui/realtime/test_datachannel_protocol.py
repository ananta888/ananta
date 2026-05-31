from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.datachannel_protocol import (
    DataChannelMessage,
    DataChannelProtocol,
)


class TestDataChannelProtocol(unittest.TestCase):
    def setUp(self):
        self.protocol = DataChannelProtocol()

    def test_ping_round_trips(self):
        msg = self.protocol.make_ping("nonce")
        decoded = self.protocol.decode(self.protocol.encode(msg))
        self.assertEqual(decoded.type, "ping")
        self.assertEqual(decoded.protocol_version, DataChannelProtocol.VERSION)
        self.assertEqual(decoded.session_nonce, "nonce")

    def test_unknown_message_type_is_rejected(self):
        raw = DataChannelMessage(type="unknown", session_nonce="nonce").to_json().encode("utf-8")
        with self.assertRaises(ValueError):
            self.protocol.decode(raw)

    def test_protocol_version_mismatch_is_rejected(self):
        raw = DataChannelMessage(type="ping", protocol_version=999, session_nonce="nonce").to_json().encode("utf-8")
        with self.assertRaises(ValueError):
            self.protocol.decode(raw)

    def test_artifact_size_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            self.protocol.make_artifact_offer(
                filename="big.bin",
                size=DataChannelProtocol.MAX_ARTIFACT_BYTES + 1,
                sha256="0" * 64,
                session_nonce="nonce",
            )

    def test_sha256_integrity_helper(self):
        self.assertTrue(
            DataChannelProtocol.verify_artifact_integrity(
                b"artifact",
                "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c",
            )
        )
        self.assertFalse(DataChannelProtocol.verify_artifact_integrity(b"artifact", "0" * 64))


if __name__ == "__main__":
    unittest.main()
