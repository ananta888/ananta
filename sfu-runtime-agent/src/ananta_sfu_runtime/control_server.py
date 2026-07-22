"""Small mTLS-only HTTP facade for the optional runtime extension mode."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID

from .command_guard import RuntimeCommandGuard, RuntimeCommandGuardConfig, RuntimeCommandGuardError

MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 65_536
_ROUTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_READ_PATHS = frozenset({"/v1/capabilities", "/v1/routes/observe", "/v1/health"})
_CONTROL_PATHS = frozenset(
    {"/v1/routes/apply", "/v1/routes/update", "/v1/routes/revoke", "/v1/drain"}
)
_ALL_PATHS = _READ_PATHS | _CONTROL_PATHS


class RuntimeBoundaryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class RuntimeControlBackend(Protocol):
    def capabilities(self) -> Mapping[str, object]: ...

    def apply(self, route: Mapping[str, object]) -> Mapping[str, object]: ...

    def update(self, route: Mapping[str, object]) -> Mapping[str, object]: ...

    def revoke(self, route: Mapping[str, object]) -> Mapping[str, object]: ...

    def observe(self, route: Mapping[str, object]) -> Mapping[str, object]: ...

    def health(self) -> Mapping[str, object]: ...

    def drain(self) -> Mapping[str, object]: ...


class UnsupportedRuntimeControlBackend:
    def capabilities(self) -> Mapping[str, object]:
        return {
            "runtime_control_mode": "authenticated_runtime_extension",
            "available": False,
            "reason_code": "runtime_extension_backend_unsupported",
        }

    def health(self) -> Mapping[str, object]:
        return {"ready": False, "reason_code": "runtime_extension_backend_unsupported"}

    def _unsupported(self, _route=None):
        raise RuntimeBoundaryError("runtime_extension_backend_unsupported", status_code=501)

    apply = update = revoke = observe = drain = _unsupported


class PeerCertificateAuthorizer:
    def __init__(
        self,
        *,
        san_prefix: str,
        revocation_file: Path,
        clock=time.time,
    ) -> None:
        self._san_prefix = san_prefix.rstrip("/")
        self._revocation_file = revocation_file
        self._clock = clock

    def authorize(self, certificate_der: bytes | None, *, control_required: bool) -> str:
        if not certificate_der:
            raise RuntimeBoundaryError("runtime_mtls_peer_required", status_code=401)
        try:
            certificate = x509.load_der_x509_certificate(certificate_der)
        except ValueError as exc:
            raise RuntimeBoundaryError("runtime_mtls_peer_invalid", status_code=401) from exc
        now = self._clock()
        not_before = getattr(certificate, "not_valid_before_utc", certificate.not_valid_before.replace(tzinfo=None))
        not_after = getattr(certificate, "not_valid_after_utc", certificate.not_valid_after.replace(tzinfo=None))
        now_value = time.gmtime(now)
        if now < not_before.timestamp() or now > not_after.timestamp():
            raise RuntimeBoundaryError("runtime_mtls_peer_expired", status_code=401)
        fingerprint = f"sha256:{certificate.fingerprint(hashes.SHA256()).hex()}"
        if fingerprint in self._revoked_fingerprints():
            raise RuntimeBoundaryError("runtime_mtls_peer_revoked", status_code=401)
        try:
            sans = certificate.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
        except x509.ExtensionNotFound as exc:
            raise RuntimeBoundaryError("runtime_mtls_peer_san_missing", status_code=403) from exc
        try:
            usages = certificate.extensions.get_extension_for_oid(
                ExtensionOID.EXTENDED_KEY_USAGE
            ).value
        except x509.ExtensionNotFound as exc:
            raise RuntimeBoundaryError("runtime_mtls_peer_eku_missing", status_code=403) from exc
        if ExtendedKeyUsageOID.CLIENT_AUTH not in usages:
            raise RuntimeBoundaryError("runtime_mtls_client_auth_required", status_code=403)
        role = None
        for candidate in ("sfu_control", "sfu_observer"):
            prefix = f"{self._san_prefix}/{candidate}/"
            if len(sans) == 1 and sans[0].startswith(prefix) and len(sans[0]) > len(prefix):
                role = candidate
        if role is None:
            raise RuntimeBoundaryError("runtime_mtls_peer_san_invalid", status_code=403)
        if control_required and role != "sfu_control":
            raise RuntimeBoundaryError("runtime_mtls_control_role_required", status_code=403)
        return fingerprint

    def _revoked_fingerprints(self) -> frozenset[str]:
        try:
            if self._revocation_file.stat().st_size > 65_536:
                raise RuntimeBoundaryError("runtime_revocation_store_oversize", status_code=503)
            lines = self._revocation_file.read_text(encoding="ascii").splitlines()
        except OSError as exc:
            raise RuntimeBoundaryError("runtime_revocation_store_unavailable", status_code=503) from exc
        if len(lines) > 1_024:
            raise RuntimeBoundaryError("runtime_revocation_store_oversize", status_code=503)
        return frozenset(line.strip().lower() for line in lines if line.strip())


class FixedWindowRateLimiter:
    """Protective edge limiter only; it is not an identity or policy authority."""

    def __init__(
        self,
        *,
        limit: int = 60,
        window_seconds: int = 60,
        bucket_count_max: int = 1_024,
        clock=time.time,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._bucket_count_max = bucket_count_max
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, int], int] = {}

    def consume(self, fingerprint: str) -> bool:
        window = int(self._clock() // self._window)
        key = (fingerprint, window)
        with self._lock:
            self._buckets = {
                existing: count
                for existing, count in self._buckets.items()
                if existing[1] >= window - 1
            }
            if key not in self._buckets and len(self._buckets) >= self._bucket_count_max:
                return False
            count = self._buckets.get(key, 0) + 1
            self._buckets[key] = count
            return count <= self._limit


class RuntimeControlApplication:
    def __init__(
        self,
        backend: RuntimeControlBackend,
        authorizer: PeerCertificateAuthorizer,
        *,
        rate_limiter: FixedWindowRateLimiter | None = None,
        command_guard: RuntimeCommandGuard | None = None,
    ) -> None:
        self._backend = backend
        self._authorizer = authorizer
        self._rate_limiter = rate_limiter or FixedWindowRateLimiter()
        self._command_guard = command_guard

    def handle(
        self,
        *,
        method: str,
        path: str,
        body: Mapping[str, object],
        peer_certificate_der: bytes | None,
    ) -> tuple[int, Mapping[str, object]]:
        if path not in _ALL_PATHS:
            return 404, {"status": "error", "reason_code": "runtime_endpoint_not_found"}
        expected_method = "GET" if path in {"/v1/capabilities", "/v1/health"} else "POST"
        if method != expected_method:
            return 405, {"status": "error", "reason_code": "runtime_method_not_allowed"}
        try:
            fingerprint = self._authorizer.authorize(
                peer_certificate_der,
                control_required=path in _CONTROL_PATHS,
            )
            if not self._rate_limiter.consume(fingerprint):
                raise RuntimeBoundaryError("runtime_control_rate_limited", status_code=429)
            if path in _CONTROL_PATHS:
                if self._command_guard is None:
                    raise RuntimeBoundaryError(
                        "runtime_command_guard_unavailable", status_code=503
                    )
                payload = self._command_guard.execute(
                    path=path,
                    envelope=body,
                    action=lambda command_body: self._dispatch(path, command_body),
                )
            else:
                payload = self._dispatch(path, body)
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            if len(encoded) > MAX_RESPONSE_BYTES:
                raise RuntimeBoundaryError("runtime_response_oversize", status_code=502)
            if path == "/v1/health" and not bool(payload.get("ready")):
                return 503, payload
            return 200, payload
        except (RuntimeBoundaryError, RuntimeCommandGuardError) as exc:
            return exc.status_code, {"status": "error", "reason_code": exc.reason_code}

    def _dispatch(self, path: str, body: Mapping[str, object]) -> Mapping[str, object]:
        if path == "/v1/capabilities":
            _require_fields(body, frozenset())
            return self._backend.capabilities()
        if path == "/v1/health":
            _require_fields(body, frozenset())
            return self._backend.health()
        if path == "/v1/drain":
            _require_fields(body, frozenset({"operation_id", "reason"}))
            return self._backend.drain()
        if path == "/v1/routes/observe":
            _require_fields(body, frozenset({"route_id", "room_name"}))
            _validate_route_lookup(body)
            return self._backend.observe(body)
        if path == "/v1/routes/revoke":
            _require_fields(
                body,
                frozenset(
                    {"operation_id", "route_id", "room_name", "receiver_identities", "track_sids"}
                ),
            )
            _validate_route_mutation(body)
            return self._backend.revoke(body)
        if path in {"/v1/routes/apply", "/v1/routes/update"}:
            allowed = {"operation_id", "route"}
            if path.endswith("update"):
                allowed.add("expected_route_digest")
            _require_fields(body, frozenset(allowed))
            route = body.get("route")
            if not isinstance(route, Mapping):
                raise RuntimeBoundaryError("runtime_route_object_required")
            _require_fields(
                route,
                frozenset({"route_id", "room_name", "receiver_identities", "track_sids"}),
            )
            _validate_route_mutation(route)
            return self._backend.update(body) if path.endswith("update") else self._backend.apply(body)
        raise RuntimeBoundaryError("runtime_endpoint_not_found", status_code=404)


def _require_fields(body: Mapping[str, object], allowed: frozenset[str]) -> None:
    if set(body) != set(allowed):
        raise RuntimeBoundaryError("runtime_request_fields_invalid")


def _validate_route_lookup(route: Mapping[str, object]) -> None:
    if not _ROUTE_ID.fullmatch(str(route.get("route_id") or "")):
        raise RuntimeBoundaryError("runtime_route_id_invalid")
    if not _ROUTE_ID.fullmatch(str(route.get("room_name") or "")):
        raise RuntimeBoundaryError("runtime_room_name_invalid")


def _validate_route_mutation(route: Mapping[str, object]) -> None:
    _validate_route_lookup(route)
    receivers = route.get("receiver_identities")
    tracks = route.get("track_sids")
    if not isinstance(receivers, list) or not 1 <= len(receivers) <= 7:
        raise RuntimeBoundaryError("runtime_receivers_invalid")
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 64:
        raise RuntimeBoundaryError("runtime_tracks_invalid")
    if len(set(map(str, receivers))) != len(receivers) or len(set(map(str, tracks))) != len(tracks):
        raise RuntimeBoundaryError("runtime_route_duplicates_invalid")


class _Server(ThreadingHTTPServer):
    application: RuntimeControlApplication


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:
        self._handle({})

    def do_POST(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            self._write(411, {"status": "error", "reason_code": "runtime_content_length_required"})
            return
        length = int(raw_length)
        if length > MAX_REQUEST_BYTES:
            self._write(413, {"status": "error", "reason_code": "runtime_request_oversize"})
            return
        try:
            decoded = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self._write(400, {"status": "error", "reason_code": "runtime_request_json_invalid"})
            return
        if not isinstance(decoded, Mapping):
            self._write(400, {"status": "error", "reason_code": "runtime_request_object_required"})
            return
        self._handle(decoded)

    def _handle(self, body: Mapping[str, object]) -> None:
        certificate = self.connection.getpeercert(binary_form=True)
        status, payload = self.server.application.handle(
            method=self.command,
            path=self.path,
            body=body,
            peer_certificate_der=certificate,
        )
        self._write(status, payload)

    def _write(self, status: int, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args) -> None:
        return


def build_server(
    *,
    host: str,
    port: int,
    server_certificate: Path,
    server_private_key: Path,
    client_ca: Path,
    revocation_file: Path,
    backend: RuntimeControlBackend,
    command_guard: RuntimeCommandGuard | None = None,
) -> ThreadingHTTPServer:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(client_ca))
    context.load_cert_chain(certfile=str(server_certificate), keyfile=str(server_private_key))
    server = _Server((host, port), _Handler)
    server.application = RuntimeControlApplication(
        backend,
        PeerCertificateAuthorizer(
            san_prefix="spiffe://ananta.local/sfu",
            revocation_file=revocation_file,
        ),
        command_guard=command_guard,
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main() -> None:
    if os.environ.get("ANANTA_SFU_RUNTIME_CONTROL_MODE") != "authenticated_runtime_extension":
        raise RuntimeBoundaryError("runtime_extension_mode_not_selected", status_code=503)
    server = build_server(
        host="0.0.0.0",
        port=int(os.environ.get("ANANTA_SFU_RUNTIME_PORT", "8443")),
        server_certificate=Path(os.environ["ANANTA_SFU_RUNTIME_SERVER_CERT"]),
        server_private_key=Path(os.environ["ANANTA_SFU_RUNTIME_SERVER_KEY"]),
        client_ca=Path(os.environ["ANANTA_SFU_RUNTIME_CLIENT_CA"]),
        revocation_file=Path(os.environ["ANANTA_SFU_RUNTIME_REVOCATIONS"]),
        backend=UnsupportedRuntimeControlBackend(),
        command_guard=RuntimeCommandGuard(
            RuntimeCommandGuardConfig.from_environment(os.environ)
        ),
    )
    server.serve_forever(poll_interval=0.5)


__all__ = [
    "FixedWindowRateLimiter",
    "PeerCertificateAuthorizer",
    "RuntimeBoundaryError",
    "RuntimeControlApplication",
    "RuntimeControlBackend",
    "UnsupportedRuntimeControlBackend",
    "build_server",
    "main",
]
