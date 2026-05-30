"""PRD06.01: Smoke-Test für public-ananta Netzwerkprofil.

- Lädt public-ananta Profil und prüft Default-URLs
- Validiert ENV-Override funktioniert
- Simuliert zwei TUI Clients gegen Test-Rendezvous
- Profil local/offline nutzt keine öffentlichen URLs
"""
from __future__ import annotations

import os
import pytest


def test_public_ananta_profile_has_correct_defaults():
    import importlib
    import client_surfaces.operator_tui.network_profile as np_mod
    importlib.reload(np_mod)
    profile = np_mod.get_profile("public-ananta")
    assert profile["profile_id"] == "public-ananta"
    assert "keycloak.ananta.de" in profile["oidc"]["issuer"]
    assert "webrtc.ananta.de" in profile["rendezvous"]["base_url"]
    assert "webrtc.ananta.de" in profile["rendezvous"]["signaling_url"]
    assert profile["rendezvous"]["require_e2e_payload_encryption"] is True
    assert profile["oidc"]["pkce_required"] is True


def test_local_profile_has_no_public_urls():
    from client_surfaces.operator_tui.network_profile import get_profile
    profile = get_profile("local")
    assert "keycloak.ananta.de" not in (profile["oidc"].get("issuer") or "")
    assert "webrtc.ananta.de" not in (profile["rendezvous"].get("base_url") or "")


def test_offline_profile_has_no_transport():
    from client_surfaces.operator_tui.network_profile import get_profile
    profile = get_profile("offline")
    assert not profile["rendezvous"]["transport_order"]
    assert not profile["oidc"]["issuer"]


def test_env_override_oidc_issuer(monkeypatch):
    monkeypatch.setenv("ANANTA_OIDC_ISSUER", "https://my-keycloak.example.com/realms/test")
    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "custom")
    import importlib
    import client_surfaces.operator_tui.network_profile as np_mod
    importlib.reload(np_mod)
    profile = np_mod.get_active_profile()
    assert profile["oidc"]["issuer"] == "https://my-keycloak.example.com/realms/test"


def test_env_override_rendezvous_url(monkeypatch):
    monkeypatch.setenv("ANANTA_RENDEZVOUS_URL", "https://my-rendezvous.example.com")
    monkeypatch.setenv("ANANTA_NETWORK_PROFILE", "custom")
    import importlib
    import client_surfaces.operator_tui.network_profile as np_mod
    importlib.reload(np_mod)
    profile = np_mod.get_active_profile()
    assert profile["rendezvous"]["base_url"] == "https://my-rendezvous.example.com"


def test_two_clients_rendezvous_flow():
    """Simuliert Owner und Teilnehmer über Rendezvous-Service (in-memory)."""
    import agent.services.rendezvous_service as svc_mod
    svc_mod._sessions.clear()
    svc_mod._participants.clear()
    svc_mod._invite_codes.clear()
    svc_mod._service = None

    from agent.services.rendezvous_service import RendezvousService
    service = RendezvousService()

    # Client A: erstellt Session
    session = service.create_session(
        owner_user_id="user-a",
        owner_device_fingerprint="fp-a:aa:bb:cc:dd",
        oidc_issuer="https://keycloak.ananta.de/realms/ananta",
        title="Smoke Test Session",
    )
    assert session["invite_code"]
    invite_code = session["invite_code"]

    # Client B: tritt bei
    result = service.join_session(
        invite_code=invite_code,
        user_id="user-b",
        user_sub="sub-user-b",
        device_id="device-b",
        device_fingerprint="fp-b:11:22:33:44",
        oidc_issuer="https://keycloak.ananta.de/realms/ananta",
    )
    assert result["ok"]
    assert result["participant"]["user_id"] == "user-b"

    # Beide sehen die Teilnehmerliste
    participants = service.get_participants(session_id=session["id"], requester_user_id="user-a")
    assert participants["ok"]
    assert len(participants["participants"]) == 1

    # Verschlüsselte Chat-Nachricht ist ohne Klartext übertragbar
    from client_surfaces.operator_tui.share_crypto import SessionKeyPair, encrypt_chat, decrypt_chat, _CONTEXT_CHAT
    kp_a = SessionKeyPair()
    kp_b = SessionKeyPair()
    shared_key_a = kp_a.derive_shared_key(kp_b.public_key_bytes, _CONTEXT_CHAT)
    shared_key_b = kp_b.derive_shared_key(kp_a.public_key_bytes, _CONTEXT_CHAT)
    plaintext = b"Hallo von Client A"
    payload = encrypt_chat(plaintext, shared_key_a, "msg-smoke-1")
    wire = payload.to_dict()
    # Kein Klartext im übertragenen Payload
    assert plaintext.decode() not in str(wire)
    # Client B kann entschlüsseln
    from client_surfaces.operator_tui.share_crypto import EncryptedPayload
    decoded = decrypt_chat(EncryptedPayload.from_dict(wire), shared_key_b)
    assert decoded == plaintext

    # Aufräumen
    svc_mod._sessions.clear()
    svc_mod._participants.clear()
    svc_mod._invite_codes.clear()
    svc_mod._service = None
