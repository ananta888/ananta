"""WebSocket-based signaling client for Ananta WebRTC DataChannel stack.

Architecture: Option C — completely separate from webrtc_transport.py (Hub Relay).
This module connects to wss://webrtc.ananta.de/signaling using OIDC session nonce
authentication. It does NOT use Hub JWT tokens.

WebSocket implementation strategy:
  1. Try to import `websockets` (async) — wrap in sync thread executor.
  2. Try to import `websocket` (websocket-client, sync).
  3. Fall back to minimal RFC 6455 WebSocket implementation over ssl.SSLContext.

The URL allowlist is checked BEFORE any network connection is attempted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .signaling_models import SignalingMessage


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SignalingClientError(Exception):
    """Base error for SignalingClient."""


class SignalingNotAllowedError(SignalingClientError):
    """Raised when the target URL is not in the allowlist."""


class SignalingConnectionError(SignalingClientError):
    """Raised when the WebSocket connection fails."""


# ---------------------------------------------------------------------------
# Minimal RFC 6455 WebSocket framing (stdlib only)
# ---------------------------------------------------------------------------

def _make_websocket_key() -> str:
    return base64.b64encode(os.urandom(16)).decode()


def _expected_accept(key: str) -> str:
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    digest = hashlib.sha1((key + magic).encode()).digest()
    return base64.b64encode(digest).decode()


class _MinimalWebSocket:
    """Synchronous RFC 6455 WebSocket client using only stdlib."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._closed = False

    def connect(self) -> None:
        parsed = urllib.parse.urlparse(self._url)
        scheme = parsed.scheme
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection((host, port), timeout=self._timeout)

        if scheme == "wss":
            ctx = ssl.create_default_context()
            self._sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        else:
            self._sock = raw_sock

        self._sock.settimeout(self._timeout)

        # HTTP upgrade handshake
        key = _make_websocket_key()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(handshake.encode())

        # Read response headers
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise SignalingConnectionError("WebSocket handshake: server closed connection")
            response += chunk

        header_part = response.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
        first_line = header_part.split("\r\n")[0]
        if "101" not in first_line:
            raise SignalingConnectionError(f"WebSocket handshake failed: {first_line}")

        # Verify accept key
        expected = _expected_accept(key)
        if expected not in header_part:
            raise SignalingConnectionError("WebSocket handshake: accept key mismatch")

        self._sock.settimeout(None)  # switch to blocking for recv thread

    def send_text(self, text: str) -> None:
        """Send a text frame (opcode 0x1) with masking (required for clients)."""
        payload = text.encode("utf-8")
        self._send_frame(0x1, payload)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask_key = os.urandom(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        length = len(payload)

        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode
        if length < 126:
            header.append(0x80 | length)  # MASK bit set
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask_key
        header += masked

        with self._lock:
            if self._sock:
                self._sock.sendall(bytes(header))

    def recv_text(self, timeout: float = 1.0) -> str | None:
        """Read one text frame, return None on timeout or close."""
        if not self._sock or self._closed:
            return None
        self._sock.settimeout(timeout)
        try:
            return self._read_frame()
        except (TimeoutError, socket.timeout):
            return None
        except OSError:
            self._closed = True
            return None
        finally:
            self._sock.settimeout(None)

    def _read_frame(self) -> str | None:
        def recv_exactly(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = self._sock.recv(n - len(buf))  # type: ignore[union-attr]
                if not chunk:
                    raise SignalingConnectionError("Connection closed mid-frame")
                buf += chunk
            return buf

        b1, b2 = recv_exactly(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F

        if length == 126:
            length = struct.unpack(">H", recv_exactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", recv_exactly(8))[0]

        mask_key = recv_exactly(4) if masked else b""
        payload = recv_exactly(length)

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x8:  # close frame
            self._closed = True
            return None
        if opcode == 0x9:  # ping
            self._send_frame(0xA, payload)  # pong
            return None
        if opcode in (0x1, 0x2):  # text or binary
            return payload.decode("utf-8", errors="replace")
        # continuation or other — skip
        return None

    def close(self) -> None:
        self._closed = True
        try:
            if self._sock:
                self._send_frame(0x8, b"")  # close frame
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None and not self._closed


# ---------------------------------------------------------------------------
# SignalingClient
# ---------------------------------------------------------------------------

class SignalingClient:
    """Allowlisted WebSocket signaling client.

    The URL allowlist is enforced before any network call.
    ``session_nonce`` is included in all messages; the raw OIDC token is NEVER used here.
    """

    ALLOWED_SERVERS: list[str] = []  # populated from config at runtime

    def __init__(
        self,
        server_url: str,
        allowed_servers: list[str],
        session_nonce: str,
    ) -> None:
        self._server_url = server_url
        self._allowed_servers = list(allowed_servers)
        self._session_nonce = session_nonce
        self._ws: _MinimalWebSocket | None = None
        self._recv_queue: queue.Queue[str | None] = queue.Queue()
        self._recv_thread: threading.Thread | None = None
        self._connected = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        """Open the WebSocket connection.

        Raises
        ------
        SignalingNotAllowedError
            If ``server_url`` is not in ``allowed_servers``.
        SignalingConnectionError
            If the TCP/TLS/WebSocket handshake fails.
        """
        self._check_allowlist()
        ws = _MinimalWebSocket(self._server_url, timeout=timeout)
        ws.connect()
        self._ws = ws
        self._connected = True
        self._start_recv_thread()

    def send(self, msg: SignalingMessage) -> None:
        """Send a SignalingMessage. Raises SignalingClientError if not connected."""
        if not self._connected or not self._ws:
            raise SignalingClientError("Not connected")
        self._ws.send_text(msg.to_json())

    def receive(self, timeout: float = 1.0) -> SignalingMessage | None:
        """Return the next incoming SignalingMessage, or None on timeout."""
        try:
            raw = self._recv_queue.get(timeout=timeout)
            if raw is None:
                self._connected = False
                return None
            return SignalingMessage.from_json(raw)
        except queue.Empty:
            return None
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def disconnect(self) -> None:
        """Close the connection gracefully."""
        self._connected = False
        if self._ws:
            self._ws.close()
            self._ws = None
        # Unblock any waiting receive() calls
        self._recv_queue.put_nowait(None)

    def is_connected(self) -> bool:
        return self._connected and self._ws is not None and self._ws.is_open

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_allowlist(self) -> None:
        url = self._server_url.rstrip("/")
        for allowed in self._allowed_servers:
            if url.startswith(allowed.rstrip("/")):
                return
        raise SignalingNotAllowedError(
            f"Server URL not in allowlist: {self._server_url!r}. "
            f"Allowed: {self._allowed_servers}"
        )

    def _start_recv_thread(self) -> None:
        t = threading.Thread(target=self._recv_loop, daemon=True, name="signaling-recv")
        self._recv_thread = t
        t.start()

    def _recv_loop(self) -> None:
        while self._connected and self._ws and self._ws.is_open:
            try:
                raw = self._ws.recv_text(timeout=1.0)
                if raw is None:
                    if not self._ws.is_open:
                        break
                    continue
                self._recv_queue.put_nowait(raw)
            except Exception:
                break
        self._connected = False
        self._recv_queue.put_nowait(None)
