"""WebRTC session controller — orchestrates signaling, ICE and DataChannel.

Boundary decision
-----------------
Python owns all session state, signaling, and policy enforcement.
The browser JS app (via window.ANANTA_CONFIG) receives ONLY derived,
non-secret session metadata (session_nonce, oidc_subject_hash, signaling_url,
ICE server URLs). The raw OIDC access/refresh token is NEVER passed to JS.

See docs/operator-tui/carbonyl-webrtc-session.md for the Mermaid diagram.

This module is completely separate from webrtc_transport.py (Hub Relay).
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .signaling_client import SignalingClient, SignalingClientError
from .signaling_models import SignalingMessage

if TYPE_CHECKING:
    from .webrtc_policy import WebRtcPolicy


# ---------------------------------------------------------------------------
# Transfer state machine
# ---------------------------------------------------------------------------

class _TransferState:
    IDLE = "idle"
    OFFER_PENDING = "offer_pending"
    TRANSFERRING = "transferring"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# WebRtcSessionController
# ---------------------------------------------------------------------------

class WebRtcSessionController:
    """Orchestrates a single WebRTC DataChannel session.

    Responsibilities:
    - Validate policy before starting.
    - Connect the signaling channel.
    - Drive ICE and DataChannel state machine via tick().
    - Accept or reject artifact offers from peers.
    - Expose a status dict consumed by TUI commands.

    Thread safety: tick() is non-blocking and may be called from the TUI
    event loop. Blocking operations run in daemon threads.
    """

    def __init__(
        self,
        signaling_client: SignalingClient,
        policy: WebRtcPolicy,
    ) -> None:
        self._signaling = signaling_client
        self._policy = policy
        self._lock = threading.Lock()

        self._session_id: str = ""
        self._oidc_subject_hash: str = ""
        self._session_nonce: str = ""
        self._peer_id: str | None = None

        self._auth_state: str = "unauthenticated"   # unauthenticated | authenticated
        self._signaling_state: str = "disconnected" # disconnected | connecting | connected | failed
        self._ice_state: str = "new"                # new | checking | connected | completed | failed
        self._datachannel_state: str = "closed"     # closed | connecting | open | error
        self._transfer_state: str = _TransferState.IDLE

        self._pending_offers: dict[str, dict] = {}  # offer_id -> offer payload
        self._error: str = ""
        self._started = False
        self._stopped = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, oidc_subject_hash: str, session_nonce: str) -> None:
        """Start the WebRTC session.

        Parameters
        ----------
        oidc_subject_hash : str
            Hashed OIDC subject — safe to pass to browser JS.
        session_nonce : str
            Short-lived session nonce — included in all messages.
        """
        with self._lock:
            if self._started:
                return
            self._oidc_subject_hash = oidc_subject_hash
            self._session_nonce = session_nonce
            self._auth_state = "authenticated" if oidc_subject_hash else "unauthenticated"
            self._started = True
            self._signaling_state = "connecting"

        t = threading.Thread(target=self._connect_signaling, daemon=True, name="webrtc-signaling")
        t.start()

    def stop_session(self) -> None:
        """Stop the session and clean up."""
        with self._lock:
            self._stopped = True
            self._signaling_state = "disconnected"
            self._ice_state = "closed"
            self._datachannel_state = "closed"
        try:
            self._signaling.disconnect()
        except Exception:
            pass

    def get_status(self) -> dict:
        """Return a snapshot of the current session state for TUI display."""
        with self._lock:
            return {
                "auth": self._auth_state,
                "signaling": self._signaling_state,
                "ice": self._ice_state,
                "datachannel": self._datachannel_state,
                "peer_id": self._peer_id,
                "transfer_state": self._transfer_state,
                "pending_offers": list(self._pending_offers.keys()),
                "error": self._error,
                "session_nonce_set": bool(self._session_nonce),
                "oidc_subject_hash": self._oidc_subject_hash,
            }

    def accept_artifact(self, offer_id: str) -> None:
        """Accept an artifact offer by offer_id.

        Raises ValueError if policy denies artifact exchange or offer not found.
        """
        if not self._policy.allows_artifact_exchange():
            reason = self._policy.denial_reason("artifact_exchange")
            raise ValueError(f"Artifact exchange denied: {reason}")
        with self._lock:
            if offer_id not in self._pending_offers:
                raise ValueError(f"No pending offer with id={offer_id!r}")
            offer = self._pending_offers.pop(offer_id)
            self._transfer_state = _TransferState.TRANSFERRING

        accept_msg = SignalingMessage(
            type="answer",
            session_id=self._session_id,
            sender_id="python-controller",
            recipient_id=self._peer_id or "",
            payload={"action": "artifact_accept", "offer_id": offer_id, "offer": offer},
            session_nonce=self._session_nonce,
        )
        try:
            self._signaling.send(accept_msg)
        except SignalingClientError as exc:
            with self._lock:
                self._transfer_state = _TransferState.FAILED
                self._error = str(exc)

    def reject_artifact(self, offer_id: str, reason: str) -> None:
        """Reject an artifact offer."""
        with self._lock:
            self._pending_offers.pop(offer_id, None)

        reject_msg = SignalingMessage(
            type="answer",
            session_id=self._session_id,
            sender_id="python-controller",
            recipient_id=self._peer_id or "",
            payload={"action": "artifact_reject", "offer_id": offer_id, "reason": reason},
            session_nonce=self._session_nonce,
        )
        try:
            self._signaling.send(reject_msg)
        except SignalingClientError:
            pass

    def tick(self) -> None:
        """Non-blocking tick — process pending signals from the recv queue."""
        if self._stopped:
            return
        msg = self._signaling.receive(timeout=0.0)
        if msg is None:
            return
        self._handle_signal(msg)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect_signaling(self) -> None:
        try:
            self._signaling.connect(timeout=10.0)
            with self._lock:
                self._signaling_state = "connected"
        except Exception as exc:
            with self._lock:
                self._signaling_state = "failed"
                self._error = str(exc)

    def _handle_signal(self, msg: SignalingMessage) -> None:
        t = msg.type
        if t == "join":
            with self._lock:
                self._peer_id = msg.sender_id
                self._ice_state = "checking"
        elif t == "offer":
            offer_id = msg.payload.get("offer_id", msg.message_id)
            with self._lock:
                self._pending_offers[offer_id] = msg.payload
                self._transfer_state = _TransferState.OFFER_PENDING
        elif t == "error":
            with self._lock:
                self._error = msg.payload.get("message", "signaling error")
        elif t == "heartbeat":
            pass  # no-op, keeps connection alive
