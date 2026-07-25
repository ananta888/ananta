from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlsplit


class JmapEndpointPolicyError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "jmap_endpoint_rejected")
        super().__init__(self.reason_code)


DnsResolver = Callable[[str, int], Sequence[str]]


@dataclass(frozen=True, slots=True)
class JmapEndpointPolicyConfig:
    external_network_enabled: bool = False
    local_endpoints_enabled: bool = False
    allowed_related_origins: tuple[str, ...] = ()
    allowed_local_hosts: tuple[str, ...] = ()
    allowed_local_cidrs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedJmapEndpoint:
    url: str
    origin: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]
    local: bool


_TEMPLATE_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")
_TEMPLATE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "download": frozenset({"accountId", "blobId", "name", "type"}),
    "upload": frozenset({"accountId"}),
    "event_source": frozenset({"types", "closeafter", "ping"}),
}


def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise JmapEndpointPolicyError("jmap_endpoint_dns_failed") from exc
    return tuple(sorted({str(row[4][0]).split("%", 1)[0] for row in rows}))


def _canonical_origin(scheme: str, host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{display_host}:{port}"


def _origin_from_url(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").lower()
    try:
        host = host.encode("idna").decode("ascii")
        port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    except (UnicodeError, ValueError) as exc:
        raise JmapEndpointPolicyError("jmap_endpoint_invalid") from exc
    return _canonical_origin(parsed.scheme.lower(), host, port)


class JmapEndpointPolicy:
    def __init__(
        self,
        *,
        config: JmapEndpointPolicyConfig,
        resolver: DnsResolver | None = None,
    ) -> None:
        self._config = config
        self._resolver = resolver or _system_resolver
        self._related_origins = frozenset(_origin_from_url(value) for value in config.allowed_related_origins)
        self._local_hosts = frozenset(str(value).strip().lower() for value in config.allowed_local_hosts)
        try:
            self._local_networks = tuple(ipaddress.ip_network(value, strict=True) for value in config.allowed_local_cidrs)
        except ValueError as exc:
            raise JmapEndpointPolicyError("jmap_local_cidr_invalid") from exc

    def validate_initial(self, value: str, *, purpose: str = "session") -> ValidatedJmapEndpoint:
        return self._validate(value, purpose=purpose, trusted_origin=None, template=False)

    def validate_related(
        self,
        value: str,
        *,
        trusted_origin: str,
        purpose: str,
    ) -> ValidatedJmapEndpoint:
        return self._validate(value, purpose=purpose, trusted_origin=trusted_origin, template=False)

    def validate_template(
        self,
        value: str,
        *,
        trusted_origin: str,
        purpose: str,
    ) -> ValidatedJmapEndpoint:
        required = _TEMPLATE_REQUIREMENTS.get(purpose)
        if required is None:
            raise JmapEndpointPolicyError("jmap_template_purpose_invalid")
        variables = frozenset(_TEMPLATE_RE.findall(str(value or "")))
        if variables != required:
            raise JmapEndpointPolicyError(f"jmap_{purpose}_template_variables_invalid")
        candidate = _TEMPLATE_RE.sub("template-value", str(value or ""))
        if "{" in candidate or "}" in candidate:
            raise JmapEndpointPolicyError("jmap_uri_template_invalid")
        return self._validate(candidate, purpose=purpose, trusted_origin=trusted_origin, template=True)

    def expand_template(
        self,
        template: str,
        *,
        variables: Mapping[str, object],
        trusted_origin: str,
        purpose: str,
    ) -> ValidatedJmapEndpoint:
        self.validate_template(template, trusted_origin=trusted_origin, purpose=purpose)
        required = _TEMPLATE_REQUIREMENTS[purpose]
        if frozenset(variables) != required:
            raise JmapEndpointPolicyError(f"jmap_{purpose}_template_values_invalid")

        def replace(match: re.Match[str]) -> str:
            raw = str(variables[match.group(1)])
            if not raw:
                raise JmapEndpointPolicyError(f"jmap_{purpose}_template_value_empty")
            return quote(raw, safe="")

        expanded = _TEMPLATE_RE.sub(replace, template)
        return self.validate_related(expanded, trusted_origin=trusted_origin, purpose=purpose)

    def validate_redirect(
        self,
        location: str,
        *,
        current_url: str,
        trusted_origin: str,
    ) -> ValidatedJmapEndpoint:
        target = urljoin(current_url, str(location or ""))
        return self.validate_related(target, trusted_origin=trusted_origin, purpose="session")

    def _validate(
        self,
        value: str,
        *,
        purpose: str,
        trusted_origin: str | None,
        template: bool,
    ) -> ValidatedJmapEndpoint:
        raw = str(value or "").strip()
        if not raw:
            raise JmapEndpointPolicyError("jmap_endpoint_required")
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in {"https", "http"}:
            raise JmapEndpointPolicyError("jmap_endpoint_scheme_forbidden")
        if parsed.username is not None or parsed.password is not None:
            raise JmapEndpointPolicyError("jmap_endpoint_userinfo_forbidden")
        if parsed.fragment:
            raise JmapEndpointPolicyError("jmap_endpoint_fragment_forbidden")
        if parsed.query and purpose in {"session", "api"} and not template:
            raise JmapEndpointPolicyError("jmap_endpoint_query_forbidden")
        host = str(parsed.hostname or "").strip().lower()
        if not host:
            raise JmapEndpointPolicyError("jmap_endpoint_host_required")
        try:
            host = host.encode("idna").decode("ascii")
            port = int(parsed.port or (443 if scheme == "https" else 80))
        except (UnicodeError, ValueError) as exc:
            raise JmapEndpointPolicyError("jmap_endpoint_invalid") from exc
        if not 1 <= port <= 65535:
            raise JmapEndpointPolicyError("jmap_endpoint_port_invalid")
        origin = _canonical_origin(scheme, host, port)
        if trusted_origin is not None:
            canonical_trusted = _origin_from_url(trusted_origin)
            if origin != canonical_trusted and origin not in self._related_origins:
                raise JmapEndpointPolicyError("jmap_related_origin_not_allowlisted")
        try:
            literal = ipaddress.ip_address(host)
            addresses = (str(literal),)
        except ValueError:
            addresses = tuple(sorted(set(self._resolver(host, port))))
        if not addresses:
            raise JmapEndpointPolicyError("jmap_endpoint_dns_empty")
        try:
            parsed_addresses = tuple(ipaddress.ip_address(value) for value in addresses)
        except ValueError as exc:
            raise JmapEndpointPolicyError("jmap_endpoint_dns_invalid") from exc
        public = all(address.is_global for address in parsed_addresses)
        local_allowed = (
            self._config.local_endpoints_enabled
            and host in self._local_hosts
            and bool(self._local_networks)
            and all(any(address in network for network in self._local_networks) for address in parsed_addresses)
        )
        if not public and not local_allowed:
            raise JmapEndpointPolicyError("jmap_endpoint_address_forbidden")
        if public and not self._config.external_network_enabled:
            raise JmapEndpointPolicyError("jmap_external_network_disabled")
        if scheme != "https" and not local_allowed:
            raise JmapEndpointPolicyError("jmap_https_required")
        return ValidatedJmapEndpoint(
            url=raw,
            origin=origin,
            scheme=scheme,
            host=host,
            port=port,
            addresses=tuple(str(value) for value in parsed_addresses),
            local=not public,
        )


__all__ = [
    "DnsResolver",
    "JmapEndpointPolicy",
    "JmapEndpointPolicyConfig",
    "JmapEndpointPolicyError",
    "ValidatedJmapEndpoint",
]
