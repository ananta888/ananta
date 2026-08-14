from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace

import pytest

from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.errors import ContractValidationError
from agent.services.workflow_runtime.security import HmacKeyRing
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
)
from ananta_contracts.temporal_workflow import (
    COMMAND_SCHEMA,
    LEGACY_COMMAND_SCHEMA,
    WorkflowCommand,
    WorkflowCommandType,
)
from worker.temporal.command_authority import (
    PublicKeyWorkflowCommandAuthorityVerifier,
    WorkflowCommandAuthorityActivity,
)
from worker.temporal.config import TemporalWorkerConfig
from worker.temporal.runtime import build_command_authority_activity

PLAN_HASH = "a" * 64


def _ed_signer(key_id: str, seed: bytes) -> Ed25519SigningKeyRing:
    return Ed25519SigningKeyRing(
        {key_id: base64.b64encode(seed).decode("ascii")},
        active_key_id=key_id,
    )


def _issue(
    key_ring: HmacKeyRing | Ed25519SigningKeyRing,
    *,
    command_id: str = "command-1",
    payload: dict[str, object] | None = None,
    nonce: str = "nonce-1",
    now: float = 100.0,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=key_ring,
        command_id=command_id,
        command_type="approve",
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        checkpoint_id="checkpoint-1",
        expected_revision=3,
        plan_hash=PLAN_HASH,
        policy_version="policy-v1",
        actor_id="operator-1",
        actor_roles=("operator",),
        payload=dict(payload or {}),
        now=now,
        ttl_seconds=60,
        nonce=nonce,
    )


def _legacy_mapping(
    command: SignedWorkflowCommand,
    key_ring: HmacKeyRing | Ed25519SigningKeyRing,
) -> dict[str, object]:
    unsigned = command.to_dict()
    unsigned["schema"] = LEGACY_COMMAND_SCHEMA
    unsigned.pop("signature_algorithm")
    unsigned.pop("payload_digest")
    unsigned.pop("signature")
    key_id, signature = key_ring.sign(
        namespace=LEGACY_COMMAND_SCHEMA,
        payload=unsigned,
        key_id=str(unsigned["key_id"]),
    )
    return {**unsigned, "key_id": key_id, "signature": signature}


def _bindings() -> dict[str, object]:
    return {
        "tenant_id": "tenant-1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "step_id": "step-1",
        "checkpoint_id": "checkpoint-1",
        "expected_revision": 3,
        "plan_hash": PLAN_HASH,
        "policy_version": "policy-v1",
        "now": 101.0,
    }


def _activity_result(
    activity: WorkflowCommandAuthorityActivity,
    command: dict[str, object],
) -> dict[str, object]:
    return asyncio.run(activity.verify(command))


def test_v3_preserves_hub_shape_payload_limit_and_neutral_enum() -> None:
    key_ring = HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key")
    command = _issue(key_ring, payload={"blob": "x" * 20_000})

    assert command.schema == COMMAND_SCHEMA
    assert command.command_type == "approve"
    assert command.signature_algorithm == "hmac-sha256"
    assert command.payload_digest.startswith("sha256:")
    neutral = WorkflowCommand.from_mapping(command.to_dict())
    assert neutral.command_type is WorkflowCommandType.APPROVE

    with pytest.raises(ContractValidationError, match="workflow_command_payload_too_large"):
        _issue(key_ring, payload={"blob": "x" * 65_537})


def test_legacy_v2_hmac_parses_verifies_and_keeps_semantic_digest() -> None:
    key_ring = HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key")
    current = _issue(key_ring)
    raw_legacy = _legacy_mapping(current, key_ring)
    legacy = SignedWorkflowCommand.from_mapping(raw_legacy)

    assert len(legacy.signature) == 64
    assert legacy.signature_algorithm == ""
    assert "signature_algorithm" not in legacy.to_dict()
    assert legacy.computed_payload_digest() == current.payload_digest
    legacy.verify(key_ring=key_ring, **_bindings())


