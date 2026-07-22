from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest

from agent.adapters.livekit_control_api_client import (
    LiveKitControlApiClient,
    LiveKitControlApiConfig,
    LiveKitControlApiError,
    LiveKitRouteBinding,
    PINNED_LIVEKIT_IMAGE,
    TwirpResponse,
    UnsupportedSfuRuntimeControlBoundary,
    build_sfu_runtime_control_boundary,
)
from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sfu-runtime-agent/src"))

from ananta_sfu_runtime.control_server import (  # noqa: E402
    FixedWindowRateLimiter,
    RuntimeControlApplication,
    RuntimeBoundaryError,
)
from ananta_sfu_runtime.key_providers import (  # noqa: E402
    KeyProviderUnavailable,
    Tpm2KeyProvider,
    key_provider_from_environment,
)

NOW = 1_800_000_000.0
SECRET = "s" * 32


class _Transport:
    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = list(statuses or ())

    def post(self, **kwargs):
        self.calls.append(kwargs)
        status = self.statuses.pop(0) if self.statuses else 200
        payload = {"participants": [{"identity": "receiver-a"}]} if kwargs["url"].endswith("ListParticipants") else {}
        return TwirpResponse(status, payload, 10)


class _Bindings:
    def __init__(self):
        self.binding = LiveKitRouteBinding(
            "room-a",
            ("receiver-a", "receiver-b"),
            ("TR_track_a",),
            subscription_only_safe=True,
        )

    def desired(self, _projection):
        return self.binding

    def existing(self, _key, _version):
        return self.binding

    def observed(self, _key):
        return self.binding


def _config(**changes):
    values = {
        "endpoint": "https://livekit.internal.example",
        "api_key": "ananta-control",
        "api_secret": SECRET,
        "server_version": "1.13.1",
    }
    values.update(changes)
    return LiveKitControlApiConfig(**values)


def _projection():
    return SimpleNamespace(
        runtime_control_mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        issued_at_ms=int(NOW * 1000) - 100,
        expires_at_ms=int(NOW * 1000) + 10_000,
        key=SimpleNamespace(),
    )


def test_livekit_boundary_requires_https_pinned_version_and_tls_verification():
    with pytest.raises(LiveKitControlApiError, match="livekit_https_endpoint_required"):
        _config(endpoint="http://livekit:7880")
    with pytest.raises(LiveKitControlApiError, match="livekit_server_version_skew"):
        _config(server_version="1.13.2")
    with pytest.raises(LiveKitControlApiError, match="livekit_tls_verification_required"):
        _config(verify_tls=False)
    assert "@sha256:" in PINNED_LIVEKIT_IMAGE


def test_apply_uses_only_roomservice_update_subscriptions_and_never_claims_ack():
    transport = _Transport()
    client = LiveKitControlApiClient(_config(), _Bindings(), transport=transport, clock=lambda: NOW)
    result = client.apply(SimpleNamespace(desired=_projection()))
    assert result.accepted_by_api is True
    assert result.authoritative_runtime_ack is False
    assert result.reason_code == "livekit_command_accepted_unverified"
    assert len(transport.calls) == 2
    assert all(call["url"].endswith("/twirp/livekit.RoomService/UpdateSubscriptions") for call in transport.calls)
    assert all(call["verify_tls"] is True for call in transport.calls)
    token = transport.calls[0]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False, "verify_nbf": False})
    assert claims["video"] == {"roomAdmin": True, "room": "room-a"}
    assert "roomCreate" not in claims["video"]


def test_remote_rate_limit_partial_apply_and_drain_fail_closed():
    transport = _Transport(statuses=[200, 429, 200])
    client = LiveKitControlApiClient(_config(), _Bindings(), transport=transport, clock=lambda: NOW)
    result = client.apply(SimpleNamespace(desired=_projection()))
    assert result.accepted_by_api is False
    assert result.rollback_completed is True
    assert result.retryable is True
    drain = client.drain()
    assert drain.accepted_by_api is False
    assert drain.reason_code == "livekit_drain_control_api_unsupported"


def test_observe_is_explicitly_non_authoritative():
    transport = _Transport()
    bindings = _Bindings()
    missing = LiveKitControlApiClient(_config(), bindings, transport=transport, clock=lambda: NOW)
    with pytest.raises(
        LiveKitControlApiError,
        match="livekit_observation_binding_resolver_missing",
    ):
        missing.observe(SimpleNamespace(key=SimpleNamespace()))
    client = LiveKitControlApiClient(
        _config(),
        bindings,
        observation_bindings=bindings,
        transport=transport,
        clock=lambda: NOW,
    )
    observation = client.observe(SimpleNamespace(key=SimpleNamespace()))
    assert observation.presence == "unknown"
    assert observation.authoritative is False
    assert observation.participant_identities == ("receiver-a",)


