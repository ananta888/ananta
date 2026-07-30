"""Fail-closed Git remote, credential-reference and egress policy."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

_CREDENTIAL_REFERENCE = re.compile(
    r"^(?:vault|secret)://[A-Za-z0-9][A-Za-z0-9._/-]{2,255}$"
)
_SCP_REMOTE = re.compile(
    r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^?#]+)$"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DEFAULT_ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")
_HARDENED_GIT_CONFIG = (
    "-c",
    "http.followRedirects=false",
    "-c",
    "http.proxy=",
    "-c",
    "https.proxy=",
    "-c",
    "core.gitProxy=",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "submodule.recurse=false",
    "-c",
    "fetch.recurseSubmodules=false",
    "-c",
    "credential.helper=",
    "-c",
    "credential.interactive=never",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.required=false",
)


class GitRemotePolicyError(ValueError):
    """Typed, content-free policy rejection."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class GitCredentialStatusPort(Protocol):
    def status(self, credential_ref: str) -> str: ...


@dataclass(frozen=True)
class GitRemotePolicyRequest:
    remote_url: str
    operation: str
    credential_ref: str | None = None
    allow_redirects: bool = False
    redirect_url: str | None = None
    proxy_url: str | None = None
    recurse_submodules: bool = False
    lfs_mode: str = "pointer_only"


@dataclass(frozen=True)
class AuthorizedGitRemote:
    canonical_url: str
    redacted_url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]
    credential_ref: str | None


@dataclass(frozen=True)
class GitTransportAuthorization:
    """Secret-free, immutable input for one concrete Git network operation.

    A transport must connect only to ``validated_ips`` while preserving
    ``host`` for TLS SNI, certificate checks, SSH host verification, and the
    HTTP Host header. It must not resolve ``canonical_url`` again, follow a
    redirect, use a proxy, recurse into submodules, or fetch LFS payloads.
    ``credential_ref`` is an opaque server-side lookup key, never credential
    material and never an argv, log, or result value.
    """

    canonical_url: str
    scheme: str
    host: str
    port: int
    validated_ips: tuple[str, ...]
    operation: str
    redirects: str
    proxy: str
    recurse_submodules: bool
    lfs_mode: str
    credential_ref: str | None
    requires_dns_ip_pinning: bool
    authorization_digest: str

    @classmethod
    def create(
        cls,
        *,
        authorized: AuthorizedGitRemote,
        request: GitRemotePolicyRequest,
    ) -> "GitTransportAuthorization":
        coordinates = {
            "canonical_url": authorized.canonical_url,
            "scheme": authorized.scheme,
            "host": authorized.host,
            "port": authorized.port,
            "validated_ips": list(authorized.resolved_ips),
            "operation": str(request.operation).strip().lower(),
            "redirects": "deny",
            "proxy": "deny",
            "recurse_submodules": bool(request.recurse_submodules),
            "lfs_mode": str(request.lfs_mode).strip().lower(),
            "credential_ref": authorized.credential_ref,
            "requires_dns_ip_pinning": True,
        }
        return cls(
            **{
                **coordinates,
                "validated_ips": tuple(authorized.resolved_ips),
            },
            authorization_digest=_transport_digest(coordinates),
        )

    def validate(self) -> None:
        coordinates = self.coordinates()
        if (
            self.redirects != "deny"
            or self.proxy != "deny"
            or self.recurse_submodules
            or self.lfs_mode not in {"disabled", "pointer_only"}
            or self.operation
            not in {"clone", "fetch", "pull", "push", "configure"}
            or self.scheme not in {"https", "ssh"}
            or not self.requires_dns_ip_pinning
            or not self.validated_ips
            or _transport_digest(coordinates)
            != self.authorization_digest
        ):
            raise GitRemotePolicyError(
                "git_transport_authorization_invalid"
            )
        parsed = GitRemoteAccessPolicy._parse_remote_url(
            self.canonical_url
        )
        if (
            str(parsed.scheme).lower() != self.scheme
            or GitRemoteAccessPolicy._normalize_host(
                parsed.hostname or ""
            )
            != self.host
            or int(
                parsed.port
                or (443 if self.scheme == "https" else 22)
            )
            != self.port
        ):
            raise GitRemotePolicyError(
                "git_transport_authorization_invalid"
            )
        for value in self.validated_ips:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise GitRemotePolicyError(
                    "git_transport_authorization_invalid"
                ) from exc
            if not address.is_global:
                raise GitRemotePolicyError(
                    "git_transport_authorization_invalid"
                )

    def coordinates(self) -> dict[str, object]:
        return {
            "canonical_url": self.canonical_url,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "validated_ips": list(self.validated_ips),
            "operation": self.operation,
            "redirects": self.redirects,
            "proxy": self.proxy,
            "recurse_submodules": self.recurse_submodules,
            "lfs_mode": self.lfs_mode,
            "credential_ref": self.credential_ref,
            "requires_dns_ip_pinning": (
                self.requires_dns_ip_pinning
            ),
        }

    def __repr__(self) -> str:
        return (
            "GitTransportAuthorization("
            f"host={self.host!r}, port={self.port!r}, "
            f"operation={self.operation!r}, "
            f"validated_ips={self.validated_ips!r}, "
            "credential_ref=<opaque>, "
            f"authorization_digest={self.authorization_digest!r})"
        )


