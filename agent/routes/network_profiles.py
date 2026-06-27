"""T25: GET /api/network-profiles/<profile_id> — liefert Netzwerkprofil an Angular."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify

from agent.auth import check_auth
from agent.services.oidc_settings import get_oidc_config, oidc_is_configured

network_profiles_bp = Blueprint("network_profiles", __name__)

_PROFILES_PATH = Path(__file__).parent.parent.parent / "config" / "ananta_network_profiles.default.json"
_CACHE: dict = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 300.0


def _load_profiles() -> dict:
    global _CACHE, _CACHE_TS
    now = time.monotonic()
    if _CACHE and (now - _CACHE_TS) < _CACHE_TTL:
        return _CACHE
    try:
        raw = json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        profiles = {p["profile_id"]: p for p in raw.get("profiles", []) if "profile_id" in p}
        _CACHE = profiles
        _CACHE_TS = now
        return profiles
    except Exception:
        return {}


def _resolve_turn_credentials(profile: dict) -> dict:
    """Generate ephemeral TURN credentials (test mode: static fallback)."""
    turn = profile.get("turn", {})
    if turn.get("credential_mode") == "ephemeral_from_rendezvous_or_test_env":
        test_user = os.environ.get("ANANTA_TURN_TEST_USER", "ananta-test")
        test_pass = os.environ.get("ANANTA_TURN_TEST_PASS", "")
        return {"username": test_user, "credential": test_pass, "ttl": 3600}
    return {}


@network_profiles_bp.route("/api/network-profiles/<profile_id>", methods=["GET"])
@check_auth
def get_network_profile(profile_id: str):
    profiles = _load_profiles()
    profile = profiles.get(profile_id)
    if not profile:
        return jsonify({"ok": False, "error": "profile_not_found", "profile_id": profile_id}), 404

    # Build ice_servers with ephemeral TURN credentials if needed
    ice_servers = []
    for srv in profile.get("ice_servers", []):
        entry = dict(srv)
        if entry.get("credential_mode") == "ephemeral_from_rendezvous_or_test_env":
            creds = _resolve_turn_credentials(profile)
            if creds.get("credential"):
                entry["username"] = creds["username"]
                entry["credential"] = creds["credential"]
            del entry["credential_mode"]
        ice_servers.append(entry)

    # Pair/WebRTC OIDC and Hub account linking are separate capabilities.
    # The profile owns the Pair provider.  Hub linking is an opt-in feature
    # and must not overwrite that provider or turn OIDC into Hub auth.
    oidc_block = dict(profile.get("oidc", {}))
    pair_enabled = bool(oidc_block.get("issuer") and oidc_block.get("client_id"))
    link_enabled = False
    if oidc_is_configured():
        oidc_cfg = get_oidc_config()
        link_enabled = pair_enabled and oidc_cfg.issuer_url.rstrip("/") == str(
            oidc_block.get("issuer") or ""
        ).rstrip("/")
    oidc_block = {
        **oidc_block,
        "enabled": pair_enabled,
        "hub_link_enabled": link_enabled,
        # Backward-compatible alias for clients introduced during Welle 4.
        "bridge_active": link_enabled,
    }

    return jsonify({
        "ok": True,
        "profile": {
            "profile_id": profile["profile_id"],
            "label": profile.get("label", ""),
            "oidc": oidc_block,
            "rendezvous": profile.get("rendezvous", {}),
            "ice_servers": ice_servers,
            "require_e2e_payload_encryption": profile.get("rendezvous", {}).get(
                "require_e2e_payload_encryption", False
            ),
            "signaling_url": profile.get("rendezvous", {}).get("signaling_url", ""),
            "transport_order": profile.get("rendezvous", {}).get("transport_order", ["hub_relay"]),
            "warning": profile.get("warning", ""),
        },
    })


@network_profiles_bp.route("/api/network-profiles", methods=["GET"])
@check_auth
def list_network_profiles():
    profiles = _load_profiles()
    return jsonify({
        "ok": True,
        "profiles": [
            {"profile_id": p["profile_id"], "label": p.get("label", "")}
            for p in profiles.values()
        ],
    })
