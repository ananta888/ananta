from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from agent.adapters.livekit_broadcast_egress_adapter import (
    LivekitBroadcastEgressAdapter,
    SfuEgressObservationQuery,
    SfuEgressRuntimeActionCommand,
    SfuEgressSubscriptionCommand,
)
from agent.services.sfu_broadcast_runtime_control_port import (
    SfuRuntimeControlCommand,
    SfuRuntimeControlResult,
)


NOW = 1_800_000_000.0
DIGEST = "a" * 64


class _Client:
    def __init__(self):
        self.calls = []

    def capabilities(self):
        return {"route_apply": "accepted_unverified", "route_update": "accepted_unverified", "route_revoke": "accepted_unverified"}

    def apply(self, command):
        self.calls.append(("apply", command))
        return SimpleNamespace(
            accepted_by_api=True, authoritative_runtime_ack=False,
            reason_code="livekit_command_accepted_unverified", calls_completed=1,
            retryable=False,
        )

    update = apply
    revoke = apply


class _Authorization:
    def authorize_subscription(self, command): return True
    def authorize_runtime_action(self, command): return True


class _Capabilities:
    def __init__(self, values=None): self.values = values or {}
    def capabilities(self, target_runtime_id): return self.values


class _Runtime:
    def __init__(self): self.calls = []
    def execute(self, command):
        self.calls.append(command)
        return SfuRuntimeControlResult(
            True, True, "runtime_action_applied", command.target_runtime_id,
            command.flag_version, command.cohort_version, command.config_digest,
            command.nonce, command.fencing_token, "ack",
        )


def _subscription():
    return SfuEgressSubscriptionCommand(
        "op-a", "subscribe", "tenant-a", "room-a", "pub-a", 3, 4, 5,
        int(NOW * 1000), int(NOW * 1000) + 10_000, DIGEST, DIGEST,
        object(),
    )


def _control(action="disconnect"):
    return SfuRuntimeControlCommand(
        "runtime-a", f"sfu_egress_{action}", "runtime-a", "tenant-a",
        1, 1, DIGEST, "nonce-a", 5, NOW, NOW + 10, {},
    )


def test_public_subscription_is_authorized_idempotent_and_never_claims_runtime_ack() -> None:
    client = _Client()
    adapter = LivekitBroadcastEgressAdapter(
        client=client, authorization=_Authorization(), runtime_capabilities=_Capabilities(),
        clock=lambda: NOW,
    )
    first = adapter.mutate_subscription(_subscription())
    duplicate = adapter.mutate_subscription(_subscription())
    assert first.outcome == "accepted_unverified"
    assert duplicate.duplicate and len(client.calls) == 1
    conflict = adapter.mutate_subscription(replace(_subscription(), route_epoch=4))
    assert conflict.reason_code == "sfu_egress_idempotency_conflict"


def test_optional_hook_requires_exact_positive_capability() -> None:
    runtime = _Runtime()
    command = SfuEgressRuntimeActionCommand(
        "runtime-op", "disconnect", "tenant-a", "room-a", "pub-a", 3, 4, 5,
        int(NOW * 1000) + 10_000, DIGEST, _control(),
    )
    unsupported = LivekitBroadcastEgressAdapter(
        client=_Client(), authorization=_Authorization(), runtime_capabilities=_Capabilities(),
        runtime_control=runtime, clock=lambda: NOW,
    ).execute_runtime_action(command)
    assert unsupported.reason_code == "sfu_egress_capability_unsupported" and not runtime.calls
    available = LivekitBroadcastEgressAdapter(
        client=_Client(), authorization=_Authorization(),
        runtime_capabilities=_Capabilities({"runtime_disconnect": "available"}),
        runtime_control=runtime, clock=lambda: NOW,
    ).execute_runtime_action(command)
    assert available.outcome == "applied" and len(runtime.calls) == 1
    invalid_control = replace(_control(), config_digest="not-a-digest")
    invalid = available_adapter = LivekitBroadcastEgressAdapter(
        client=_Client(), authorization=_Authorization(),
        runtime_capabilities=_Capabilities({"runtime_disconnect": "available"}),
        runtime_control=runtime, clock=lambda: NOW,
    )
    rejected = available_adapter.execute_runtime_action(replace(
        command, operation_id="runtime-op-invalid", control_command=invalid_control,
    ))
    assert rejected.reason_code == "sfu_egress_digest_invalid"


def test_observation_rejects_payload_sampling_and_accepts_only_content_free_metrics() -> None:
    query = SfuEgressObservationQuery("tenant-a", "room-a", "pub-a", "node-a", 3, 4, 5)
    valid = {
        "tenant_id": "tenant-a", "room_id": "room-a", "publication_id": "pub-a",
        "node_id": "node-a", "window_started_at_ms": 100, "window_ended_at_ms": 1100,
        "route_epoch": 3, "topology_epoch": 4, "fencing_token": 5,
        "actual_egress_bytes": 1000, "estimated_egress_bytes": None,
        "observable_drops": 2, "receiver_count": 4,
    }
    source = SimpleNamespace(observe=lambda _query: valid)
    adapter = LivekitBroadcastEgressAdapter(
        client=_Client(), authorization=_Authorization(), runtime_capabilities=_Capabilities(),
        observations=source, clock=lambda: NOW,
    )
    assert adapter.observe(query).available
    source.observe = lambda _query: {**valid, "payload_sample": "forbidden"}
    assert adapter.observe(query).reason_code == "sfu_egress_observation_invalid"