class GitRemoteAccessPolicyPort(Protocol):
    def authorize(self, request: GitRemotePolicyRequest) -> AuthorizedGitRemote: ...


def hardened_git_network_args(arguments: Iterable[str]) -> list[str]:
    return [*_HARDENED_GIT_CONFIG, *[str(item) for item in arguments]]


def hardened_git_transport_args(
    authorization: GitTransportAuthorization,
    arguments: Iterable[str],
    *,
    remote_name: str | None = None,
) -> list[str]:
    """Bind one Git CLI invocation to the policy-authorized endpoint.

    Git/libcurl fails the invocation when ``http.curloptResolve`` is not
    supported. SSH is forced through OpenSSH with an IP ``Hostname`` and has
    no DNS-based fallback.
    """

    authorization.validate()
    address = ipaddress.ip_address(authorization.validated_ips[0])
    transport_config: list[str]
    if authorization.scheme == "https":
        rendered_address = (
            f"[{address.compressed}]"
            if address.version == 6
            else address.compressed
        )
        transport_config = [
            "-c",
            "http.curloptResolve=",
            "-c",
            (
                "http.curloptResolve="
                f"{authorization.host}:{authorization.port}:"
                f"{rendered_address}"
            ),
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.proxy=",
            "-c",
            "http.sslVerify=true",
            "-c",
            "http.extraHeader=",
        ]
    else:
        host_key_alias = (
            authorization.host
            if authorization.port == 22
            else f"[{authorization.host}]:{authorization.port}"
        )
        ssh_command = shlex.join(
            [
                "ssh",
                "-F",
                os.devnull,
                "-o",
                f"Hostname={address.compressed}",
                "-o",
                f"HostKeyAlias={host_key_alias}",
                "-o",
                "CheckHostIP=no",
                "-o",
                "CanonicalizeHostname=no",
                "-o",
                "ProxyCommand=none",
                "-o",
                "ProxyJump=none",
                "-o",
                "StrictHostKeyChecking=yes",
            ]
        )
        transport_config = ["-c", f"core.sshCommand={ssh_command}"]

    if remote_name is not None:
        normalized_remote = str(remote_name).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", normalized_remote):
            raise GitRemotePolicyError(
                "git_transport_remote_name_invalid"
            )
        transport_config.extend(
            [
                "-c",
                (
                    f"remote.{normalized_remote}.url="
                    f"{authorization.canonical_url}"
                ),
                "-c",
                f"remote.{normalized_remote}.proxy=",
            ]
        )
        if authorization.operation == "push":
            transport_config.extend(
                [
                    "-c",
                    (
                        f"remote.{normalized_remote}.pushurl="
                        f"{authorization.canonical_url}"
                    ),
                ]
            )

    return hardened_git_network_args(
        [*transport_config, *[str(item) for item in arguments]]
    )


