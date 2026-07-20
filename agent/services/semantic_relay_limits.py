"""Resource policy for opaque semantic relay envelopes."""

from __future__ import annotations

from dataclasses import dataclass

from ananta_contracts.webrtc_datachannel import TRAFFIC_CLASS_LIMITS


@dataclass(frozen=True, slots=True)
class SemanticRelayLimits:
    max_request_bytes: int = 1_500_000
    max_batch_count: int = 100
    max_session_messages: int = 2_000
    max_session_bytes: int = 32 * 1024 * 1024
    max_peer_messages: int = 500
    max_peer_bytes: int = 8 * 1024 * 1024
    max_global_messages: int = 20_000
    max_global_bytes: int = 256 * 1024 * 1024
    priority_reserve_messages: int = 200
    priority_reserve_bytes: int = 16 * 1024 * 1024
    max_sessions: int = 2_000
    max_peers_per_session: int = 100
    max_poll_per_minute: int = 240
    retention_seconds: int = 300

    def envelope_limit(self, traffic_class: str) -> int:
        return int(TRAFFIC_CLASS_LIMITS.get(traffic_class, 0))

    def global_message_limit(self, traffic_class: str) -> int:
        if traffic_class in {"control", "transcript"}:
            return self.max_global_messages
        reserve = min(self.priority_reserve_messages, max(0, self.max_global_messages - 1))
        return self.max_global_messages - reserve

    def global_byte_limit(self, traffic_class: str) -> int:
        if traffic_class in {"control", "transcript"}:
            return self.max_global_bytes
        reserve = min(self.priority_reserve_bytes, max(0, self.max_global_bytes - 1))
        return self.max_global_bytes - reserve

    def validate(self) -> None:
        values = (
            self.__dict__
            if hasattr(self, "__dict__")
            else {field: getattr(self, field) for field in self.__dataclass_fields__}
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values.values()):
            raise ValueError("semantic_relay_limits_invalid")


DEFAULT_SEMANTIC_RELAY_LIMITS = SemanticRelayLimits()


__all__ = ["DEFAULT_SEMANTIC_RELAY_LIMITS", "SemanticRelayLimits"]