def test_semantic_digest_excludes_renewable_authority_fields() -> None:
    first = _issue(_ed_signer("ed-old", b"o" * 32))
    renewed = _issue(
        _ed_signer("ed-new", b"n" * 32),
        nonce="nonce-renewed",
        now=110.0,
    )

    assert first.payload_digest == renewed.payload_digest
    assert first.key_id != renewed.key_id
    assert first.nonce != renewed.nonce
    changed = _issue(
        _ed_signer("ed-new", b"n" * 32),
        payload={"decision": "changed"},
    )
    assert changed.payload_digest != first.payload_digest


def test_public_key_activity_accepts_v3_and_legacy_88_char_ed25519() -> None:
    signer = _ed_signer("ed-current", b"c" * 32)
    activity = WorkflowCommandAuthorityActivity(
        PublicKeyWorkflowCommandAuthorityVerifier(signer.verification_key_ring())
    )
    current = _issue(signer)
    legacy = _legacy_mapping(current, signer)

    assert len(current.signature) == 88
    assert len(str(legacy["signature"])) == 88
    assert _activity_result(activity, current.to_dict())["accepted"] is True
    legacy_result = _activity_result(activity, legacy)
    assert legacy_result["accepted"] is True
    assert legacy_result["payload_digest"] == current.payload_digest


