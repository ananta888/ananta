#!/usr/bin/env python3
"""Run the content-free M1 WebRTC cryptographic conformance matrix."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.services.share_view_security_service import ShareSecureEnvelopeService, ShareViewSecurityError
from agent.services.webrtc_epoch_service import WebrtcEpochService
from agent.services.webrtc_group_key_authorization_service import WebrtcGroupKeyAuthorizationService
from ananta_contracts.webrtc_security import (
    MAX_CIPHERTEXT_BYTES,
    AuthenticatedMetadata,
    EnvelopeRecipient,
    EnvelopeScope,
    SecureEnvelopeError,
    SecureEnvelopeV1,
    open_secure_envelope,
    seal_secure_envelope,
    validate_secure_envelope,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTORS = ROOT / "tests/fixtures/webrtc/crypto_vectors/secure_envelope_vectors.v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def evaluate(vector: dict[str, Any], fixture: dict[str, Any]) -> str:
    mutation = vector["mutation"]
    if mutation in {"wrong_peer", "wrong_epoch", "replay", "nonce_reuse"}:
        return _evaluate_live_admission(mutation)
    if mutation in {"join", "revoke"}:
        return _evaluate_group_key_boundary(mutation)
    raw = copy.deepcopy(fixture["envelope"])
    now_ms = int(fixture["now_ms"])
    if mutation == "tamper_ciphertext":
        ciphertext = bytearray(base64.b64decode(raw["ciphertext_b64"]))
        ciphertext[0] ^= 1
        raw["ciphertext_b64"] = base64.b64encode(ciphertext).decode()
    elif mutation == "swap_payload_type":
        raw["payload_type"] = "pair.control"
    elif mutation == "unknown_field":
        raw["plaintext"] = "must-not-be-accepted"
    elif mutation == "oversize_ciphertext":
        raw["ciphertext_b64"] = base64.b64encode(bytes(MAX_CIPHERTEXT_BYTES + 1)).decode()
    elif mutation == "expired":
        now_ms = int(raw["expires_at_ms"]) + 30_001

    try:
        envelope = validate_secure_envelope(raw, now_ms=now_ms)
        plaintext = open_secure_envelope(key=base64.b64decode(fixture["key_b64"]), envelope=envelope)
        return "ok" if plaintext == base64.b64decode(fixture["plaintext_b64"]) else "plaintext_mismatch"
    except SecureEnvelopeError as exc:
        return exc.reason_code


def _epoch_stack() -> tuple[WebrtcEpochService, WebrtcEpochRepository]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repository = WebrtcEpochRepository(db_engine=engine)
    return WebrtcEpochService(repository), repository


def _live_envelope(*, recipient_id: str = "bob", sequence: int = 1, nonce: bytes = b"n" * 12) -> SecureEnvelopeV1:
    pending = SecureEnvelopeV1(
        version=1,
        scope=EnvelopeScope("session", "sess-live-gate"),
        sender_id="alice",
        recipient=EnvelopeRecipient("peer", recipient_id),
        epoch=1,
        sequence=sequence,
        key_id="key-live-gate",
        payload_type="pair.view_delta",
        expires_at_ms=int(time.time() * 1000) + 60_000,
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        aad=AuthenticatedMetadata("semantic", "json", "0" * 64),
        ciphertext_b64="",
    )
    return seal_secure_envelope(key=b"k" * 32, plaintext=b"{}", envelope=pending)


def _evaluate_live_admission(mutation: str) -> str:
    epochs, _repository = _epoch_stack()
    claimed = epochs.claim_epoch(scope_kind="session", scope_id="sess-live-gate", hub_id="hub-gate")
    if not claimed.ok or claimed.epoch != 1:
        return "epoch_claim_failed"
    envelope = _live_envelope(recipient_id="alice" if mutation == "wrong_peer" else "bob")
    if mutation == "wrong_peer":
        try:
            ShareSecureEnvelopeService(epochs).validate(
                session_id="sess-live-gate",
                authenticated_sender_id="alice",
                serialized=json.dumps(envelope.to_dict()),
            )
        except ShareViewSecurityError as exc:
            return exc.reason_code
        return "ok"
    if mutation == "wrong_epoch":
        advanced = epochs.claim_epoch(
            scope_kind="session", scope_id="sess-live-gate", hub_id="hub-gate", advance=True
        )
        if not advanced.ok or advanced.epoch != 2:
            return "epoch_claim_failed"
        return epochs.accept_sequence(
            scope_kind="session",
            scope_id="sess-live-gate",
            epoch=envelope.epoch,
            sender_id=envelope.sender_id,
            authenticated_sender_id=envelope.sender_id,
            traffic_class=envelope.aad.traffic_class,
            sequence=envelope.sequence,
            nonce_b64=envelope.nonce_b64,
        ).reason_code
    first = epochs.accept_sequence(
        scope_kind="session",
        scope_id="sess-live-gate",
        epoch=envelope.epoch,
        sender_id=envelope.sender_id,
        authenticated_sender_id=envelope.sender_id,
        traffic_class=envelope.aad.traffic_class,
        sequence=envelope.sequence,
        nonce_b64=envelope.nonce_b64,
    )
    if not first.accepted:
        return first.reason_code
    return epochs.accept_sequence(
        scope_kind="session",
        scope_id="sess-live-gate",
        epoch=envelope.epoch,
        sender_id=envelope.sender_id,
        authenticated_sender_id=envelope.sender_id,
        traffic_class=envelope.aad.traffic_class,
        sequence=envelope.sequence if mutation == "replay" else envelope.sequence + 1,
        nonce_b64=envelope.nonce_b64,
    ).reason_code


def _evaluate_group_key_boundary(mutation: str) -> str:
    epochs, repository = _epoch_stack()
    room_id = f"room-gate-{uuid.uuid4().hex}"
    first = epochs.claim_epoch(scope_kind="room", scope_id=room_id, hub_id="hub-gate")
    if not first.ok or first.epoch != 1:
        return "epoch_claim_failed"
    service = WebrtcGroupKeyAuthorizationService(
        private_key=Ed25519PrivateKey.generate(),
        hub_key_id="hub-gate-key",
        epoch_repository=repository,
    )
    current_epoch = 1
    previous_epoch = 0
    members = ["alice"]
    if mutation == "join":
        advanced = epochs.claim_epoch(scope_kind="room", scope_id=room_id, hub_id="hub-gate", advance=True)
        if not advanced.ok or advanced.epoch != 2:
            return "epoch_claim_failed"
        current_epoch, previous_epoch, members = 2, 1, ["alice", "bob"]
    elif mutation == "revoke":
        initial = service.authorize(
            tenant_id="tenant-gate",
            room_id=room_id,
            publication_id="publication-gate",
            epoch=1,
            previous_epoch=0,
            active_member_ids=["alice", "bob"],
            key_package_refs={"alice": "package-alice-1", "bob": "package-bob-1"},
            reason="create",
            rekey_deadline_ms=int(time.time() * 1000) + 5_000,
            expires_at_ms=int(time.time() * 1000) + 60_000,
        )
        if "content_key" in initial.__dict__:
            return "group_content_key_exposed"
        advanced = epochs.claim_epoch(scope_kind="room", scope_id=room_id, hub_id="hub-gate", advance=True)
        if not advanced.ok or advanced.epoch != 2:
            return "epoch_claim_failed"
        current_epoch, previous_epoch = 2, 1
    authorization = service.authorize(
        tenant_id="tenant-gate",
        room_id=room_id,
        publication_id="publication-gate",
        epoch=current_epoch,
        previous_epoch=previous_epoch,
        active_member_ids=members,
        key_package_refs={member: f"package-{member}-{current_epoch}" for member in members},
        reason=mutation,
        rekey_deadline_ms=int(time.time() * 1000) + 5_000,
        expires_at_ms=int(time.time() * 1000) + 60_000,
    )
    return "group_content_key_exposed" if "content_key" in authorization.__dict__ else "group_key_missing"


def run_product_suites() -> bool:
    commands = (
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/contracts/test_webrtc_secure_envelope.py",
                "tests/security/test_webrtc_replay_protection.py",
                "tests/test_share_view_security_service.py",
                "tests/security/test_webrtc_group_rekey.py",
                "tests/test_semantic_sfu_group_keys.py",
            ],
            ROOT,
        ),
        (
            [
                "npx",
                "vitest",
                "run",
                "src/app/services/webrtc-crypto-conformance.spec.ts",
                "src/app/services/e2e-encryption.service.spec.ts",
                "src/app/services/webrtc-replay-window.service.spec.ts",
                "src/app/services/webrtc-group-key.service.spec.ts",
            ],
            ROOT / "frontend-angular",
        ),
    )
    for command, cwd in commands:
        completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, timeout=300)
        if completed.returncode != 0:
            return False
    return True


def run(vectors_path: Path) -> dict[str, Any]:
    fixture = json.loads(vectors_path.read_text(encoding="utf-8"))
    results = []
    for vector in fixture["vectors"]:
        actual = evaluate(vector, fixture)
        passed = actual == vector["expected_code"]
        results.append(
            {
                "name": vector["name"],
                "code": actual,
                "hash": hashlib.sha256(_canonical(vector)).hexdigest(),
                "passed": passed,
            }
        )
    return {"version": 1, "passed": all(item["passed"] for item in results), "vectors": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.vectors)
    runtime_passed = run_product_suites()
    if not runtime_passed:
        report["passed"] = False
        report["vectors"].append(
            {
                "name": "production_runtime_suites",
                "code": "production_runtime_suite_failed",
                "hash": hashlib.sha256(b"production-runtime-suites-v1").hexdigest(),
                "passed": False,
            }
        )
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
