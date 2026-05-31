from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.webrtc_policy import WebRtcPolicy


class TestWebRtcPolicy(unittest.TestCase):
    def test_media_permissions_default_to_disabled(self):
        policy = WebRtcPolicy()
        self.assertIn("disabled", policy.denial_reason("camera").lower())
        self.assertIn("disabled", policy.denial_reason("microphone").lower())
        self.assertIn("disabled", policy.denial_reason("screen_share").lower())

    def test_artifact_exchange_requires_datachannel(self):
        policy = WebRtcPolicy(datachannel_enabled=False)
        self.assertFalse(policy.allows_artifact_exchange())
        self.assertIn("datachannel", policy.denial_reason("artifact_exchange").lower())


if __name__ == "__main__":
    unittest.main()
