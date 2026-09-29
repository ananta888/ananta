from __future__ import annotations

from dataclasses import replace

from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1
from agent.services.sfu_fanout_route_lifecycle import (
    FanoutRouteEvent,
    FanoutRouteLifecycleConfig,
    FanoutRouteLifecycleRecord,
    FanoutRouteReason,
    FanoutRouteState,
    RouteActivationGuards,
    RouteApplyEvidence,
    RouteEpochBinding,
    SfuFanoutRouteLifecycle,
    TRANSITION_TABLE,
)


def record(mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API):
    return FanoutRouteLifecycleRecord.persisted_intent(
        route_id="route-a", tenant_id="tenant-a", room_ref="room-a",
        runtime_scope_ref="cluster-a", runtime_control_mode=mode,
        intent_digest="a" * 64, idempotency_key="intent-a", nonce="nonce-a",
        intent_sequence=7, projection_version=3,
        epochs=RouteEpochBinding(2, 3, 4, 5, 6), fencing_token="fence-7",
        issued_at_ms=1_000, expires_at_ms=5_000,
    )


def evidence(mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API, **changes):
    value = RouteApplyEvidence(
        operation_id="ack", idempotency_key="intent-a", nonce="nonce-a", sequence=7,
        projection_version=3, expires_at_ms=4_000, tenant_id="tenant-a",
        room_ref="room-a", runtime_scope_ref="cluster-a", intent_digest="a" * 64,
        fencing_token="fence-7", route_epoch=5, runtime_control_mode=mode,
        tls_bound=True, api_credential_bound=True, reconciliation_confirmed=True,
        mtls_bound=mode is RuntimeControlModeV1.AUTHENTICATED_RUNTIME_EXTENSION,
    )
    return replace(value, **changes)


def apply(lifecycle, item, event, op, now=1_100, **kwargs):
    return lifecycle.transition(item, event, operation_id=op, request_digest=(op[0] * 64), now_ms=now, **kwargs)


def test_livekit_activation_requires_persisted_intent_bound_proof_and_guards() -> None:
    lifecycle = SfuFanoutRouteLifecycle(FanoutRouteLifecycleConfig())
    dispatched = apply(lifecycle, record(), FanoutRouteEvent.DISPATCH, "dispatch").record
    acknowledged = apply(lifecycle, dispatched, FanoutRouteEvent.ACK, "ack", evidence=evidence())
    assert acknowledged.accepted and acknowledged.record.state is FanoutRouteState.ACK
    active = apply(
        lifecycle, acknowledged.record, FanoutRouteEvent.ACTIVATE, "activate",
        guards=RouteActivationGuards(True, True, True),
    )
    assert active.accepted and active.record.state is FanoutRouteState.ACTIVE
    duplicate = apply(
        lifecycle, active.record, FanoutRouteEvent.ACTIVATE, "activate",
        guards=RouteActivationGuards(True, True, True),
    )
    assert duplicate.replayed and duplicate.reason_code is FanoutRouteReason.DUPLICATE_IDEMPOTENT


def test_wrong_binding_and_unverified_extension_ack_are_denied_and_auditable() -> None:
    lifecycle = SfuFanoutRouteLifecycle(FanoutRouteLifecycleConfig())
    dispatched = apply(lifecycle, record(), FanoutRouteEvent.DISPATCH, "dispatch").record
    wrong = apply(lifecycle, dispatched, FanoutRouteEvent.ACK, "ack", evidence=evidence(room_ref="room-b"))
    assert not wrong.accepted
    assert wrong.reason_code is FanoutRouteReason.APPLY_EVIDENCE_BINDING_INVALID
    assert len(wrong.audit_digest) == 64
    extension = replace(record(RuntimeControlModeV1.AUTHENTICATED_RUNTIME_EXTENSION), state=FanoutRouteState.DISPATCH)
    unverified = apply(
        lifecycle, extension, FanoutRouteEvent.ACK, "ack",
        evidence=evidence(RuntimeControlModeV1.AUTHENTICATED_RUNTIME_EXTENSION, mtls_bound=False, signature_verified=False),
    )
    assert unverified.reason_code is FanoutRouteReason.RUNTIME_ACK_INVALID


def test_timeout_retry_budget_revoke_during_dispatch_and_expiry_during_update() -> None:
    lifecycle = SfuFanoutRouteLifecycle(FanoutRouteLifecycleConfig(dispatch_deadline_ms=10, retry_budget=1, retry_cooldown_ms=5))
    dispatched = apply(lifecycle, record(), FanoutRouteEvent.DISPATCH, "dispatch", now=1_100).record
    early = apply(lifecycle, dispatched, FanoutRouteEvent.TIMEOUT, "timeout-a", now=1_105)
    assert early.reason_code is FanoutRouteReason.DEADLINE_NOT_REACHED
    timed_out = apply(lifecycle, dispatched, FanoutRouteEvent.TIMEOUT, "timeout-b", now=1_110)
    retry = apply(lifecycle, timed_out.record, FanoutRouteEvent.RETRY, "retry", now=1_115)
    assert retry.accepted and retry.record.deadline_at_ms == 1_125
    revoked = apply(lifecycle, dispatched, FanoutRouteEvent.REVOKE, "revoke")
    assert revoked.record.state is FanoutRouteState.REVOKE
    updating = replace(record(), state=FanoutRouteState.UPDATE)
    expired = apply(lifecycle, updating, FanoutRouteEvent.ACK, "late-ack", now=5_000, evidence=evidence())
    assert expired.record.state is FanoutRouteState.EXPIRE


def test_table_is_closed_and_covers_all_nonterminal_terminal_paths() -> None:
    nonterminal = {FanoutRouteState.INTENT, FanoutRouteState.DISPATCH, FanoutRouteState.ACK, FanoutRouteState.ACTIVE, FanoutRouteState.UPDATE}
    for state in nonterminal:
        assert TRANSITION_TABLE[(state, FanoutRouteEvent.REVOKE)] is FanoutRouteState.REVOKE
        assert TRANSITION_TABLE[(state, FanoutRouteEvent.EXPIRE)] is FanoutRouteState.EXPIRE
        assert TRANSITION_TABLE[(state, FanoutRouteEvent.FAIL)] is FanoutRouteState.FAILED
