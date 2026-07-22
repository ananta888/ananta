import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sfu-runtime-agent/src"))

from ananta_sfu_runtime.command_guard import (  # noqa: E402
    RuntimeCommandGuard,
    RuntimeCommandGuardConfig,
    RuntimeCommandGuardError,
)


NOW = 1_700_000_000_000
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def command(*, fence=4, command_id="command-a", payload=None):
    payload = payload or {
        "operation_id": "operation-a",
        "route": {
            "route_id": "route-a",
            "room_name": "room-a",
            "receiver_identities": ["receiver-a"],
            "track_sids": ["track-a"],
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {
        "schema_version": "sfu_runtime_control_command.v2",
        "command_id": command_id,
        "command_type": "route_apply",
        "target_runtime_id": "runtime-a",
        "nonce": "nonce-a",
        "config_digest": DIGEST_A,
        "capability_digest": DIGEST_B,
        "flag_version": 2,
        "cohort_version": 3,
        "topology_epoch": 5,
        "route_epoch": 7,
        "parent_key_epoch": 11,
        "fencing_token": fence,
        "issued_at_ms": NOW - 100,
        "expires_at_ms": NOW + 10_000,
        "stale_access_deadline_ms": NOW + 8_000,
        "payload_digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "payload": payload,
    }


def guard(tmp_path):
    return RuntimeCommandGuard(
        RuntimeCommandGuardConfig(
            runtime_id="runtime-a",
            config_digest=DIGEST_A,
            capability_digest=DIGEST_B,
            state_path=tmp_path / "state.json",
        ),
        clock_ms=lambda: NOW,
    )


def test_duplicate_command_returns_receipt_without_second_backend_call(tmp_path):
    instance = guard(tmp_path)
    calls = 0

    def action(payload):
        nonlocal calls
        calls += 1
        return {"accepted": True, "reason_code": "accepted"}

    first = instance.execute(path="/v1/routes/apply", envelope=command(), action=action)
    second = instance.execute(path="/v1/routes/apply", envelope=command(), action=action)

    assert first == second
    assert calls == 1


def test_failed_newer_fence_still_blocks_older_hub(tmp_path):
    instance = guard(tmp_path)

    with pytest.raises(RuntimeError):
        instance.execute(
            path="/v1/routes/apply",
            envelope=command(fence=9, command_id="new-hub"),
            action=lambda payload: (_ for _ in ()).throw(RuntimeError("backend down")),
        )

    with pytest.raises(RuntimeCommandGuardError, match="runtime_command_fencing_stale"):
        instance.execute(
            path="/v1/routes/apply",
            envelope=command(fence=8, command_id="old-hub"),
            action=lambda payload: {"accepted": True},
        )


def test_policy_or_orchestration_fields_are_forbidden(tmp_path):
    with pytest.raises(RuntimeCommandGuardError, match="runtime_command_payload_forbidden"):
        guard(tmp_path).execute(
            path="/v1/routes/apply",
            envelope=command(payload={"task": "start-worker"}),
            action=lambda payload: {"accepted": True},
        )

