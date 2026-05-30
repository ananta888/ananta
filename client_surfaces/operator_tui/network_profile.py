"""PRD01.01: Netzwerkprofile für Rendezvous/OIDC-Konfiguration.

Profile: public-ananta | local | offline | custom
ENV-Werte überschreiben Repo-Defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_DEFAULT_PROFILES_FILE = Path(__file__).parent.parent.parent / "config" / "ananta_network_profiles.default.json"


def _load_default_profiles() -> list[dict[str, Any]]:
    try:
        return json.loads(_DEFAULT_PROFILES_FILE.read_text(encoding="utf-8")).get("profiles", [])
    except Exception:
        return []


def _env_or(key: str, default: str) -> str:
    return str(os.environ.get(key) or default).strip()


def _active_profile_id() -> str:
    return _env_or("ANANTA_NETWORK_PROFILE", "local")


def get_all_profiles() -> list[dict[str, Any]]:
    return _load_default_profiles()


def get_profile(profile_id: str | None = None) -> dict[str, Any]:
    pid = (profile_id or _active_profile_id()).strip().lower()
    for p in _load_default_profiles():
        if p.get("profile_id") == pid:
            return _apply_env_overrides(dict(p))
    # Fallback: local
    for p in _load_default_profiles():
        if p.get("profile_id") == "local":
            return _apply_env_overrides(dict(p))
    return _apply_env_overrides({"profile_id": "local", "label": "Local", "enabled_by_default": True, "oidc": {}, "rendezvous": {}, "turn": {}})


def get_active_profile() -> dict[str, Any]:
    return get_profile(_active_profile_id())


def is_public_profile_active() -> bool:
    return get_active_profile().get("profile_id") == "public-ananta"


def _apply_env_overrides(profile: dict[str, Any]) -> dict[str, Any]:
    oidc = dict(profile.get("oidc") or {})
    rendezvous = dict(profile.get("rendezvous") or {})
    turn = dict(profile.get("turn") or {})

    if os.environ.get("ANANTA_OIDC_ISSUER"):
        oidc["issuer"] = _env_or("ANANTA_OIDC_ISSUER", oidc.get("issuer", ""))
    if os.environ.get("ANANTA_OIDC_CLIENT_ID"):
        oidc["client_id"] = _env_or("ANANTA_OIDC_CLIENT_ID", oidc.get("client_id", ""))
    if os.environ.get("ANANTA_RENDEZVOUS_URL"):
        rendezvous["base_url"] = _env_or("ANANTA_RENDEZVOUS_URL", rendezvous.get("base_url", ""))
    if os.environ.get("ANANTA_SIGNALING_URL"):
        rendezvous["signaling_url"] = _env_or("ANANTA_SIGNALING_URL", rendezvous.get("signaling_url", ""))
    if os.environ.get("ANANTA_TURN_URL"):
        existing = turn.get("urls") or []
        extra = _env_or("ANANTA_TURN_URL", "")
        if extra and extra not in existing:
            turn["urls"] = [extra] + existing
    if os.environ.get("ANANTA_REQUIRE_E2E_PAYLOAD_ENCRYPTION"):
        val = _env_or("ANANTA_REQUIRE_E2E_PAYLOAD_ENCRYPTION", "").lower()
        rendezvous["require_e2e_payload_encryption"] = val in ("1", "true", "yes")
    if os.environ.get("ANANTA_PUBLIC_RENDEZVOUS_ENABLED"):
        enabled = _env_or("ANANTA_PUBLIC_RENDEZVOUS_ENABLED", "").lower()
        profile["_env_enabled"] = enabled in ("1", "true", "yes")

    profile["oidc"] = oidc
    profile["rendezvous"] = rendezvous
    profile["turn"] = turn
    return profile


def e2e_encryption_required() -> bool:
    profile = get_active_profile()
    return bool(profile.get("rendezvous", {}).get("require_e2e_payload_encryption", False))


def oidc_issuer() -> str:
    return str(get_active_profile().get("oidc", {}).get("issuer") or "")


def rendezvous_base_url() -> str:
    return str(get_active_profile().get("rendezvous", {}).get("base_url") or "")


def signaling_url() -> str:
    return str(get_active_profile().get("rendezvous", {}).get("signaling_url") or "")


def turn_urls() -> list[str]:
    return list(get_active_profile().get("turn", {}).get("urls") or [])


def transport_order() -> list[str]:
    return list(get_active_profile().get("rendezvous", {}).get("transport_order") or ["hub_relay"])
