"""Canonical endpoint binding and provider-aware egress policy."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

LOCAL_PROVIDER_IDS = frozenset(
    {
        "koboldcpp",
        "llamacpp",
        "lm_studio",
        "lmstudio",
        "local",
        "local_mock",
        "mock",
        "ollama",
        "openai_compatible",
        "textgen_webui",
    }
)

_SHARED_LOCAL_HOSTS = frozenset({"localhost", "host.docker.internal"})
_PROVIDER_SERVICE_HOSTS: dict[str, frozenset[str]] = {
    "koboldcpp": frozenset({"koboldcpp"}),
    "llamacpp": frozenset({"llamacpp", "llama-cpp"}),
    "lm_studio": frozenset({"lmstudio", "lm-studio"}),
    "lmstudio": frozenset({"lmstudio", "lm-studio"}),
    "local": frozenset({"local"}),
    "local_mock": frozenset({"local-mock", "local_mock"}),
    "mock": frozenset({"mock"}),
    "ollama": frozenset({"ollama"}),
    "openai_compatible": frozenset(
        {"openai-compatible", "openai_compatible"}
    ),
    "textgen_webui": frozenset({"textgen-webui", "textgen_webui"}),
}
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_ALLOWED_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
ProviderEndpointResolver = Callable[
    ..., Sequence[tuple[Any, ...]]
]


def normalize_provider_endpoint_identity(
    *,
    provider_id: str,
    endpoint_url: str,
) -> str:
    """Return the actual canonical provider API endpoint."""

    return build_provider_request_url(
        provider_id=provider_id,
        endpoint_url=endpoint_url,
    )


def build_provider_request_url(
    *,
    provider_id: str,
    endpoint_url: str,
) -> str:
    """Build the one request URL shared by signer, Hub and Worker.

    Hub profile URLs commonly identify an API base such as ``/v1`` while the
    transport calls ``/v1/chat/completions``. Normalizing both sides to the
    actual call target lets the Hub sign scheme, host, port and path exactly.
    Ambiguous custom paths are denied instead of being expanded differently by
    individual transports.
    """

    provider = str(provider_id or "").strip().lower()
    raw_endpoint = str(endpoint_url or "").strip()
    if len(raw_endpoint) > 1024 or "\x00" in raw_endpoint:
        raise ValueError("provider_endpoint_identity_invalid")
    parsed = urlsplit(raw_endpoint)
    if (
        not provider
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider_endpoint_identity_invalid")
    scheme = parsed.scheme.lower()
    host = _canonical_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("provider_endpoint_identity_invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("provider_endpoint_identity_invalid")
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None

    decoded_path = unquote(parsed.path or "")
    segments = decoded_path.split("/")
    if (
        "\\" in decoded_path
        or any(ord(value) < 32 for value in decoded_path)
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ValueError("provider_endpoint_identity_invalid")
    path = "/" + "/".join(segment for segment in segments if segment)
    path = "" if path == "/" else path.rstrip("/")
    if provider == "anthropic":
        if path in {"", "/v1"}:
            path = "/v1/messages"
        elif path != "/v1/messages":
            raise ValueError("provider_endpoint_path_unsupported")
    elif provider == "ollama" and path.endswith("/api/generate"):
        if path != "/api/generate":
            raise ValueError("provider_endpoint_path_unsupported")
    elif path.endswith("/chat/completions"):
        if not path.removesuffix("/chat/completions").endswith("/v1"):
            raise ValueError("provider_endpoint_path_unsupported")
    elif path:
        if not path.endswith("/v1"):
            raise ValueError("provider_endpoint_path_unsupported")
        path = f"{path}/chat/completions"
    else:
        if not path:
            path = "/v1"
        path = f"{path}/chat/completions"

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        authority_host = host
    else:
        authority_host = (
            f"[{address.compressed}]"
            if address.version == 6
            else address.compressed
        )
    authority = (
        f"{authority_host}:{port}" if port is not None else authority_host
    )
    identity = f"{scheme}://{authority}{path}"
    if len(identity) > 1024:
        raise ValueError("provider_endpoint_identity_invalid")
    return identity


def provider_endpoint_matches(
    *,
    provider_id: str,
    endpoint_url: str,
    expected_identity: str,
) -> bool:
    try:
        actual = normalize_provider_endpoint_identity(
            provider_id=provider_id,
            endpoint_url=endpoint_url,
        )
    except ValueError:
        return False
    return actual == str(expected_identity or "").strip()


def is_forbidden_provider_endpoint_target(endpoint_url: str) -> bool:
    """Deny metadata and non-routable literal targets under every policy."""

    try:
        host = _canonical_host(urlsplit(endpoint_url).hostname)
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address in _METADATA_ADDRESSES
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or (
            address.is_private
            and not address.is_loopback
            and not any(
                address in network
                for network in _ALLOWED_PRIVATE_NETWORKS
                if address.version == network.version
            )
        )
    )


def is_local_provider_endpoint(
    *,
    provider_id: str,
    endpoint_url: str,
    endpoint_bound: bool = False,
) -> bool:
    """Classify a local endpoint without trusting ambient DNS aliases."""

    provider = str(provider_id or "").strip().lower()
    if provider not in LOCAL_PROVIDER_IDS:
        return False
    try:
        identity = normalize_provider_endpoint_identity(
            provider_id=provider,
            endpoint_url=endpoint_url,
        )
    except ValueError:
        return False
    if is_forbidden_provider_endpoint_target(identity):
        return False
    host = _canonical_host(urlsplit(identity).hostname)
    if host in _SHARED_LOCAL_HOSTS:
        return True
    if host in _PROVIDER_SERVICE_HOSTS.get(provider, frozenset()):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return bool(endpoint_bound and address.is_private)


def is_legacy_compatible_provider_endpoint(
    *,
    provider_id: str,
    endpoint_url: str,
) -> bool:
    """Narrow compatibility path for persisted contexts without a binding."""

    provider = str(provider_id or "").strip().lower()
    try:
        identity = normalize_provider_endpoint_identity(
            provider_id=provider,
            endpoint_url=endpoint_url,
        )
    except ValueError:
        return False
    defaults = {
        "ollama": {
            "http://ollama:11434/v1/chat/completions",
            "http://localhost:11434/v1/chat/completions",
            "http://127.0.0.1:11434/v1/chat/completions",
        },
        "lmstudio": {
            "http://host.docker.internal:1234/v1/chat/completions",
            "http://localhost:1234/v1/chat/completions",
            "http://127.0.0.1:1234/v1/chat/completions",
        },
        "lm_studio": {
            "http://host.docker.internal:1234/v1/chat/completions",
            "http://localhost:1234/v1/chat/completions",
            "http://127.0.0.1:1234/v1/chat/completions",
        },
    }
    return identity in defaults.get(provider, set())


def validate_provider_endpoint_resolution(
    *,
    provider_id: str,
    endpoint_url: str,
    endpoint_bound: bool = False,
    resolver: ProviderEndpointResolver | None = None,
) -> tuple[str, ...]:
    """Resolve external DNS once and reject every non-public answer.

    Signed RFC1918 literals and exact local service aliases are deliberate Hub
    capabilities and do not use ambient DNS authorization. Arbitrary DNS names
    for local providers fail closed. Public external providers must resolve to
    a non-empty set containing only globally routable A/AAAA addresses.

    The returned set is suitable for a transport that supports address
    pinning. The current requests-based HTTPS Hub transport validates this set
    but still delegates SNI/certificate handling to the library, so callers
    must not treat this helper alone as DNS pinning.
    """

    provider = str(provider_id or "").strip().lower()
    identity = build_provider_request_url(
        provider_id=provider,
        endpoint_url=endpoint_url,
    )
    parsed = urlsplit(identity)
    host = _canonical_host(parsed.hostname)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        explicitly_local = bool(
            provider in LOCAL_PROVIDER_IDS
            and not is_forbidden_provider_endpoint_target(identity)
            and (
                address.is_loopback
                or any(
                    address in network
                    for network in _ALLOWED_PRIVATE_NETWORKS
                    if address.version == network.version
                )
            )
        )
        legacy_local = bool(
            explicitly_local
            and is_legacy_compatible_provider_endpoint(
                provider_id=provider,
                endpoint_url=identity,
            )
        )
        if not address.is_global and not (
            explicitly_local
            and (endpoint_bound or legacy_local)
        ):
            raise ValueError(
                "provider_endpoint_non_global_literal_denied"
            )
        return (address.compressed,)
    if _is_known_local_alias(provider, host):
        return ()
    if provider in LOCAL_PROVIDER_IDS:
        raise ValueError("provider_endpoint_dns_name_denied")

    resolve = resolver or socket.getaddrinfo
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        answers = resolve(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            "provider_endpoint_resolution_failed"
        ) from exc
    resolved: set[str] = set()
    for answer in answers:
        try:
            sockaddr = answer[4]
            candidate = ipaddress.ip_address(str(sockaddr[0]))
        except (IndexError, TypeError, ValueError):
            raise ValueError(
                "provider_endpoint_resolution_invalid"
            ) from None
        if not candidate.is_global:
            raise ValueError(
                "provider_endpoint_resolution_denied"
            )
        resolved.add(candidate.compressed)
    if not resolved:
        raise ValueError("provider_endpoint_resolution_failed")
    return tuple(sorted(resolved))


def _is_known_local_alias(provider: str, host: str) -> bool:
    if provider not in LOCAL_PROVIDER_IDS:
        return False
    return bool(
        host in _SHARED_LOCAL_HOSTS
        or host in _PROVIDER_SERVICE_HOSTS.get(provider, frozenset())
    )


def _canonical_host(raw_host: str | None) -> str:
    host = str(raw_host or "").strip().lower().rstrip(".")
    if (
        not host
        or any(ord(value) <= 32 for value in host)
        or any(value in host for value in "/\\?#")
    ):
        raise ValueError("provider_endpoint_identity_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # libc accepts historical integer, hexadecimal, octal and short-dot
        # IPv4 forms that ``ipaddress`` deliberately rejects.  Treating those
        # as DNS names would let e.g. 2852039166 reach 169.254.169.254.
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise ValueError("provider_endpoint_identity_invalid")
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(
                "provider_endpoint_identity_invalid"
            ) from exc
    if "%" in host or (
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
    ):
        raise ValueError("provider_endpoint_identity_invalid")
    return address.compressed


__all__ = [
    "LOCAL_PROVIDER_IDS",
    "build_provider_request_url",
    "is_forbidden_provider_endpoint_target",
    "is_legacy_compatible_provider_endpoint",
    "is_local_provider_endpoint",
    "normalize_provider_endpoint_identity",
    "provider_endpoint_matches",
    "validate_provider_endpoint_resolution",
]
