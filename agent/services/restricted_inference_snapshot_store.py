"""Secure staging and atomic promotion of restricted model snapshots."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import socket
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from agent.services.restricted_inference_model_manifest import (
    SOURCE_HUGGINGFACE_SNAPSHOT,
    ModelManifestValidationError,
    ModelSnapshotValidator,
    RestrictedModelManifest,
    VerifiedModelSnapshot,
)


@dataclass(frozen=True)
class RemoteSnapshotPolicy:
    enabled: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    max_redirects: int = 2
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.enabled and not self.allowed_hosts:
            raise ValueError("remote downloads require an explicit host allowlist")
        if not 0 <= self.max_redirects <= 5:
            raise ValueError("max_redirects must be between 0 and 5")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0 and 300")


class SnapshotDownloadError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SecureSnapshotStore:
    """Build complete snapshots in a private temp dir and promote by digest."""

    def __init__(
        self,
        *,
        snapshot_root: str | Path,
        validator: ModelSnapshotValidator | None = None,
        remote_policy: RemoteSnapshotPolicy | None = None,
        resolver: Callable[..., Iterable[tuple[Any, ...]]] | None = None,
        opener: Any | None = None,
    ) -> None:
        root = Path(snapshot_root)
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        if root.is_symlink():
            raise ValueError("snapshot_root must not be a symlink")
        self._root = root.resolve(strict=True)
        self._validator = validator or ModelSnapshotValidator()
        self._remote_policy = remote_policy or RemoteSnapshotPolicy()
        self._resolver = resolver or socket.getaddrinfo
        self._opener = opener or urllib.request.build_opener(_ValidatedRedirectHandler(self))

    def import_local(self, source_root: str | Path, manifest: RestrictedModelManifest) -> VerifiedModelSnapshot:
        source = Path(source_root)
        verified_source = self._validator.validate(source, manifest)
        destination = self._root / manifest.digest
        if destination.exists():
            return self._validator.validate(destination, manifest)
        staging = Path(tempfile.mkdtemp(prefix=".restricted-stage-", dir=self._root))
        try:
            os.chmod(staging, 0o700)
            for item in manifest.files:
                source_file = verified_source.root / item.relative_path
                target = staging / item.relative_path
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _copy_verified_file(source_file, target, expected_size=item.size_bytes, expected_digest=item.sha256)
            verified = self._validator.validate(staging, manifest)
            try:
                os.replace(staging, destination)
            except OSError:
                if not destination.exists():
                    raise
            return self._validator.validate(destination, manifest) if destination.exists() else verified
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def download(
        self,
        *,
        base_url: str,
        manifest: RestrictedModelManifest,
        authorized: bool = False,
    ) -> VerifiedModelSnapshot:
        if not self._remote_policy.enabled or not authorized:
            raise SnapshotDownloadError("remote_download_denied", "remote model download is not authorized")
        if manifest.source_type != SOURCE_HUGGINGFACE_SNAPSHOT:
            raise SnapshotDownloadError(
                "source_type_mismatch",
                "manifest does not authorize a remote snapshot source",
            )
        self.validate_remote_url(base_url)
        destination = self._root / manifest.digest
        if destination.exists():
            return self._validator.validate(destination, manifest)
        staging = Path(tempfile.mkdtemp(prefix=".restricted-download-", dir=self._root))
        try:
            os.chmod(staging, 0o700)
            normalized_base = base_url.rstrip("/") + "/"
            for item in manifest.files:
                relative_url = "/".join(urllib.parse.quote(part, safe="") for part in item.relative_path.split("/"))
                url = urllib.parse.urljoin(normalized_base, relative_url)
                self.validate_remote_url(url)
                target = staging / item.relative_path
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._download_file(url, target, expected_size=item.size_bytes, expected_digest=item.sha256)
            self._validator.validate(staging, manifest)
            try:
                os.replace(staging, destination)
            except OSError:
                if not destination.exists():
                    raise
            return self._validator.validate(destination, manifest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def validate_remote_url(self, url: str) -> frozenset[str]:
        parsed = urllib.parse.urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SnapshotDownloadError("unsafe_remote_url", "remote URL must use HTTPS without credentials")
        if parsed.query or parsed.fragment:
            raise SnapshotDownloadError("unsafe_remote_url", "remote URL must not contain query or fragment data")
        host = parsed.hostname.rstrip(".").lower()
        allowed = {item.rstrip(".").lower() for item in self._remote_policy.allowed_hosts}
        if host not in allowed:
            raise SnapshotDownloadError("remote_host_not_allowed", "remote host is not allowlisted")
        try:
            addresses = self._resolver(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SnapshotDownloadError("remote_dns_failed", "remote host could not be resolved") from exc
        ips = {str(item[4][0]).split("%", 1)[0] for item in addresses}
        if not ips or any(not ipaddress.ip_address(value).is_global for value in ips):
            raise SnapshotDownloadError("remote_address_forbidden", "remote host resolved to a non-public address")
        return frozenset(ips)

    def _download_file(self, url: str, target: Path, *, expected_size: int, expected_digest: str) -> None:
        approved_ips = self.validate_remote_url(url)
        request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
        try:
            response = self._opener.open(request, timeout=self._remote_policy.timeout_seconds)
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            raise SnapshotDownloadError("download_failed", "model file download failed") from exc
        try:
            final_url_getter = getattr(response, "geturl", None)
            final_url = str(final_url_getter() if callable(final_url_getter) else url)
            final_ips = self.validate_remote_url(final_url)
            peer_ip = _response_peer_ip(response)
            if peer_ip is None:
                raise SnapshotDownloadError("remote_peer_unverified", "download peer address could not be verified")
            try:
                peer_is_global = ipaddress.ip_address(peer_ip).is_global
            except ValueError as exc:
                raise SnapshotDownloadError("remote_peer_unverified", "download peer address is invalid") from exc
            expected_ips = final_ips if final_url != url else approved_ips
            if not peer_is_global or peer_ip not in expected_ips:
                raise SnapshotDownloadError(
                    "dns_rebinding_detected",
                    "download peer differs from the approved DNS resolution",
                )
        except Exception:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            raise
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise SnapshotDownloadError("invalid_content_length", "invalid download content length") from exc
            if declared != expected_size:
                raise SnapshotDownloadError("size_mismatch", "download size differs from manifest")
        digest = hashlib.sha256()
        total = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(target, flags, 0o600)
        try:
            while True:
                chunk = response.read(min(1024 * 1024, expected_size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise SnapshotDownloadError("size_mismatch", "download exceeded manifest size")
                digest.update(chunk)
                os.write(descriptor, chunk)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if total != expected_size:
            raise SnapshotDownloadError("size_mismatch", "download was truncated")
        if digest.hexdigest() != expected_digest:
            raise SnapshotDownloadError("hash_mismatch", "download hash differs from manifest")


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, store: SecureSnapshotStore) -> None:
        super().__init__()
        self._store = store

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        redirects = int(getattr(req, "_ananta_redirects", 0)) + 1
        if redirects > self._store._remote_policy.max_redirects:
            raise SnapshotDownloadError("too_many_redirects", "download exceeded redirect limit")
        self._store.validate_remote_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            setattr(redirected, "_ananta_redirects", redirects)
        return redirected


def _response_peer_ip(response: Any) -> str | None:
    explicit = getattr(response, "peer_ip", None)
    if explicit:
        return str(explicit).split("%", 1)[0]
    current: Any = response
    for attribute in ("fp", "raw", "_sock"):
        current = getattr(current, attribute, None)
        if current is None:
            return None
    try:
        address = current.getpeername()
    except (AttributeError, OSError):
        return None
    if not isinstance(address, tuple) or not address:
        return None
    return str(address[0]).split("%", 1)[0]


def _copy_verified_file(source: Path, target: Path, *, expected_size: int, expected_digest: str) -> None:
    read_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        read_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(source, read_flags)
    target_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(source_descriptor, min(1024 * 1024, expected_size + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise ModelManifestValidationError("size_mismatch", "source changed during import")
            digest.update(chunk)
            os.write(target_descriptor, chunk)
        os.fsync(target_descriptor)
    finally:
        os.close(source_descriptor)
        os.close(target_descriptor)
    if total != expected_size or digest.hexdigest() != expected_digest:
        raise ModelManifestValidationError("hash_mismatch", "source changed during import")