def hardened_git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_LFS_SKIP_PUSH": "1",
        "GIT_SSL_NO_VERIFY": "0",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "http_proxy": "",
        "https_proxy": "",
        "all_proxy": "",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def _transport_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class GitRemoteAccessPolicy:
    """Authorize one immutable Git remote projection before network execution."""

    def __init__(
        self,
        *,
        allowed_schemes: Iterable[str] = ("https", "ssh"),
        allowed_hosts: Iterable[str] | None = None,
        allowed_lfs_modes: Iterable[str] = ("disabled", "pointer_only"),
        allow_submodules: bool = False,
        credential_status: GitCredentialStatusPort | None = None,
        dns_resolver: Callable[[str, int], Iterable[str]] | None = None,
    ) -> None:
        configured_hosts = (
            tuple(allowed_hosts)
            if allowed_hosts is not None
            else tuple(
                item.strip()
                for item in str(
                    os.environ.get("ANANTA_GIT_REMOTE_ALLOWED_HOSTS") or ""
                ).split(",")
                if item.strip()
            )
            or _DEFAULT_ALLOWED_HOSTS
        )
        self._allowed_schemes = frozenset(
            str(item).strip().lower() for item in allowed_schemes if str(item).strip()
        )
        self._allowed_hosts = tuple(
            self._normalize_host_pattern(item) for item in configured_hosts
        )
        self._allowed_lfs_modes = frozenset(
            str(item).strip().lower()
            for item in allowed_lfs_modes
            if str(item).strip()
        )
        self._allow_submodules = bool(allow_submodules)
        self._credential_status = credential_status
        self._dns_resolver = dns_resolver or self._resolve_dns

    def authorize(self, request: GitRemotePolicyRequest) -> AuthorizedGitRemote:
        operation = str(request.operation or "").strip().lower()
        if operation not in {"clone", "fetch", "pull", "push", "configure"}:
            raise GitRemotePolicyError("git_remote_operation_denied")
        if request.allow_redirects or request.redirect_url:
            raise GitRemotePolicyError("git_remote_redirect_denied")
        if request.proxy_url:
            raise GitRemotePolicyError("git_remote_proxy_denied")
        if request.recurse_submodules and not self._allow_submodules:
            raise GitRemotePolicyError("git_remote_submodule_denied")
        lfs_mode = str(request.lfs_mode or "").strip().lower()
        if lfs_mode not in self._allowed_lfs_modes:
            raise GitRemotePolicyError("git_remote_lfs_mode_denied")

        parsed = self._parse_remote_url(request.remote_url)
        scheme = str(parsed.scheme or "").lower()
        if scheme not in self._allowed_schemes:
            raise GitRemotePolicyError("git_remote_scheme_denied")
        host = self._normalize_host(parsed.hostname or "")
        if not self._host_allowed(host):
            raise GitRemotePolicyError("git_remote_host_denied")
        self._validate_url_credentials(parsed, scheme=scheme)
        credential_ref = self._validate_credential_reference(request.credential_ref)
        port = int(parsed.port or (443 if scheme == "https" else 22))
        first_resolution = self._validated_resolution(host, port)
        second_resolution = self._validated_resolution(host, port)
        if first_resolution != second_resolution:
            raise GitRemotePolicyError("git_remote_dns_rebinding_detected")

        canonical_url = urlunsplit(
            (
                scheme,
                self._canonical_netloc(parsed, scheme=scheme, host=host, port=port),
                parsed.path,
                "",
                "",
            )
        )
        return AuthorizedGitRemote(
            canonical_url=canonical_url,
            redacted_url=canonical_url,
            scheme=scheme,
            host=host,
            port=port,
            resolved_ips=first_resolution,
            credential_ref=credential_ref,
        )

    @staticmethod
    def _parse_remote_url(value: str) -> SplitResult:
        raw = str(value or "").strip()
        if not raw or _CONTROL_CHARACTERS.search(raw):
            raise GitRemotePolicyError("git_remote_url_invalid")
        scp_match = _SCP_REMOTE.fullmatch(raw)
        if scp_match is not None:
            raw = (
                f"ssh://{scp_match.group('user')}@{scp_match.group('host')}/"
                f"{scp_match.group('path').lstrip('/')}"
            )
        try:
            parsed = urlsplit(raw)
            _ = parsed.port
        except ValueError as exc:
            raise GitRemotePolicyError("git_remote_url_invalid") from exc
        if (
            not parsed.scheme
            or not parsed.hostname
            or not parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise GitRemotePolicyError("git_remote_url_invalid")
        return parsed

    @staticmethod
    def _validate_url_credentials(parsed: SplitResult, *, scheme: str) -> None:
        if parsed.password is not None:
            raise GitRemotePolicyError("git_remote_url_credentials_denied")
        username = str(parsed.username or "")
        if scheme == "https" and username:
            raise GitRemotePolicyError("git_remote_url_credentials_denied")
        if scheme == "ssh" and username not in {"", "git"}:
            raise GitRemotePolicyError("git_remote_ssh_identity_denied")

    def _validate_credential_reference(self, value: str | None) -> str | None:
        credential_ref = str(value or "").strip() or None
        if credential_ref is None:
            return None
        if _CREDENTIAL_REFERENCE.fullmatch(credential_ref) is None:
            raise GitRemotePolicyError("git_credential_reference_invalid")
        if self._credential_status is None:
            raise GitRemotePolicyError("git_credential_status_unavailable")
        status = str(self._credential_status.status(credential_ref) or "").strip().lower()
        if status == "revoked":
            raise GitRemotePolicyError("git_credential_revoked")
        if status != "active":
            raise GitRemotePolicyError("git_credential_unavailable")
        return credential_ref

    def _validated_resolution(self, host: str, port: int) -> tuple[str, ...]:
        try:
            raw_addresses = tuple(self._dns_resolver(host, port))
        except (OSError, socket.gaierror) as exc:
            raise GitRemotePolicyError("git_remote_dns_resolution_failed") from exc
        addresses: set[str] = set()
        for value in raw_addresses:
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError as exc:
                raise GitRemotePolicyError("git_remote_dns_result_invalid") from exc
            if not address.is_global:
                raise GitRemotePolicyError("git_remote_address_denied")
            addresses.add(address.compressed)
        if not addresses:
            raise GitRemotePolicyError("git_remote_dns_resolution_failed")
        return tuple(sorted(addresses))

    @staticmethod
    def _resolve_dns(host: str, port: int) -> Iterable[str]:
        return {
            str(sockaddr[0])
            for _family, _type, _protocol, _canonical, sockaddr in socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        }

    @staticmethod
    def _normalize_host(value: str) -> str:
        raw = str(value or "").strip().rstrip(".").lower()
        if not raw:
            raise GitRemotePolicyError("git_remote_host_invalid")
        try:
            normalized = raw.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise GitRemotePolicyError("git_remote_host_invalid") from exc
        if (
            len(normalized) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label) is None
                for label in normalized.split(".")
            )
        ):
            raise GitRemotePolicyError("git_remote_host_invalid")
        return normalized

    @classmethod
    def _normalize_host_pattern(cls, value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw.startswith("*."):
            return f"*.{cls._normalize_host(raw[2:])}"
        return cls._normalize_host(raw)

    def _host_allowed(self, host: str) -> bool:
        return any(
            host == pattern
            or (
                pattern.startswith("*.")
                and host.endswith(pattern[1:])
                and host != pattern[2:]
            )
            for pattern in self._allowed_hosts
        )

    @staticmethod
    def _canonical_netloc(
        parsed: SplitResult,
        *,
        scheme: str,
        host: str,
        port: int,
    ) -> str:
        username = "git@" if scheme == "ssh" and parsed.username == "git" else ""
        default_port = 443 if scheme == "https" else 22
        rendered_host = f"[{host}]" if ":" in host else host
        rendered_port = f":{port}" if port != default_port else ""
        return f"{username}{rendered_host}{rendered_port}"


_DEFAULT_POLICY: GitRemoteAccessPolicy | None = None


def get_git_remote_access_policy() -> GitRemoteAccessPolicy:
    global _DEFAULT_POLICY
    if _DEFAULT_POLICY is None:
        _DEFAULT_POLICY = GitRemoteAccessPolicy()
    return _DEFAULT_POLICY


__all__ = [
    "AuthorizedGitRemote",
    "GitCredentialStatusPort",
    "GitRemoteAccessPolicy",
    "GitRemoteAccessPolicyPort",
    "GitRemotePolicyError",
    "GitRemotePolicyRequest",
    "GitTransportAuthorization",
    "get_git_remote_access_policy",
    "hardened_git_environment",
    "hardened_git_network_args",
    "hardened_git_transport_args",
]