def test_unsupported_mode_keeps_control_disabled():
    boundary = build_sfu_runtime_control_boundary("unsupported")
    assert isinstance(boundary, UnsupportedSfuRuntimeControlBoundary)
    assert boundary.capabilities()["available"] is False
    with pytest.raises(LiveKitControlApiError) as health_error:
        boundary.health()
    assert health_error.value.reason_code == "sfu_runtime_mode_not_selected"
    with pytest.raises(LiveKitControlApiError) as command_error:
        boundary.apply(object())
    assert command_error.value.reason_code == "sfu_runtime_mode_not_selected"
    with pytest.raises(LiveKitControlApiError) as query_error:
        boundary.observe(object())
    assert query_error.value.reason_code == "sfu_runtime_mode_not_selected"


def test_mode_profiles_are_exclusive_and_stock_profile_has_no_agent_build():
    compose = (ROOT / "docker-compose.sfu-broadcast.yml").read_text(encoding="utf-8")
    stock = compose.split("  sfu-broadcast-livekit:", 1)[1].split("  sfu-runtime-agent:", 1)[0]
    extension = compose.split("  sfu-runtime-agent:", 1)[1].split("networks:", 1)[0]
    assert 'profiles: ["livekit_control_api"]' in stock
    assert "build:" not in stock
    assert "sfu-runtime-agent" not in stock
    assert 'profiles: ["authenticated_runtime_extension"]' in extension
    assert "build:" in extension
    assert "ANANTA_SFU_KEY_PROVIDER: tpm2" in extension
    assert ":?required" not in stock
    runtime_image = (ROOT / "docker/sfu-runtime-agent.Dockerfile").read_text(encoding="utf-8")
    assert "tpm2-tools" in runtime_image


def test_tpm_provider_has_no_private_export_or_fallback():
    provider = Tpm2KeyProvider("0x81000001", "ECDSA-SHA256", runner=lambda *_args, **_kwargs: None)
    assert not hasattr(provider, "private_key")
    assert not hasattr(provider, "export_private_key")
    with pytest.raises(KeyProviderUnavailable, match="requested_key_provider_unsupported"):
        key_provider_from_environment(
            {
                "ANANTA_SFU_RUNTIME_CONTROL_MODE": "authenticated_runtime_extension",
                "ANANTA_SFU_KEY_PROVIDER": "filesystem",
            }
        )
    with pytest.raises(KeyProviderUnavailable, match="runtime_extension_mode_not_selected"):
        key_provider_from_environment(
            {
                "ANANTA_SFU_RUNTIME_CONTROL_MODE": "livekit_control_api",
                "ANANTA_SFU_KEY_PROVIDER": "tpm2",
            }
        )


class _Authorizer:
    def __init__(self):
        self.control_required = []

    def authorize(self, certificate, *, control_required):
        if certificate is None:
            raise RuntimeBoundaryError("runtime_mtls_peer_required", status_code=401)
        self.control_required.append(control_required)
        return "sha256:peer"


class _Backend:
    def capabilities(self):
        return {"available": True}

    def health(self):
        return {"ready": True}

    def apply(self, route):
        return {"accepted": True, "route_id": route["route"]["route_id"]}

    update = apply

    def revoke(self, route):
        return {"accepted": True, "route_id": route["route_id"]}

    def observe(self, route):
        return {"presence": "unknown", "route_id": route["route_id"]}

    def drain(self):
        return {"accepted": True}


def test_extension_api_is_mtls_rate_limited_and_has_no_policy_or_task_surface():
    authorizer = _Authorizer()
    app = RuntimeControlApplication(
        _Backend(),
        authorizer,
        rate_limiter=FixedWindowRateLimiter(limit=1, clock=lambda: NOW),
    )
    denied, _ = app.handle(method="GET", path="/v1/health", body={}, peer_certificate_der=None)
    assert denied == 401
    missing, _ = app.handle(method="POST", path="/v1/tasks/create", body={}, peer_certificate_der=b"peer")
    assert missing == 404
    status, payload = app.handle(method="GET", path="/v1/capabilities", body={}, peer_certificate_der=b"peer")
    assert status == 200 and payload["available"] is True
    limited, _ = app.handle(method="GET", path="/v1/health", body={}, peer_certificate_der=b"peer")
    assert limited == 429


@pytest.mark.parametrize("forbidden", ["membership", "consent", "audience", "layercaps", "ttl", "epoch", "fencing"])
def test_extension_route_schema_cannot_expand_hub_authority(forbidden):
    app = RuntimeControlApplication(_Backend(), _Authorizer())
    route = {
        "route_id": "route-a",
        "room_name": "room-a",
        "receiver_identities": ["receiver-a"],
        "track_sids": ["TR_track_a"],
        forbidden: "forbidden",
    }
    status, payload = app.handle(
        method="POST",
        path="/v1/routes/apply",
        body={"operation_id": "op-a", "route": route},
        peer_certificate_der=b"peer",
    )
    assert status == 400
    assert payload["reason_code"] == "runtime_request_fields_invalid"
