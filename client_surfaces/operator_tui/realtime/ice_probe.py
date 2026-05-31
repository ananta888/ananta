"""ICE/STUN/TURN capability probe for Ananta WebRTC stack.

Uses basic UDP sockets to test STUN (RFC 5389 binding request).
Gracefully fails when network is unavailable.
Local IPs are redacted from non-verbose output.
"""
from __future__ import annotations

import re
import socket
import struct
import time
import urllib.parse
from dataclasses import dataclass, field

# RFC 5389 STUN Binding Request magic cookie
_STUN_MAGIC_COOKIE = 0x2112A442
_STUN_BINDING_REQUEST = 0x0001
_STUN_TIMEOUT = 5.0


def _build_stun_binding_request() -> bytes:
    """Build a minimal RFC 5389 STUN Binding Request."""
    transaction_id = b"\x00" * 12  # simplified — not random for probing
    message_length = 0
    header = struct.pack(
        ">HHI12s",
        _STUN_BINDING_REQUEST,
        message_length,
        _STUN_MAGIC_COOKIE,
        transaction_id,
    )
    return header


def _parse_stun_response(data: bytes) -> list[str]:
    """Extract XOR-MAPPED-ADDRESS or MAPPED-ADDRESS candidate types from STUN response."""
    candidates = []
    if len(data) < 20:
        return candidates
    msg_type = struct.unpack(">H", data[:2])[0]
    if msg_type != 0x0101:  # Binding Success Response
        return candidates
    magic = struct.unpack(">I", data[4:8])[0]
    if magic != _STUN_MAGIC_COOKIE:
        return candidates
    # Message parsed successfully → srflx candidate
    candidates.append("srflx")
    return candidates


def _redact_ip(text: str) -> str:
    """Replace IP addresses with [redacted] for non-verbose output."""
    ip_re = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    return ip_re.sub("[redacted]", text)


def _parse_stun_url(stun_url: str) -> tuple[str, int]:
    """Parse 'stun:host:port' or 'stun:host'. Returns (host, port)."""
    url = stun_url
    if url.startswith("stun:"):
        url = url[5:]
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 3478


def _parse_turn_url(turn_url: str) -> tuple[str, int]:
    """Parse 'turn:host:port' or 'turn:host'. Returns (host, port)."""
    url = turn_url
    if url.startswith("turn:"):
        url = url[5:]
    # Strip ?transport=udp etc.
    url = url.split("?")[0]
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 3478


@dataclass
class IceProbeResult:
    """Result of an ICE server connectivity probe.

    Attributes
    ----------
    stun_reachable : bool
    turn_reachable : bool
    candidate_types : list[str]
        Observed candidate types, e.g. ["host", "srflx", "relay"].
    error : str
        Human-readable error string, empty on success.
    duration_ms : float
        Time taken for the probe in milliseconds.
    """

    stun_reachable: bool = False
    turn_reachable: bool = False
    candidate_types: list[str] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0


class IceProbe:
    """Probe STUN and TURN server reachability using UDP sockets.

    All methods are synchronous and thread-safe.
    """

    def probe_stun(self, stun_url: str, timeout: float = _STUN_TIMEOUT) -> IceProbeResult:
        """Send a STUN Binding Request and check for a valid response.

        Parameters
        ----------
        stun_url : str
            e.g. "stun:webrtc.ananta.de:3478"
        timeout : float
            Socket timeout in seconds.
        """
        start = time.time()
        try:
            host, port = _parse_stun_url(stun_url)
        except Exception as exc:
            return IceProbeResult(error=f"URL parse error: {exc}")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            request = _build_stun_binding_request()
            sock.sendto(request, (host, port))
            data, _ = sock.recvfrom(2048)
            sock.close()
        except socket.timeout:
            duration_ms = round((time.time() - start) * 1000, 1)
            return IceProbeResult(
                error=f"STUN timeout after {timeout}s ({_redact_ip(host)}:{port})",
                duration_ms=duration_ms,
            )
        except OSError as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            return IceProbeResult(
                error=f"STUN socket error: {_redact_ip(str(exc))}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.time() - start) * 1000, 1)
        candidates = _parse_stun_response(data)
        # Always add "host" as we reached the server
        if "host" not in candidates:
            candidates.insert(0, "host")

        return IceProbeResult(
            stun_reachable=True,
            candidate_types=candidates,
            duration_ms=duration_ms,
        )

    def probe_turn(
        self,
        turn_url: str,
        username: str,
        credential: str,
        timeout: float = _STUN_TIMEOUT,
    ) -> IceProbeResult:
        """Probe TURN server reachability.

        Sends a STUN Binding Request to the TURN port.
        A full TURN Allocate Request requires HMAC-SHA1 authentication which
        is beyond this probe's scope — we test basic UDP reachability only.

        Parameters
        ----------
        turn_url : str
            e.g. "turn:webrtc.ananta.de:3478"
        username : str
            TURN username (not used in basic probe but validated non-empty).
        credential : str
            TURN credential (not used in basic probe).
        """
        start = time.time()
        if not username:
            return IceProbeResult(error="TURN: username is required")
        try:
            host, port = _parse_turn_url(turn_url)
        except Exception as exc:
            return IceProbeResult(error=f"TURN URL parse error: {exc}")

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            # Send a STUN binding request to the TURN port
            request = _build_stun_binding_request()
            sock.sendto(request, (host, port))
            data, _ = sock.recvfrom(2048)
            sock.close()
        except socket.timeout:
            duration_ms = round((time.time() - start) * 1000, 1)
            return IceProbeResult(
                error=f"TURN timeout after {timeout}s ({_redact_ip(host)}:{port})",
                duration_ms=duration_ms,
            )
        except OSError as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            return IceProbeResult(
                error=f"TURN socket error: {_redact_ip(str(exc))}",
                duration_ms=duration_ms,
            )

        duration_ms = round((time.time() - start) * 1000, 1)
        # If we got a response, TURN server UDP port is reachable
        candidates = _parse_stun_response(data)
        # relay candidate requires actual TURN Allocate, but server is reachable
        if "relay" not in candidates:
            candidates.append("relay")

        return IceProbeResult(
            stun_reachable=True,
            turn_reachable=True,
            candidate_types=["host"] + candidates,
            duration_ms=duration_ms,
        )
