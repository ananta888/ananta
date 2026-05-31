from __future__ import annotations

import unittest

from client_surfaces.operator_tui.realtime.signaling_models import SignalingMessage
from client_surfaces.operator_tui.realtime.webrtc_policy import WebRtcPolicy
from client_surfaces.operator_tui.realtime.webrtc_session_controller import WebRtcSessionController


class FakeSignalingClient:
    def __init__(self) -> None:
        self.sent: list[SignalingMessage] = []
        self.disconnected = False

    def connect(self, timeout: float = 10.0) -> None:
        return None

    def disconnect(self) -> None:
        self.disconnected = True

    def send(self, msg: SignalingMessage) -> None:
        self.sent.append(msg)

    def receive(self, timeout: float = 0.0) -> None:
        return None


class TestWebRtcSessionController(unittest.TestCase):
    def test_start_requires_oidc_when_policy_requires_it(self):
        ctrl = WebRtcSessionController(
            signaling_client=FakeSignalingClient(),  # type: ignore[arg-type]
            policy=WebRtcPolicy(),
            session_id="s1",
        )
        with self.assertRaises(ValueError):
            ctrl.start_session(oidc_subject_hash="", session_nonce="nonce")

    def test_session_nonce_mismatch_is_rejected(self):
        ctrl = WebRtcSessionController(
            signaling_client=FakeSignalingClient(),  # type: ignore[arg-type]
            policy=WebRtcPolicy(),
            session_id="s1",
        )
        ctrl.start_session(oidc_subject_hash="subject-hash", session_nonce="nonce")
        ctrl._handle_signal(SignalingMessage(
            type="join",
            session_id="s1",
            sender_id="peer-1",
            recipient_id="python-controller",
            payload={},
            session_nonce="wrong",
        ))
        self.assertIn("nonce", ctrl.get_status()["error"])
        self.assertIsNone(ctrl.get_status()["peer_id"])

    def test_session_id_mismatch_is_rejected(self):
        ctrl = WebRtcSessionController(
            signaling_client=FakeSignalingClient(),  # type: ignore[arg-type]
            policy=WebRtcPolicy(),
            session_id="s1",
        )
        ctrl.start_session(oidc_subject_hash="subject-hash", session_nonce="nonce")
        ctrl._handle_signal(SignalingMessage(
            type="join",
            session_id="other",
            sender_id="peer-1",
            recipient_id="python-controller",
            payload={},
            session_nonce="nonce",
        ))
        self.assertIn("session_id", ctrl.get_status()["error"])
        self.assertIsNone(ctrl.get_status()["peer_id"])

    def test_accept_artifact_uses_controller_session_id(self):
        fake = FakeSignalingClient()
        ctrl = WebRtcSessionController(
            signaling_client=fake,  # type: ignore[arg-type]
            policy=WebRtcPolicy(),
            session_id="s1",
        )
        ctrl.start_session(oidc_subject_hash="subject-hash", session_nonce="nonce")
        ctrl._handle_signal(SignalingMessage(
            type="join",
            session_id="s1",
            sender_id="peer-1",
            recipient_id="python-controller",
            payload={},
            session_nonce="nonce",
        ))
        ctrl._handle_signal(SignalingMessage(
            type="offer",
            session_id="s1",
            sender_id="peer-1",
            recipient_id="python-controller",
            payload={"offer_id": "offer-1"},
            session_nonce="nonce",
        ))
        ctrl.accept_artifact("offer-1")
        self.assertEqual(fake.sent[-1].session_id, "s1")
        self.assertEqual(fake.sent[-1].recipient_id, "peer-1")


if __name__ == "__main__":
    unittest.main()