def test_public_key_activity_fails_closed_for_missing_hmac_unknown_and_tampering() -> None:
    trusted = _ed_signer("ed-trusted", b"t" * 32)
    command = _issue(trusted)
    activity = WorkflowCommandAuthorityActivity(
        PublicKeyWorkflowCommandAuthorityVerifier(trusted.verification_key_ring())
    )

    assert (
        _activity_result(WorkflowCommandAuthorityActivity(), command.to_dict())["reason_code"]
        == "temporal_command_verification_keyring_required"
    )

    hmac_command = _issue(HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key"))
    assert _activity_result(activity, hmac_command.to_dict())["reason_code"] == (
        "unsupported_command_signature_algorithm"
    )

    unknown = _issue(_ed_signer("ed-unknown", b"u" * 32))
    assert _activity_result(activity, unknown.to_dict())["reason_code"] == "signing_key_unknown"

    digest_tamper = command.to_dict()
    digest_tamper["payload"] = {"decision": "tampered"}
    assert _activity_result(activity, digest_tamper)["reason_code"] == ("invalid_command_payload_digest")

    changed = _issue(trusted, payload={"decision": "tampered"})
    signature_tamper = {**changed.to_dict(), "signature": command.signature}
    assert _activity_result(activity, signature_tamper)["reason_code"] == "signature_invalid"


def test_public_key_rotation_overlap_and_revocation_are_explicit() -> None:
    old_signer = _ed_signer("ed-old", b"o" * 32)
    new_signer = _ed_signer("ed-new", b"n" * 32)
    public_keys = {**old_signer.public_keys(), **new_signer.public_keys()}
    overlap = WorkflowCommandAuthorityActivity(
        PublicKeyWorkflowCommandAuthorityVerifier(Ed25519VerificationKeyRing(public_keys))
    )
    old_command = _issue(old_signer, command_id="old-command")
    new_command = _issue(new_signer, command_id="new-command")

    assert _activity_result(overlap, old_command.to_dict())["accepted"] is True
    assert _activity_result(overlap, new_command.to_dict())["accepted"] is True

    revoked = WorkflowCommandAuthorityActivity(
        PublicKeyWorkflowCommandAuthorityVerifier(
            Ed25519VerificationKeyRing(
                public_keys,
                revoked_key_ids=("ed-old",),
                revoked_contract_ids=("new-command",),
            )
        )
    )
    assert _activity_result(revoked, old_command.to_dict())["reason_code"] == "signing_key_revoked"
    assert _activity_result(revoked, new_command.to_dict())["reason_code"] == "signed_contract_revoked"


def test_worker_runtime_without_public_keyring_is_fail_closed_and_verify_only() -> None:
    signer = _ed_signer("ed-current", b"c" * 32)
    activity = build_command_authority_activity(TemporalWorkerConfig())
    result = _activity_result(activity, _issue(signer).to_dict())

    assert result["accepted"] is False
    assert result["reason_code"] == "temporal_command_verification_keyring_required"
    public_ring = signer.verification_key_ring()
    assert not hasattr(public_ring, "sign")
    with pytest.raises(TypeError, match="ed25519_verification_keyring_required"):
        PublicKeyWorkflowCommandAuthorityVerifier(
            HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key")  # type: ignore[arg-type]
        )


def test_worker_runtime_loads_public_ed25519_but_not_legacy_hmac(tmp_path) -> None:
    signer = _ed_signer("ed-current", b"c" * 32)
    public_keyring = tmp_path / "public-keyring.json"
    public_keyring.write_text(
        json.dumps(signer.verification_mapping()),
        encoding="utf-8",
    )
    accepted = _activity_result(
        build_command_authority_activity(TemporalWorkerConfig(authorization_keyring_file=str(public_keyring))),
        _issue(signer).to_dict(),
    )
    assert accepted["accepted"] is True
    assert "private_keys" not in public_keyring.read_text(encoding="utf-8")

    legacy_keyring = tmp_path / "legacy-keyring.json"
    legacy_keyring.write_text(
        json.dumps(
            {
                "active_key_id": "hmac-key",
                "keys": {"hmac-key": "h" * 32},
            }
        ),
        encoding="utf-8",
    )
    legacy_command = _issue(HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key"))
    rejected = _activity_result(
        build_command_authority_activity(
            TemporalWorkerConfig(
                authorization_keyring_file=str(legacy_keyring),
                allow_legacy_hmac_keyring=True,
            )
        ),
        legacy_command.to_dict(),
    )
    assert rejected["reason_code"] == "temporal_command_verification_keyring_required"


def test_unexpected_activity_infrastructure_failure_remains_ambiguous() -> None:
    class BrokenVerifier:
        def verify(self, _command: WorkflowCommand):
            raise RuntimeError("test command authority infrastructure failure")

    signer = _ed_signer("ed-current", b"c" * 32)
    activity = WorkflowCommandAuthorityActivity(BrokenVerifier())

    with pytest.raises(RuntimeError, match="infrastructure failure"):
        _activity_result(activity, _issue(signer).to_dict())


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "reason_code"),
    (
        pytest.param("expected_revision", True, "invalid_command_revision", id="revision-bool"),
        pytest.param("expected_revision", {}, "invalid_command_revision", id="revision-type"),
        pytest.param(
            "expected_revision",
            "not-an-int",
            "invalid_command_revision",
            id="revision-value",
        ),
        pytest.param("expected_revision", 3.5, "invalid_command_revision", id="revision-fraction"),
        pytest.param(
            "expected_revision",
            float("nan"),
            "invalid_command_revision",
            id="revision-nan",
        ),
        pytest.param(
            "expected_revision",
            float("inf"),
            "invalid_command_revision",
            id="revision-positive-inf",
        ),
        pytest.param(
            "expected_revision",
            float("-inf"),
            "invalid_command_revision",
            id="revision-negative-inf",
        ),
        pytest.param("expected_revision", -1, "invalid_command_revision", id="revision-negative"),
        pytest.param(
            "expected_revision",
            9_007_199_254_740_992,
            "invalid_command_revision",
            id="revision-out-of-range",
        ),
        pytest.param(
            "expected_revision",
            "9" * 5_000,
            "invalid_command_revision",
            id="revision-huge-decimal-string",
        ),
        pytest.param("issued_at", True, "invalid_command_issued_at", id="issued-bool"),
        pytest.param("issued_at", [], "invalid_command_issued_at", id="issued-type"),
        pytest.param(
            "issued_at",
            "not-a-float",
            "invalid_command_issued_at",
            id="issued-value",
        ),
        pytest.param(
            "issued_at",
            float("nan"),
            "invalid_command_issued_at",
            id="issued-nan",
        ),
        pytest.param(
            "issued_at",
            float("inf"),
            "invalid_command_issued_at",
            id="issued-positive-inf",
        ),
        pytest.param(
            "issued_at",
            float("-inf"),
            "invalid_command_issued_at",
            id="issued-negative-inf",
        ),
        pytest.param("issued_at", -1.0, "invalid_command_issued_at", id="issued-negative"),
        pytest.param(
            "issued_at",
            9_007_199_254_740_992,
            "invalid_command_issued_at",
            id="issued-out-of-range",
        ),
        pytest.param(
            "issued_at",
            10**10_000,
            "invalid_command_issued_at",
            id="issued-overflow",
        ),
        pytest.param(
            "issued_at",
            "9" * 5_000,
            "invalid_command_issued_at",
            id="issued-huge-decimal-string",
        ),
        pytest.param("expires_at", False, "invalid_command_expires_at", id="expires-bool"),
        pytest.param("expires_at", {}, "invalid_command_expires_at", id="expires-type"),
        pytest.param(
            "expires_at",
            "not-a-float",
            "invalid_command_expires_at",
            id="expires-value",
        ),
        pytest.param(
            "expires_at",
            float("nan"),
            "invalid_command_expires_at",
            id="expires-nan",
        ),
        pytest.param(
            "expires_at",
            float("inf"),
            "invalid_command_expires_at",
            id="expires-positive-inf",
        ),
        pytest.param(
            "expires_at",
            float("-inf"),
            "invalid_command_expires_at",
            id="expires-negative-inf",
        ),
        pytest.param("expires_at", -1.0, "invalid_command_expires_at", id="expires-negative"),
        pytest.param(
            "expires_at",
            9_007_199_254_740_992,
            "invalid_command_expires_at",
            id="expires-out-of-range",
        ),
        pytest.param(
            "expires_at",
            10**10_000,
            "invalid_command_expires_at",
            id="expires-overflow",
        ),
        pytest.param(
            "expires_at",
            "9" * 5_000,
            "invalid_command_expires_at",
            id="expires-huge-decimal-string",
        ),
    ),
)
def test_malformed_command_numerics_are_typed_contract_denials(
    field_name: str,
    invalid_value: object,
    reason_code: str,
) -> None:
    class VerifierMustNotRun:
        def verify(self, _command: WorkflowCommand):
            raise AssertionError("verifier must not run for malformed command numerics")

    raw_command = _issue(_ed_signer("ed-current", b"c" * 32)).to_dict()
    raw_command[field_name] = invalid_value

    result = _activity_result(
        WorkflowCommandAuthorityActivity(VerifierMustNotRun()),
        raw_command,
    )

    assert result["accepted"] is False
    assert result["reason_code"] == reason_code


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    (
        pytest.param(100.0, 100.0, id="equal"),
        pytest.param(101.0, 100.0, id="reversed"),
    ),
)
def test_malformed_command_expiry_window_is_a_typed_contract_denial(
    issued_at: float,
    expires_at: float,
) -> None:
    raw_command = _issue(_ed_signer("ed-current", b"c" * 32)).to_dict()
    raw_command["issued_at"] = issued_at
    raw_command["expires_at"] = expires_at

    result = _activity_result(WorkflowCommandAuthorityActivity(), raw_command)

    assert result["accepted"] is False
    assert result["reason_code"] == "invalid_command_expiry"


def test_dataclass_tamper_still_fails_existing_hub_signature_contract() -> None:
    key_ring = HmacKeyRing({"hmac-key": "h" * 32}, active_key_id="hmac-key")
    command = _issue(key_ring)

    with pytest.raises(Exception, match="signature_invalid"):
        replace(command, actor_roles=("admin",)).verify(key_ring=key_ring, **_bindings())
