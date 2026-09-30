"""Row, JSON and AAD codecs plus shared guards of the SFU group-key repositories.

Used by both the SQL adapter and the in-memory test/restart adapter so the
two stay substitutable (same receipts, same AAD binding, same page limits).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from sqlmodel import Session, select

from agent.db_models.sfu_broadcast_group_keys import (
    SfuBroadcastGroupKeyAuthorizationDB,
    SfuBroadcastGroupKeyReceiptDB,
)
from agent.models.sfu_group_keys import (
    GroupKeyEpochAuthorization,
    SfuGroupKeyEpochState,
    SfuGroupKeyMutationResult,
    SfuGroupKeyReceipt,
    SfuHubSealedSecret,
)
from agent.ports.sfu_group_keys import SfuHubSecretEnvelopePort
from agent.repositories.sfu_broadcast_group_key_validation import (
    SfuBroadcastGroupKeyRepositoryError,
)
from agent.repositories.sfu_broadcast_group_key_validation import (
    mutation_result as _result,
)


def _delivery_failure(
    state: SfuGroupKeyEpochState | None,
    tenant_id: str,
    expected_version: int,
    expected_fencing_token: int,
    now_ms: int,
) -> SfuGroupKeyMutationResult | None:
    if state is None or state.authorization.tenant_id != tenant_id:
        return _result("not_found", reason="sfu_group_authorization_unavailable")
    if state.status != "active" or state.authorization.expires_at_ms <= now_ms:
        return _result("expired", state=state, reason="sfu_group_authorization_stale")
    if state.version != expected_version:
        return _result("conflict", state=state, reason="sfu_group_version_conflict")
    if state.fencing_token != expected_fencing_token:
        return _result("stale_epoch", state=state, reason="sfu_group_fencing_stale")
    return None


def _authorization_row(
    state: SfuGroupKeyEpochState,
    now: float,
    envelope: SfuHubSecretEnvelopePort,
) -> SfuBroadcastGroupKeyAuthorizationDB:
    authorization = state.authorization
    payload = json.dumps(
        _authorization_json(authorization), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sealed = envelope.seal(
        payload,
        purpose="sfu-group-key-authorization",
        scope=f"{authorization.tenant_id}:{authorization.authorization_id}",
        aad=_authorization_aad(
            authorization.tenant_id,
            authorization.authorization_id,
            authorization.member_set_digest,
        ),
    )
    return SfuBroadcastGroupKeyAuthorizationDB(
        id=authorization.authorization_id,
        tenant_id=authorization.tenant_id,
        session_id=state.session_id,
        room_id=authorization.room_id,
        publication_id=authorization.publication_id,
        publisher_digest=state.publisher_digest,
        membership_epoch=authorization.membership_epoch or 0,
        key_epoch=authorization.epoch,
        previous_key_epoch=authorization.previous_epoch,
        member_set_digest=authorization.member_set_digest,
        authorization_json={},
        authorization_ciphertext=sealed.ciphertext,
        authorization_nonce=sealed.nonce,
        authorization_wrapping_key_id=sealed.key_id,
        distribution_mode=state.distribution_mode,
        package_count=state.package_count,
        total_package_bytes=state.total_package_bytes,
        status=state.status,
        fencing_token=state.fencing_token,
        version=state.version,
        valid_from_ms=authorization.valid_from_ms,
        expires_at_ms=authorization.expires_at_ms,
        rekey_deadline_ms=authorization.rekey_deadline_ms,
        created_at=now,
        updated_at=now,
    )


def _authorization_json(value: GroupKeyEpochAuthorization) -> dict:
    raw = asdict(value)
    raw["member_ids"] = list(value.member_ids)
    return json.loads(json.dumps(raw))


def _authorization_from_json(raw: dict) -> GroupKeyEpochAuthorization:
    value = dict(raw)
    value["member_ids"] = tuple(value.get("member_ids") or ())
    value["key_package_refs"] = dict(value.get("key_package_refs") or {})
    return GroupKeyEpochAuthorization(**value)


def _authorization_from_row(
    row: SfuBroadcastGroupKeyAuthorizationDB,
    envelope: SfuHubSecretEnvelopePort,
) -> GroupKeyEpochAuthorization:
    if (
        row.authorization_ciphertext is not None
        and row.authorization_nonce is not None
        and row.authorization_wrapping_key_id
    ):
        plaintext = envelope.open(
            SfuHubSealedSecret(
                row.authorization_wrapping_key_id,
                row.authorization_nonce,
                row.authorization_ciphertext,
            ),
            purpose="sfu-group-key-authorization",
            scope=f"{row.tenant_id}:{row.id}",
            aad=_authorization_aad(row.tenant_id, row.id, row.member_set_digest),
        )
        return _authorization_from_json(json.loads(plaintext.decode("utf-8")))
    return _authorization_from_json(row.authorization_json)


def _authorization_aad(
    tenant_id: str, authorization_id: str, member_set_digest: str
) -> bytes:
    return json.dumps(
        {
            "authorization_id": authorization_id,
            "member_set_digest": member_set_digest,
            "tenant_id": tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _receipt_row(receipt: SfuGroupKeyReceipt) -> SfuBroadcastGroupKeyReceiptDB:
    return SfuBroadcastGroupKeyReceiptDB(
        id="sfu-gk-receipt-" + hashlib.sha256("\0".join(_receipt_key(receipt)).encode()).hexdigest(),
        tenant_id=receipt.tenant_id,
        actor_digest=receipt.actor_digest,
        operation=receipt.operation,
        idempotency_key_digest=receipt.idempotency_key_digest,
        request_digest=receipt.request_digest,
        result_json=json.loads(json.dumps(receipt.result)),
        expires_at_ms=receipt.expires_at_ms,
    )


def _receipt_from_row(row: SfuBroadcastGroupKeyReceiptDB) -> SfuGroupKeyReceipt:
    return SfuGroupKeyReceipt(
        row.tenant_id, row.actor_digest, row.operation, row.idempotency_key_digest,
        row.request_digest, json.loads(json.dumps(row.result_json)), row.expires_at_ms,
    )


def _find_receipt(db: Session, receipt: SfuGroupKeyReceipt) -> SfuBroadcastGroupKeyReceiptDB | None:
    return db.exec(select(SfuBroadcastGroupKeyReceiptDB).where(
        SfuBroadcastGroupKeyReceiptDB.tenant_id == receipt.tenant_id,
        SfuBroadcastGroupKeyReceiptDB.actor_digest == receipt.actor_digest,
        SfuBroadcastGroupKeyReceiptDB.operation == receipt.operation,
        SfuBroadcastGroupKeyReceiptDB.idempotency_key_digest == receipt.idempotency_key_digest,
    )).first()


def _receipt_key(receipt: SfuGroupKeyReceipt) -> tuple[str, str, str, str]:
    return receipt.tenant_id, receipt.actor_digest, receipt.operation, receipt.idempotency_key_digest


def _validate_receipt(receipt: SfuGroupKeyReceipt, tenant_id: str) -> None:
    if receipt.tenant_id != tenant_id or receipt.operation not in {"prepare", "deliver"}:
        raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_receipt_invalid")


def _package_aad(tenant_id: str, authorization_id: str, package_ref: str, package_digest: str) -> bytes:
    return f"ananta:sfu-group-key-package:v1\0{tenant_id}\0{authorization_id}\0{package_ref}\0{package_digest}".encode()


def _validate_page(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise SfuBroadcastGroupKeyRepositoryError("sfu_group_key_page_limit_invalid")
