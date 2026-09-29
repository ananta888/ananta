"""Unit tests for the OIDC settings layer (Welle 2).

These tests verify:
- Default config (OIDC disabled) is the safe path
- `oidc_is_configured()` requires ALL fields when enabled
- Settings are re-read each call (no stale module-level cache)
- Field types are normalised correctly (algorithms → tuple)
"""

from agent.services import oidc_settings


def _reset_settings(**overrides):
    """Mutate the live settings object for one test."""
    fields = [
        "oidc_enabled",
        "oidc_issuer_url",
        "oidc_jwks_url",
        "oidc_audience",
        "oidc_client_id",
        "oidc_jwks_cache_seconds",
        "oidc_allowed_algorithms",
    ]
    old = {}
    for f in fields:
        old[f] = getattr(oidc_settings.settings, f)
        if f in overrides:
            setattr(oidc_settings.settings, f, overrides[f])
        else:
            # Reset to defaults (mirrors config.py defaults)
            defaults = {
                "oidc_enabled": False,
                "oidc_issuer_url": "",
                "oidc_jwks_url": "",
                "oidc_audience": "",
                "oidc_client_id": "",
                "oidc_jwks_cache_seconds": 3600,
                "oidc_allowed_algorithms": "RS256",
            }
            setattr(oidc_settings.settings, f, defaults[f])
    return old


def test_default_state_oidc_disabled_and_not_configured():
    _reset_settings()
    cfg = oidc_settings.get_oidc_config()
    assert cfg.enabled is False
    assert oidc_settings.oidc_is_configured() is False


def test_enabled_with_all_required_fields_is_configured():
    _reset_settings(
        oidc_enabled=True,
        oidc_issuer_url="https://keycloak.example/realms/ananta",
        oidc_jwks_url="https://keycloak.example/realms/ananta/protocol/openid-connect/certs",
        oidc_audience="ananta-hub",
        oidc_client_id="ananta-frontend",
    )
    assert oidc_settings.oidc_is_configured() is True


def test_enabled_but_missing_one_required_field_is_not_configured():
    """Default-deny: partial config must NOT silently fall back."""
    for missing in ["oidc_issuer_url", "oidc_jwks_url", "oidc_audience", "oidc_client_id"]:
        kwargs = dict(
            oidc_enabled=True,
            oidc_issuer_url="https://keycloak.example/realms/ananta",
            oidc_jwks_url="https://keycloak.example/realms/ananta/protocol/openid-connect/certs",
            oidc_audience="ananta-hub",
            oidc_client_id="ananta-frontend",
        )
        kwargs[missing] = ""
        _reset_settings(**kwargs)
        assert oidc_settings.oidc_is_configured() is False, (
            f"missing {missing} should make OIDC NOT configured (default-deny)"
        )


def test_algorithms_normalised_to_tuple():
    _reset_settings(oidc_enabled=True, oidc_allowed_algorithms="RS256,RS512,ES256")
    cfg = oidc_settings.get_oidc_config()
    assert cfg.allowed_algorithms == ("RS256", "RS512", "ES256")


def test_empty_algorithms_defaults_to_rs256():
    _reset_settings(oidc_enabled=True, oidc_allowed_algorithms="")
    cfg = oidc_settings.get_oidc_config()
    assert cfg.allowed_algorithms == ("RS256",)


def test_config_re_read_each_call_no_stale_cache():
    """Mutating settings between calls is reflected immediately."""
    _reset_settings(oidc_enabled=False)
    assert oidc_settings.get_oidc_config().enabled is False
    _reset_settings(oidc_enabled=True)
    assert oidc_settings.get_oidc_config().enabled is True