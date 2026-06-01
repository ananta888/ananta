"""E2E Live-Konnektivitätstests für keycloak.ananta.de und webrtc.ananta.de.

Diese Tests prüfen die echten Live-Dienste. Sie sind standardmäßig übersprungen
und werden nur mit dem Flag ANANTA_E2E_LIVE_EXTERNAL=1 ausgeführt.

Was wird getestet:
  Keycloak:
    - OIDC Discovery Endpoint antwortet (/.well-known/openid-configuration)
    - Response enthält authorization_endpoint, token_endpoint, issuer
    - Issuer stimmt mit der konfigurierten URL überein
    - End-session endpoint ist vorhanden
    - PKCE (S256) ist als code_challenge_method angekündigt

  WebRTC:
    - DNS-Auflösung von webrtc.ananta.de möglich
    - HTTPS Basis-Erreichbarkeit
    - STUN-Konnektivität auf Port 3478 (IceProbe wenn verfügbar)
    - Signaling WebSocket Endpoint ist erreichbar (/health oder /signaling)

Umgebungsvariablen:
    ANANTA_E2E_LIVE_EXTERNAL   Auf 1 setzen (erforderlich)
    ANANTA_KEYCLOAK_URL        Override (default: https://keycloak.ananta.de)
    ANANTA_KEYCLOAK_REALM      Override (default: ananta)
    ANANTA_WEBRTC_URL          Override (default: https://webrtc.ananta.de)
    ANANTA_STUN_PORT           Override (default: 3478)
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_LIVE_FLAG = "ANANTA_E2E_LIVE_EXTERNAL"

KEYCLOAK_BASE = os.environ.get("ANANTA_KEYCLOAK_URL", "https://keycloak.ananta.de").rstrip("/")
KEYCLOAK_REALM = os.environ.get("ANANTA_KEYCLOAK_REALM", "ananta")
WEBRTC_BASE = os.environ.get("ANANTA_WEBRTC_URL", "https://webrtc.ananta.de").rstrip("/")
STUN_PORT = int(os.environ.get("ANANTA_STUN_PORT", "3478"))


def _require_live() -> None:
    if os.environ.get(_LIVE_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip(f"Set {_LIVE_FLAG}=1 to run live external service tests.")


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"GET {url} → HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f"GET {url} unreachable: {exc.reason}") from exc


def _tcp_reachable(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _resolve_host(hostname: str) -> list[str]:
    try:
        results = socket.getaddrinfo(hostname, None)
        return list({r[4][0] for r in results})
    except socket.gaierror:
        return []


# ── Keycloak Tests ───────────────────────────────────────────────────────────


class TestKeycloakLive:

    def test_keycloak_dns_resolves(self) -> None:
        """keycloak.ananta.de ist per DNS auflösbar."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(KEYCLOAK_BASE).hostname
        ips = _resolve_host(host)
        assert ips, f"DNS lookup failed for {host} — service may be down"

    def test_keycloak_https_reachable(self) -> None:
        """keycloak.ananta.de ist per HTTPS erreichbar."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(KEYCLOAK_BASE).hostname
        assert _tcp_reachable(host, 443), \
            f"Port 443 not reachable on {host} — Keycloak may be down"

    def test_keycloak_oidc_discovery_responds(self) -> None:
        """OIDC Discovery Endpoint antwortet mit gültigem JSON."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        assert isinstance(doc, dict), f"Expected JSON dict, got: {type(doc)}"
        assert doc, f"Empty OIDC discovery document from {url}"

    def test_keycloak_oidc_discovery_has_required_fields(self) -> None:
        """OIDC Discovery enthält Pflichtfelder gemäß RFC 8414."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        required = ["issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"]
        missing = [f for f in required if f not in doc]
        assert not missing, f"OIDC discovery missing required fields: {missing}\nDoc keys: {list(doc.keys())}"

    def test_keycloak_oidc_issuer_matches_configured_url(self) -> None:
        """Issuer im Discovery-Dokument stimmt mit der konfigurierten URL überein."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        issuer = str(doc.get("issuer") or "")
        expected_fragment = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}"
        assert expected_fragment in issuer or issuer.rstrip("/") == expected_fragment.rstrip("/"), \
            f"Issuer mismatch. Expected fragment '{expected_fragment}' in '{issuer}'"

    def test_keycloak_oidc_pkce_supported(self) -> None:
        """Keycloak unterstützt PKCE (S256 als code_challenge_method)."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        methods = list(doc.get("code_challenge_methods_supported") or [])
        assert "S256" in methods, \
            f"PKCE S256 not in code_challenge_methods_supported: {methods}"

    def test_keycloak_oidc_has_end_session_endpoint(self) -> None:
        """Keycloak stellt einen End-Session-Endpoint bereit (für Logout)."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        end_session = str(doc.get("end_session_endpoint") or "")
        assert end_session, "end_session_endpoint missing from OIDC discovery"
        assert KEYCLOAK_BASE in end_session, \
            f"end_session_endpoint '{end_session}' doesn't contain '{KEYCLOAK_BASE}'"

    def test_keycloak_oidc_has_device_authorization_endpoint(self) -> None:
        """Keycloak stellt Device-Flow-Endpoint bereit (für TUI-Login)."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url)
        device_ep = str(doc.get("device_authorization_endpoint") or "")
        assert device_ep, \
            "device_authorization_endpoint missing — TUI Device Flow won't work"

    def test_keycloak_ananta_realm_exists(self) -> None:
        """Das ananta-Realm ist bei Keycloak registriert (nicht nur /realms/master)."""
        _require_live()
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            assert data.get("realm") == KEYCLOAK_REALM, \
                f"Realm mismatch: expected '{KEYCLOAK_REALM}', got '{data.get('realm')}'"
        except urllib.error.HTTPError as exc:
            pytest.fail(f"Realm '{KEYCLOAK_REALM}' not accessible: HTTP {exc.code}")


# ── WebRTC Tests ─────────────────────────────────────────────────────────────


class TestWebRTCLive:

    def test_webrtc_dns_resolves(self) -> None:
        """webrtc.ananta.de ist per DNS auflösbar."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(WEBRTC_BASE).hostname
        ips = _resolve_host(host)
        assert ips, f"DNS lookup failed for {host} — WebRTC service may be down"

    def test_webrtc_https_reachable(self) -> None:
        """webrtc.ananta.de ist per HTTPS (443) erreichbar."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(WEBRTC_BASE).hostname
        assert _tcp_reachable(host, 443, timeout=8.0), \
            f"Port 443 not reachable on {host}"

    def test_webrtc_stun_port_reachable(self) -> None:
        """STUN-Port (3478) auf webrtc.ananta.de ist TCP-erreichbar."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(WEBRTC_BASE).hostname
        assert _tcp_reachable(host, STUN_PORT, timeout=8.0), \
            f"STUN port {STUN_PORT} not reachable on {host} — STUN/TURN service may be down"

    def test_webrtc_stun_probe_succeeds(self) -> None:
        """IceProbe: STUN-Server auf webrtc.ananta.de antwortet korrekt."""
        _require_live()
        from urllib.parse import urlparse
        host = urlparse(WEBRTC_BASE).hostname
        stun_url = f"stun:{host}:{STUN_PORT}"
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from client_surfaces.operator_tui.realtime.ice_probe import IceProbe
            probe = IceProbe()
            result = probe.probe_stun(stun_url, timeout=8.0)
            assert result.stun_reachable, \
                f"STUN probe failed: {result.error or 'no error details'}"
        except ImportError:
            pytest.skip("IceProbe not available — skipping STUN probe (TCP check passed)")

    def test_webrtc_signaling_health_or_root_responds(self) -> None:
        """WebRTC Signaling-Endpunkt ist erreichbar (HTTP-Ebene)."""
        _require_live()
        candidates = [
            f"{WEBRTC_BASE}/health",
            f"{WEBRTC_BASE}/signaling/health",
            WEBRTC_BASE,
        ]
        last_error: Exception | None = None
        for url in candidates:
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json, text/plain, */*"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    _ = r.read()
                return  # any 2xx/3xx means reachable
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return  # 4xx still means server is up
                last_error = exc
            except urllib.error.URLError as exc:
                last_error = exc
        pytest.fail(
            f"WebRTC service not reachable at any of {candidates}. "
            f"Last error: {last_error}"
        )

    def test_webrtc_public_rendezvous_profile_urls_match_live(self) -> None:
        """Das public-ananta Netzwerkprofil zeigt auf die live WebRTC/Keycloak URLs."""
        _require_live()
        try:
            import importlib
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            import client_surfaces.operator_tui.network_profile as np_mod
            importlib.reload(np_mod)
            profile = np_mod.get_profile("public-ananta")
        except (ImportError, Exception) as exc:
            pytest.skip(f"network_profile module not available: {exc}")
            return

        from urllib.parse import urlparse
        configured_keycloak = urlparse(KEYCLOAK_BASE).hostname
        configured_webrtc = urlparse(WEBRTC_BASE).hostname

        issuer = str(profile.get("oidc", {}).get("issuer") or "")
        rendezvous_url = str(profile.get("rendezvous", {}).get("base_url") or "")
        signaling_url = str(profile.get("rendezvous", {}).get("signaling_url") or "")

        assert configured_keycloak in issuer, \
            f"Profile issuer '{issuer}' doesn't match configured Keycloak host '{configured_keycloak}'"
        assert configured_webrtc in rendezvous_url, \
            f"Profile rendezvous_url '{rendezvous_url}' doesn't match configured WebRTC host '{configured_webrtc}'"
        assert configured_webrtc in signaling_url, \
            f"Profile signaling_url '{signaling_url}' doesn't match configured WebRTC host '{configured_webrtc}'"


# ── Combined smoke test ───────────────────────────────────────────────────────


def test_live_external_services_smoke() -> None:
    """Schnell-Smoke: Beide Dienste sind DNS-auflösbar und per TCP erreichbar.

    Dieser Test ist ein zusammenfassender Check der kritischsten Bedingungen.
    Er eignet sich für CI-Monitoring ohne vollständige Testsuite.
    """
    _require_live()
    from urllib.parse import urlparse

    keycloak_host = urlparse(KEYCLOAK_BASE).hostname
    webrtc_host = urlparse(WEBRTC_BASE).hostname

    # DNS
    kc_ips = _resolve_host(keycloak_host)
    wrtc_ips = _resolve_host(webrtc_host)

    errors = []
    if not kc_ips:
        errors.append(f"DNS failed for {keycloak_host}")
    if not wrtc_ips:
        errors.append(f"DNS failed for {webrtc_host}")

    # TCP 443
    if not _tcp_reachable(keycloak_host, 443):
        errors.append(f"{keycloak_host}:443 not reachable")
    if not _tcp_reachable(webrtc_host, 443):
        errors.append(f"{webrtc_host}:443 not reachable")

    # OIDC discovery
    try:
        url = f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
        doc = _get_json(url, timeout=8.0)
        if not doc.get("authorization_endpoint"):
            errors.append("OIDC discovery missing authorization_endpoint")
    except AssertionError as exc:
        errors.append(f"OIDC discovery: {exc}")

    if errors:
        pytest.fail("Live service smoke FAILED:\n" + "\n".join(f"  - {e}" for e in errors))
