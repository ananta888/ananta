"""Pinned TURN/STUN endpoint policy and candidate privacy boundary."""

from __future__ import annotations

import hashlib
import ipaddress
import hmac
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol


class WebrtcTurnEndpointPolicyError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnEndpointRule:
    url: str
    scheme: str
    host: str
    port: int
    transport: str
    region: str
    consumer: str
    pinned_ips: tuple[str, ...]
    allow_private: bool = False
    tls_spki_sha256: tuple[str, ...] = ()


class TurnDnsResolverPort(Protocol):
    def resolve(self, host: str) -> tuple[str, ...]: ...


class StaticTurnDnsResolver:
    def __init__(self, values: dict[str, tuple[str, ...]]) -> None:
        self._values = dict(values)

    def resolve(self, host: str) -> tuple[str, ...]:
        return self._values.get(host, ())


@dataclass(frozen=True, slots=True)
class TurnEndpointDecision:
    allowed: bool
    reason_code: str
    url: str | None
    transport: str | None
    candidate_policy: str


class WebrtcTurnEndpointPolicy:
    _URL = re.compile(
        r"^(?P<scheme>stun|stuns|turn|turns):(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+):(?P<port>[0-9]{1,5})(?:\?transport=(?P<transport>udp|tcp))?$",
        re.ASCII,
    )

    def __init__(
        self,
        rules: tuple[TurnEndpointRule, ...],
        *,
        resolver: TurnDnsResolverPort,
        diagnostic_secret: bytes,
        pseudonym_rotation_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not rules or len(rules) > 128:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_catalog_invalid")
        if len(diagnostic_secret) < 32 or pseudonym_rotation_seconds <= 0:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_diagnostic_policy_invalid")
        self._resolver = resolver
        self._diagnostic_secret = bytes(diagnostic_secret)
        self._pseudonym_rotation_seconds = pseudonym_rotation_seconds
        self._clock = clock
        self._rules: dict[tuple[str, str, int, str, str, str], TurnEndpointRule] = {}
        for rule in rules:
            parsed = self._parse(rule.url)
            expected = (rule.scheme, rule.host, rule.port, rule.transport)
            if parsed != expected or rule.consumer not in {"peer", "livekit"}:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_catalog_invalid")
            if not re.fullmatch(r"[a-z0-9-]{1,32}", rule.region) or not rule.pinned_ips:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_catalog_invalid")
            for pinned in rule.pinned_ips:
                self._validate_ip(pinned, allow_private=rule.allow_private)
            if rule.scheme in {"stuns", "turns"} and not rule.tls_spki_sha256:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_tls_pin_required")
            key = (*expected, rule.region, rule.consumer)
            if key in self._rules:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_catalog_invalid")
            self._rules[key] = rule

    def authorize(
        self,
        url: str,
        *,
        region: str,
        consumer: str,
        observed_tls_spki_sha256: str | None = None,
        redirect_count: int = 0,
        relay_only: bool = False,
    ) -> TurnEndpointDecision:
        if redirect_count != 0:
            return self._deny("turn_endpoint_redirect_forbidden", relay_only)
        try:
            scheme, host, port, transport = self._parse(url)
        except WebrtcTurnEndpointPolicyError as exc:
            return self._deny(exc.reason_code, relay_only)
        rule = self._rules.get((scheme, host, port, transport, region, consumer))
        if rule is None or rule.url != url:
            return self._deny("turn_endpoint_not_allowlisted", relay_only)
        if scheme in {"stuns", "turns"} and observed_tls_spki_sha256 not in rule.tls_spki_sha256:
            return self._deny("turn_endpoint_tls_certificate_untrusted", relay_only)
        resolved = self._resolved(host)
        if not resolved or len(resolved) > 8:
            return self._deny("turn_endpoint_dns_unavailable", relay_only)
        try:
            for address in resolved:
                self._validate_ip(address, allow_private=rule.allow_private)
        except WebrtcTurnEndpointPolicyError as exc:
            return self._deny(exc.reason_code, relay_only)
        if not set(resolved).issubset(set(rule.pinned_ips)):
            return self._deny("turn_endpoint_dns_pin_mismatch", relay_only)
        return TurnEndpointDecision(
            True,
            "turn_endpoint_allowed",
            rule.url,
            rule.transport,
            "relay_only_no_host_candidates" if relay_only else "privacy_redacted_candidates",
        )

    def redact_candidate(self, candidate: str, *, scope_id: str) -> dict[str, str]:
        if not isinstance(candidate, str) or len(candidate.encode("utf-8")) > 2048:
            raise WebrtcTurnEndpointPolicyError("turn_candidate_invalid")
        if (
            not isinstance(scope_id, str)
            or not scope_id
            or len(scope_id.encode("utf-8")) > 128
            or any(ord(char) < 32 for char in scope_id)
        ):
            raise WebrtcTurnEndpointPolicyError("turn_candidate_scope_invalid")
        normalized = candidate.casefold().split()
        candidate_type = "unknown"
        if "typ" in normalized:
            index = normalized.index("typ") + 1
            if index < len(normalized) and normalized[index] in {"host", "srflx", "prflx", "relay"}:
                candidate_type = normalized[index]
        transport = normalized[2] if len(normalized) > 2 and normalized[2] in {"udp", "tcp"} else "unknown"
        family = "unknown"
        for part in normalized:
            try:
                family = "ipv6" if ipaddress.ip_address(part).version == 6 else "ipv4"
                break
            except ValueError:
                continue
        epoch = int(self._clock()) // self._pseudonym_rotation_seconds
        message = (
            b"turn-candidate-diagnostic-v1\0"
            + str(epoch).encode("ascii")
            + b"\0"
            + scope_id.encode("utf-8")
            + b"\0"
            + candidate.encode("utf-8")
        )
        diagnostic_ref = hmac.new(
            self._diagnostic_secret,
            message,
            hashlib.sha256,
        ).hexdigest()[:24]
        return {
            "candidate_class": candidate_type,
            "transport": transport,
            "address_family": family,
            "diagnostic_ref": f"tcd1.{epoch}.{diagnostic_ref}",
        }

    @classmethod
    def _parse(cls, url: str) -> tuple[str, str, int, str]:
        if not isinstance(url, str) or len(url) > 512 or not url.isascii() or "@" in url or "#" in url:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_url_invalid")
        match = cls._URL.fullmatch(url)
        if match is None:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_url_invalid")
        scheme = match.group("scheme")
        raw_host = match.group("host")
        host = raw_host[1:-1].lower() if raw_host.startswith("[") else raw_host.lower()
        if raw_host != (f"[{host}]" if raw_host.startswith("[") else host):
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_host_not_canonical")
        port = int(match.group("port"))
        if not 1 <= port <= 65535:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_port_invalid")
        query_transport = match.group("transport")
        if scheme in {"stuns", "turns"}:
            if query_transport is not None:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_transport_invalid")
            transport = "tls"
        else:
            transport = query_transport or "udp"
        return scheme, host, port, transport

    def _resolved(self, host: str) -> tuple[str, ...]:
        try:
            address = ipaddress.ip_address(host)
            if str(address) != host:
                raise WebrtcTurnEndpointPolicyError("turn_endpoint_ip_not_canonical")
            return (host,)
        except ValueError:
            return tuple(self._resolver.resolve(host))

    @staticmethod
    def _validate_ip(value: str, *, allow_private: bool) -> None:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_ip_invalid") from exc
        if str(address) != value:
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_ip_not_canonical")
        never_allowed = (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        )
        if never_allowed or (address.is_private and not allow_private):
            raise WebrtcTurnEndpointPolicyError("turn_endpoint_ssrf_blocked")

    @staticmethod
    def _deny(reason_code: str, relay_only: bool) -> TurnEndpointDecision:
        return TurnEndpointDecision(
            False,
            reason_code,
            None,
            None,
            "relay_only_no_host_candidates" if relay_only else "privacy_redacted_candidates",
        )


__all__ = [
    "StaticTurnDnsResolver",
    "TurnDnsResolverPort",
    "TurnEndpointDecision",
    "TurnEndpointRule",
    "WebrtcTurnEndpointPolicy",
    "WebrtcTurnEndpointPolicyError",
]
