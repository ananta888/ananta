"""Ananta DataChannel Protocol v1 — Python implementation.

Must stay in sync with JS implementation in
client_surfaces/operator_tui/visual/browser/webrtc_app/datachannel.js.

Protocol invariants:
  VERSION = 1
  MAX_MESSAGE_BYTES = 65536
  MAX_ARTIFACT_BYTES = 104857600 (100 MiB)
  CHUNK_SIZE = 32768
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field

MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "hello",
        "hello_ack",
        "ping",
        "pong",
        "artifact_offer",
        "artifact_accept",
        "artifact_reject",
        "artifact_chunk",
        "artifact_complete",
        "error",
    }
)


@dataclass
class DataChannelMessage:
    """A message transmitted over an RTCDataChannel.

    Attributes
    ----------
    type : str
        Must be one of MESSAGE_TYPES.
    protocol_version : int
        Must equal DataChannelProtocol.VERSION (= 1).
    session_nonce : str
        Short-lived session nonce (not the raw OIDC token).
    message_id : str
        UUID4 for tracing and idempotency.
    timestamp : float
        UTC seconds since epoch.
    payload : dict
        Type-specific data.
    """

    type: str
    protocol_version: int = 1
    session_nonce: str = ""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "protocol_version": self.protocol_version,
            "session_nonce": self.session_nonce,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> DataChannelMessage:
        return cls(
            type=str(data["type"]),
            protocol_version=int(data.get("protocol_version", 0)),
            session_nonce=str(data.get("session_nonce", "")),
            message_id=str(data.get("message_id") or uuid.uuid4()),
            timestamp=float(data.get("timestamp") or time.time()),
            payload=dict(data.get("payload") or {}),
        )


class DataChannelProtocol:
    """Encode/decode DataChannel messages per protocol version 1."""

    VERSION = 1
    MAX_MESSAGE_BYTES = 65536
    MAX_ARTIFACT_BYTES = 104857600  # 100 MiB
    CHUNK_SIZE = 32768

    # ------------------------------------------------------------------
    # Encode / Decode
    # ------------------------------------------------------------------

    def encode(self, msg: DataChannelMessage) -> bytes:
        """Serialize to UTF-8 bytes. Raises ValueError if too large."""
        raw = msg.to_json().encode("utf-8")
        if len(raw) > self.MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Message too large: {len(raw)} bytes > {self.MAX_MESSAGE_BYTES}"
            )
        return raw

    def decode(self, data: bytes) -> DataChannelMessage:
        """Deserialize from bytes.

        Raises ValueError if:
        - message exceeds MAX_MESSAGE_BYTES
        - protocol_version != VERSION
        - unknown message type
        """
        if len(data) > self.MAX_MESSAGE_BYTES:
            raise ValueError(
                f"Incoming message too large: {len(data)} bytes > {self.MAX_MESSAGE_BYTES}"
            )
        try:
            obj = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot decode message: {exc}") from exc

        version = int(obj.get("protocol_version", 0))
        if version != self.VERSION:
            raise ValueError(
                f"Protocol version mismatch: got {version}, expected {self.VERSION}"
            )
        msg_type = str(obj.get("type", ""))
        if msg_type not in MESSAGE_TYPES:
            raise ValueError(f"Unknown message type: {msg_type!r}")

        return DataChannelMessage.from_dict(obj)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    def make_ping(self, session_nonce: str) -> DataChannelMessage:
        return DataChannelMessage(
            type="ping",
            protocol_version=self.VERSION,
            session_nonce=session_nonce,
        )

    def make_pong(self, ping: DataChannelMessage) -> DataChannelMessage:
        latency_ms = round((time.time() - ping.timestamp) * 1000)
        return DataChannelMessage(
            type="pong",
            protocol_version=self.VERSION,
            session_nonce=ping.session_nonce,
            payload={
                "ping_message_id": ping.message_id,
                "latency_ms": latency_ms,
            },
        )

    def make_artifact_offer(
        self,
        filename: str,
        size: int,
        sha256: str,
        session_nonce: str,
    ) -> DataChannelMessage:
        if size > self.MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"Artifact too large: {size} bytes > {self.MAX_ARTIFACT_BYTES}"
            )
        offer_id = str(uuid.uuid4())
        total_chunks = (size + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE
        return DataChannelMessage(
            type="artifact_offer",
            protocol_version=self.VERSION,
            session_nonce=session_nonce,
            payload={
                "offer_id": offer_id,
                "filename": filename,
                "size": size,
                "sha256": sha256,
                "chunk_size": self.CHUNK_SIZE,
                "total_chunks": total_chunks,
            },
        )

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    @staticmethod
    def verify_artifact_integrity(data: bytes, expected_sha256: str) -> bool:
        """Return True iff SHA-256 of data matches expected_sha256 (hex)."""
        actual = hashlib.sha256(data).hexdigest()
        return actual.lower() == expected_sha256.lower()
