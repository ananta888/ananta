import dataclasses

import pytest

from agent.services.webrtc_turn_credential_service import (
    InMemoryTurnCredentialStatePort,
    InMemoryTurnSigningKeyRing,
    TurnAdmissionDecision,
    TurnCredentialPolicy,
    TurnCredentialRequest,
    TurnSigningKey,
    WebrtcTurnCredentialError,
    WebrtcTurnCredentialService,
)


class _Admission:
    active = True

    def authorize(self, request):
        return TurnAdmissionDecision(self.active, self.active, 2000, "turn_admission_revoked")


def _service(mode="authorization_hook", now=None):
    now = now or [1000]
    admission = _Admission()
    keys = InMemoryTurnSigningKeyRing(TurnSigningKey("kid-1", b"s" * 32))
    state = InMemoryTurnCredentialStatePort(clock=lambda: now[0])
    policy = TurnCredentialPolicy(mode, 120, 600, 300, 600, 2, 30, 10, 3)
    tokens = iter(("credential-id-0001", "credential-id-0002", "credential-id-0003"))
    service = WebrtcTurnCredentialService(
        policy=policy,
        admission=admission,
        keys=keys,
        state=state,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )
    request = TurnCredentialRequest("tenant-a", "room-a", "participant-a", "device-a", "eu-1", "pool-a", 1, 120)
    return service, admission, keys, request, now


def test_hook_mode_enforces_all_claims_and_revokes_active_allocation_without_secret_repr():
    service, admission, _, request, _ = _service()
    bundle = service.issue(request)
    assert all(bundle.claims_binding.values())
    assert service.validate_authorization_hook(bundle, request)
    assert not service.validate_authorization_hook(bundle, dataclasses.replace(request, device_ref="device-b"))
    assert bundle.credential not in repr(bundle)
    revoked = service.revoke(bundle.credential_id)
    assert revoked.terminate_active_allocation and revoked.remaining_exposure_seconds == 0
    assert not service.validate_authorization_hook(bundle, request)
    admission.active = False
    with pytest.raises(WebrtcTurnCredentialError, match="admission_revoked"):
        service.issue(request)


def test_rest_hmac_never_claims_room_or_device_binding_and_rotation_bounds_replay():
    service, _, keys, request, now = _service("rest_hmac_bearer")
    bundle = service.issue(request)
    assert not any(bundle.claims_binding.values())
    assert bundle.active_allocation_revocable is False
    assert bundle.max_bearer_exposure_seconds == 600
    keys.rotate(TurnSigningKey("kid-2", b"n" * 32))
    assert keys.resolve("kid-1") is not None
    now[0] = 1090
    refreshed = service.refresh(bundle.credential_id, dataclasses.replace(request, refresh_count=1))
    assert refreshed.key_id == "kid-2"
    revoked = service.revoke(bundle.credential_id)
    assert not revoked.terminate_active_allocation
    assert revoked.remaining_exposure_seconds == 30
    with pytest.raises(WebrtcTurnCredentialError, match="refresh_cap_exceeded"):
        service.refresh(refreshed.credential_id, dataclasses.replace(request, refresh_count=3))


def test_refresh_is_scope_bound_single_use_and_old_hook_credential_observes_overlap():
    service, _, _, request, now = _service()
    bundle = service.issue(request)
    now[0] = 1090
    with pytest.raises(WebrtcTurnCredentialError, match="refresh_scope_mismatch"):
        service.refresh(
            bundle.credential_id,
            dataclasses.replace(request, room_ref="room-b", refresh_count=1),
        )
    refreshed = service.refresh(
        bundle.credential_id, dataclasses.replace(request, refresh_count=1)
    )
    with pytest.raises(WebrtcTurnCredentialError, match="refresh_inactive"):
        service.refresh(
            bundle.credential_id, dataclasses.replace(request, refresh_count=1)
        )
    assert service.validate_authorization_hook(bundle, request)
    now[0] = 1100
    assert not service.validate_authorization_hook(bundle, request)
    assert service.validate_authorization_hook(
        refreshed, dataclasses.replace(request, refresh_count=1)
    )


def test_emergency_signing_key_rotation_invalidates_refresh_fail_closed():
    service, _, keys, request, now = _service("rest_hmac_bearer")
    bundle = service.issue(request)
    keys.emergency_rotate(TurnSigningKey("kid-2", b"n" * 32))
    now[0] = 1090
    with pytest.raises(WebrtcTurnCredentialError, match="refresh_scope_mismatch"):
        service.refresh(
            bundle.credential_id, dataclasses.replace(request, refresh_count=1)
        )
