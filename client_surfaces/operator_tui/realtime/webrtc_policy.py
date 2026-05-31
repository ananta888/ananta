"""WebRTC session policy for Ananta DataChannel stack.

Defines what is permitted in a WebRTC session.
All media capabilities are disabled by default.
Artifact exchange requires explicit user acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass

_ARTIFACT_EXCHANGE_VALUES = frozenset({"explicit_accept_required", "disabled"})
_REMOTE_URLS_VALUES = frozenset({"allowlist_only", "disabled"})


@dataclass
class WebRtcPolicy:
    """Immutable policy for a single WebRTC session.

    Attributes
    ----------
    require_oidc_session : bool
        Session must be authenticated via OIDC before WebRTC proceeds.
    datachannel_enabled : bool
        Whether RTCDataChannel is permitted.
    artifact_exchange : str
        "explicit_accept_required" — each artifact offer must be explicitly
        accepted by the operator before transfer begins.
        "disabled" — artifact exchange is not permitted.
    camera_enabled : bool
        Whether camera (getUserMedia video) is permitted. Default False.
    microphone_enabled : bool
        Whether microphone (getUserMedia audio) is permitted. Default False.
    screen_share_enabled : bool
        Whether getDisplayMedia is permitted. Default False.
    remote_urls : str
        "allowlist_only" — only URLs from the configured allowlist.
        "disabled" — no remote URLs at all.
    reject_unknown_peers : bool
        Reject peer connections from unknown/unregistered peer IDs.
    bind_peer_to_oidc_subject : bool
        Require that the peer's identity is bound to the OIDC subject.
    require_session_nonce : bool
        Require a valid session_nonce in all DataChannel messages.
    """

    require_oidc_session: bool = True
    datachannel_enabled: bool = True
    artifact_exchange: str = "explicit_accept_required"
    camera_enabled: bool = False
    microphone_enabled: bool = False
    screen_share_enabled: bool = False
    remote_urls: str = "allowlist_only"
    reject_unknown_peers: bool = True
    bind_peer_to_oidc_subject: bool = True
    require_session_nonce: bool = True

    def __post_init__(self) -> None:
        if self.artifact_exchange not in _ARTIFACT_EXCHANGE_VALUES:
            raise ValueError(
                f"artifact_exchange must be one of {sorted(_ARTIFACT_EXCHANGE_VALUES)}, "
                f"got {self.artifact_exchange!r}"
            )
        if self.remote_urls not in _REMOTE_URLS_VALUES:
            raise ValueError(
                f"remote_urls must be one of {sorted(_REMOTE_URLS_VALUES)}, "
                f"got {self.remote_urls!r}"
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def allows_artifact_exchange(self) -> bool:
        return self.datachannel_enabled and self.artifact_exchange == "explicit_accept_required"

    def allows_datachannel(self) -> bool:
        return self.datachannel_enabled

    def denial_reason(self, action: str) -> str:
        """Return a human-readable reason why ``action`` is denied.

        Returns an empty string if the action is allowed.
        """
        action = action.lower()

        if action == "datachannel":
            if not self.datachannel_enabled:
                return "DataChannel is disabled by policy"
            return ""

        if action == "artifact_exchange":
            if not self.datachannel_enabled:
                return "Artifact exchange requires DataChannel, which is disabled by policy"
            if self.artifact_exchange == "disabled":
                return "Artifact exchange is disabled by policy"
            return ""

        if action == "camera":
            if not self.camera_enabled:
                return "Camera access is disabled by policy"
            return ""

        if action == "microphone":
            if not self.microphone_enabled:
                return "Microphone access is disabled by policy"
            return ""

        if action == "screen_share":
            if not self.screen_share_enabled:
                return "Screen sharing is disabled by policy"
            return ""

        if action == "oidc_session":
            if not self.require_oidc_session:
                return ""  # not required, so no denial
            return ""

        return f"Unknown action: {action!r}"
