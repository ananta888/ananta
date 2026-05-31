"""Signaling message model for Ananta WebRTC DataChannel stack.

These models are used by SignalingClient and WebRtcSessionController.
They are completely separate from webrtc_transport.py (Hub Relay).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field


@dataclass
class SignalingMessage:
    """A message sent over the WebSocket signaling channel.

    Fields
    ------
    type : str
        One of: join | leave | offer | answer | ice_candidate | error | heartbeat
    session_id : str
        Identifies the WebRTC session.
    sender_id : str
        Peer ID of the sender.
    recipient_id : str
        Peer ID of the intended recipient. Empty string for broadcast.
    payload : dict
        Type-specific payload (SDP, ICE candidate, etc.).
    session_nonce : str
        A short-lived nonce derived from the OIDC session.
        NEVER the raw OIDC access token or refresh token.
    message_id : str
        UUID4 for idempotency.
    timestamp : float
        UTC seconds since epoch.
    """

    type: str
    session_id: str
    sender_id: str
    recipient_id: str
    payload: dict
    session_nonce: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: __import__("time").time())

    ALLOWED_TYPES: frozenset[str] = frozenset(
        {"join", "leave", "offer", "answer", "ice_candidate", "error", "heartbeat"}
    )

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "payload": self.payload,
            "session_nonce": self.session_nonce,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> SignalingMessage:
        return cls(
            type=str(data["type"]),
            session_id=str(data.get("session_id", "")),
            sender_id=str(data.get("sender_id", "")),
            recipient_id=str(data.get("recipient_id", "")),
            payload=dict(data.get("payload") or {}),
            session_nonce=str(data.get("session_nonce", "")),
            message_id=str(data.get("message_id") or uuid.uuid4()),
            timestamp=float(data.get("timestamp") or __import__("time").time()),
        )

    @classmethod
    def from_json(cls, raw: str) -> SignalingMessage:
        return cls.from_dict(json.loads(raw))
