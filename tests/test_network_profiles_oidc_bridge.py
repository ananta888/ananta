"""Unit tests for the OIDC bridge injection in network_profiles (Welle 4).

When OIDC is enabled on the Hub, GET /api/network-profiles/<id> must
inject the actual Hub-OIDC values (issuer/client_id/audience) and set
bridge_active=true. When OIDC is disabled, the JSON file values pass
through unchanged and bridge_active=false.

We bypass auth by monkeypatching the @check_auth decorator on the
endpoint module for the duration of these tests.
"""

from __future__ import annotations

from agent.routes import network_profiles
from agent.services import oidc_settings


def _set_oidc(**kwargs):
    saved = {}
    fields = [
        "oidc_enabled",
        "oidc_issuer_url",
        "oidc_jwks_url",
        "oidc_audience",
        "oidc_client_id",
        "oidc_jwks_cache_seconds",
        "oidc_allowed_algorithms",
        "oidc_registration_allowed",
    ]
    for f in fields:
        saved[f] = getattr(oidc_settings.settings, f)
    defaults = {
        "oidc_enabled": False,
        "oidc_issuer_url": "",
        "oidc_jwks_url": "",
        "oidc_audience": "",
        "oidc_client_id": "",
        "oidc_jwks_cache_seconds": 3600,
        "oidc_allowed_algorithms": "RS256",
        "oidc_registration_allowed": False,
    }
    for f in fields:
        setattr(oidc_settings.settings, f, kwargs.get(f, defaults[f]))
    return saved


def _restore(saved):
    for k, v in saved.items():
        setattr(oidc_settings.settings, k, v)


def _build_test_profile():
    return {
        "profile_id": "public-ananta",
        "label": "Test",
        "oidc": {
            "issuer": "https://keycloak.ananta.de/realms/ananta",
            "client_id": "ananta-tui",
            "audience": "ananta-hub",
            "pkce_required": True,
        },
        "rendezvous": {"base_url": "", "signaling_url": "", "transport_order": []},
        "ice_servers": [],
        "warning": "",
    }


def _install_test_profile(profile):
    network_profiles._CACHE = {"public-ananta": profile}
    network_profiles._CACHE_TS = 999999999.0  # far future → cache hit


def _clear_test_profile():
    network_profiles._CACHE = {}
    network_profiles._CACHE_TS = 0.0


def test_disabled_oidc_passes_through_json_values(monkeypatch):
    saved = _set_oidc(oidc_enabled=False)
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200, f"unexpected status {resp.status_code}"
            body = resp.get_json()
            oidc = body["profile"]["oidc"]
            assert oidc["bridge_active"] is False
            # Original JSON values preserved
            assert oidc["issuer"] == "https://keycloak.ananta.de/realms/ananta"
            assert oidc["client_id"] == "ananta-tui"
    finally:
        _restore(saved)
        _clear_test_profile()


def test_enabled_oidc_exposes_link_capability_without_overwriting_pair_provider(monkeypatch):
    saved = _set_oidc(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.ananta.de/realms/ananta",
        oidc_jwks_url="https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/certs",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
    )
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            oidc = body["profile"]["oidc"]
            assert oidc["enabled"] is True
            assert oidc["bridge_active"] is True
            assert oidc["hub_link_enabled"] is True
            assert oidc["issuer"] == "https://keycloak.ananta.de/realms/ananta"
            assert oidc["client_id"] == "ananta-tui"
            assert oidc["audience"] == "ananta-hub"
            assert oidc["pkce_required"] is True
    finally:
        _restore(saved)
        _clear_test_profile()


def test_enabled_partial_oidc_does_not_activate_bridge(monkeypatch):
    """Default-deny: OIDC enabled but partial config → bridge_active=false,
    JSON values pass through unchanged."""
    saved = _set_oidc(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.example/realms/ananta",
        oidc_jwks_url="",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
    )
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            oidc = body["profile"]["oidc"]
            assert oidc["bridge_active"] is False
            assert oidc["issuer"] == "https://keycloak.ananta.de/realms/ananta"
    finally:
        _restore(saved)
        _clear_test_profile()


# ── Welle: Self-Registration-Gate ───────────────────────────────────────
# RED-Tests: Das Backend muss /api/network-profiles/<id> ein Feld
# oidc.registration_allowed liefern, das exklusiv von der Hub-Config
# (settings.oidc_registration_allowed) abhängt — NICHT vom Profil-JSON.
# Default-deny: ohne env-Var → False. Mit env-Var=True → True.
# Solange OIDC nicht configured ist, muss registration_allowed immer
# False bleiben, auch wenn die env-Var gesetzt ist (default-deny-pattern,
# konsistent mit bridge_active).


def test_registration_allowed_defaults_to_false_when_oidc_unconfigured(monkeypatch):
    """Default-deny: ohne OIDC config (oidc_enabled=false) bleibt
    registration_allowed=false, auch wenn man es per env-Var setzt."""
    saved = _set_oidc(oidc_enabled=False, oidc_registration_allowed=True)
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["profile"]["oidc"]["registration_allowed"] is False
    finally:
        _restore(saved)
        _clear_test_profile()


def test_registration_allowed_false_when_env_var_false_but_oidc_configured(monkeypatch):
    """Auch bei vollständig konfiguriertem OIDC: registration_allowed=false
    wenn env-Var nicht gesetzt (default-deny)."""
    saved = _set_oidc(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.ananta.de/realms/ananta",
        oidc_jwks_url="https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/certs",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
        oidc_registration_allowed=False,
    )
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["profile"]["oidc"]["bridge_active"] is True
            assert body["profile"]["oidc"]["registration_allowed"] is False
    finally:
        _restore(saved)
        _clear_test_profile()


def test_registration_allowed_true_when_env_var_true_and_oidc_configured(monkeypatch):
    """Opt-in via env-Var OIDC_REGISTRATION_ALLOWED=true und vollständig
    konfiguriertem OIDC → registration_allowed=true."""
    saved = _set_oidc(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.ananta.de/realms/ananta",
        oidc_jwks_url="https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/certs",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
        oidc_registration_allowed=True,
    )
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["profile"]["oidc"]["bridge_active"] is True
            assert body["profile"]["oidc"]["registration_allowed"] is True
    finally:
        _restore(saved)
        _clear_test_profile()


def test_registration_allowed_does_not_leak_into_local_profile(monkeypatch):
    """registration_allowed kommt NUR aus settings.oidc_registration_allowed,
    nicht aus dem Profil-JSON. Auch wenn Profil-JSON das Feld enthält,
    wird es nicht propagiert — single-source-of-truth ist die env-Var."""
    saved = _set_oidc(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.ananta.de/realms/ananta",
        oidc_jwks_url="https://keycloak.ananta.de/realms/ananta/protocol/openid-connect/certs",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
        oidc_registration_allowed=True,
    )
    monkeypatch.setattr(network_profiles, "check_auth", lambda f: f)
    try:
        profile = _build_test_profile()
        profile["oidc"]["registration_allowed"] = False  # würde default überschreiben wenn falsch
        _install_test_profile(profile)
        from agent.ai_agent import create_app

        app = create_app(testing=True)
        with app.test_request_context("/api/network-profiles/public-ananta"):
            resp = network_profiles.get_network_profile("public-ananta")
            assert resp.status_code == 200
            body = resp.get_json()
            # env-Var (True) gewinnt — Profil-JSON-Wert wird ignoriert
            assert body["profile"]["oidc"]["registration_allowed"] is True
    finally:
        _restore(saved)
        _clear_test_profile()
