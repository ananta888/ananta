"""Hub-owned persistent intents resolved by delegated mail workers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from agent.services.mail_provider_ports import (
    MailContentAccessDecision,
    MailContentAccessRequest,
    MailProviderResult,
)

_OPERATIONS = frozenset({"body", "mutation"})
_BODY_SCOPES = frozenset({"body_excerpt", "full_body", "attachment_ref"})
_MUTATION_ACTIONS = frozenset(
    {"set_keywords", "move_messages", "delete_messages"}
)
_FORBIDDEN_KEYS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "body_text",
    "html_body",
    "content",
)


def _contains_sensitive(value: Any, key: str = "") -> bool:
    normalized = str(key or "").lower()
    if any(part in normalized for part in _FORBIDDEN_KEYS):
        if not normalized.endswith(("_ref", "_refs", "_hash")):
            return True
    if isinstance(value, Mapping):
        return any(
            _contains_sensitive(item, str(item_key))
            for item_key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    return isinstance(value, (bytes, bytearray, memoryview))


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


@dataclass(frozen=True)
class MailOperationIntent:
    intent_ref: str
    operation: str
    account_id: str
    workspace_id: str
    grant_ref: str
    idempotency_key: str
    payload: Mapping[str, Any]
    expires_at: float
    job_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_ref": self.intent_ref,
            "operation": self.operation,
            "account_id": self.account_id,
            "workspace_id": self.workspace_id,
            "grant_ref": self.grant_ref,
            "idempotency_key": self.idempotency_key,
            "payload": json.loads(
                json.dumps(dict(self.payload), ensure_ascii=True)
            ),
            "expires_at": self.expires_at,
            "job_id": self.job_id,
        }


class MailOperationIntentService:
    """Persists reference-addressed intents; never stores credentials/content."""

    def __init__(
        self,
        *,
        store_path: str | Path,
        clock: Any = time.time,
    ) -> None:
        self._path = Path(store_path).resolve()
        self._clock = clock
        self._lock = RLock()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": "ananta.mail-operation-intents.v1", "intents": []}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema")
            != "ananta.mail-operation-intents.v1"
            or not isinstance(payload.get("intents"), list)
        ):
            raise ValueError("mail_operation_intent_store_invalid")
        return payload

    def create(
        self,
        *,
        operation: str,
        account_id: str,
        workspace_id: str,
        grant_ref: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        ttl_seconds: int = 300,
    ) -> MailOperationIntent:
        normalized_operation = str(operation or "").strip().lower()
        account = str(account_id or "").strip()
        workspace = str(workspace_id or "").strip()
        grant = str(grant_ref or "").strip()
        idempotency = str(idempotency_key or "").strip()
        body = json.loads(json.dumps(dict(payload or {}), ensure_ascii=True))
        ttl = int(ttl_seconds)
        if normalized_operation not in _OPERATIONS:
            raise ValueError("mail_operation_intent_operation_invalid")
        if not account or not workspace or not grant or not idempotency:
            raise ValueError("mail_operation_intent_context_incomplete")
        if ttl < 30 or ttl > 3600:
            raise ValueError("mail_operation_intent_ttl_invalid")
        self._validate_payload(normalized_operation, body)
        if _contains_sensitive(body):
            raise ValueError("mail_operation_intent_sensitive_data_forbidden")
        canonical = json.dumps(
            {
                "operation": normalized_operation,
                "account_id": account,
                "workspace_id": workspace,
            "grant_ref": grant,
            "idempotency_key": idempotency,
            "payload": body,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        intent = MailOperationIntent(
            intent_ref="mail-intent:"
            + hashlib.sha256(
                (
                    f"{normalized_operation}|{account}|{workspace}|"
                    f"{idempotency}"
                ).encode("utf-8")
            ).hexdigest(),
            operation=normalized_operation,
            account_id=account,
            workspace_id=workspace,
            grant_ref=grant,
            idempotency_key=idempotency,
            payload=body,
            expires_at=float(self._clock()) + ttl,
        )
        with self._lock:
            store = self._load()
            now = float(self._clock())
            rows = [
                dict(item)
                for item in store["intents"]
                if isinstance(item, Mapping)
                and float(item.get("expires_at") or 0.0) > now
            ]
            existing = next(
                (
                    MailOperationIntent(**item)
                    for item in rows
                    if str(item.get("intent_ref") or "")
                    == intent.intent_ref
                ),
                None,
            )
            if existing is not None:
                existing_canonical = json.dumps(
                    {
                        "operation": existing.operation,
                        "account_id": existing.account_id,
                        "workspace_id": existing.workspace_id,
                        "grant_ref": existing.grant_ref,
                        "idempotency_key": existing.idempotency_key,
                        "payload": dict(existing.payload),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if existing_canonical != canonical:
                    raise ValueError(
                        "mail_operation_intent_idempotency_mismatch"
                    )
                return existing
            rows.append(intent.to_dict())
            store["intents"] = rows
            _atomic_write(self._path, store)
        return intent

    def bind_job(self, *, intent_ref: str, job_id: str) -> MailOperationIntent:
        target = str(intent_ref or "").strip()
        job = str(job_id or "").strip()
        if not target or not job:
            raise ValueError("mail_operation_intent_binding_invalid")
        with self._lock:
            store = self._load()
            for index, item in enumerate(store["intents"]):
                if (
                    isinstance(item, Mapping)
                    and str(item.get("intent_ref") or "") == target
                ):
                    row = dict(item)
                    existing = str(row.get("job_id") or "")
                    if existing and existing != job:
                        raise ValueError("mail_operation_intent_already_bound")
                    row["job_id"] = job
                    store["intents"][index] = row
                    _atomic_write(self._path, store)
                    return MailOperationIntent(**row)
        raise ValueError("mail_operation_intent_not_found")

    def resolve(
        self,
        *,
        intent_ref: str,
        job_id: str,
        operation: str,
        account_id: str,
        workspace_id: str,
    ) -> MailProviderResult[MailOperationIntent]:
        target = str(intent_ref or "").strip()
        with self._lock:
            rows = self._load()["intents"]
        row = next(
            (
                dict(item)
                for item in rows
                if isinstance(item, Mapping)
                and str(item.get("intent_ref") or "") == target
            ),
            None,
        )
        if row is None:
            return MailProviderResult.failure(
                "mail_operation_intent_not_found"
            )
        intent = MailOperationIntent(**row)
        if intent.expires_at <= float(self._clock()):
            return MailProviderResult.failure(
                "mail_operation_intent_expired"
            )
        if (
            intent.job_id != str(job_id)
            or intent.operation != str(operation)
            or intent.account_id != str(account_id)
            or intent.workspace_id != str(workspace_id)
        ):
            return MailProviderResult.failure(
                "mail_operation_intent_scope_mismatch"
            )
        return MailProviderResult.success(
            intent,
            reason_code="mail_operation_intent_resolved",
        )

    def authorize_content(
        self,
        *,
        intent: MailOperationIntent,
        request: MailContentAccessRequest,
    ) -> MailProviderResult[MailContentAccessDecision]:
        payload = dict(intent.payload)
        ref = dict(payload.get("message_ref") or {})
        if (
            intent.operation != "body"
            or request.account_id != intent.account_id
            or request.workspace_id != intent.workspace_id
            or request.grant_ref != intent.grant_ref
            or request.mail_ref_id != str(ref.get("mail_ref_id") or "")
            or request.release_scope != str(payload.get("release_scope") or "")
            or not request.artifact_ref.startswith(
                f"mail://{request.mail_ref_id}"
            )
        ):
            return MailProviderResult.failure(
                "mail_content_access_intent_mismatch"
            )
        expires = datetime.fromtimestamp(intent.expires_at, tz=UTC)
        return MailProviderResult.success(
            MailContentAccessDecision(
                allowed=True,
                reason_code="mail_content_access_intent_authorized",
                policy_decision_ref=f"policy:mail-intent:{intent.intent_ref}",
                expires_at=expires.isoformat().replace("+00:00", "Z"),
                nonce=hashlib.sha256(
                    f"{intent.intent_ref}|{request.artifact_ref}".encode("utf-8")
                ).hexdigest(),
            )
        )

    @staticmethod
    def _validate_payload(operation: str, payload: Mapping[str, Any]) -> None:
        if operation == "body":
            if set(payload) != {"message_ref", "release_scope"}:
                raise ValueError("mail_body_intent_fields_invalid")
            ref = payload.get("message_ref")
            if not isinstance(ref, Mapping) or not all(
                str(ref.get(field) or "")
                for field in ("mail_ref_id", "account_id", "protocol")
            ):
                raise ValueError("mail_body_intent_message_ref_invalid")
            if payload.get("release_scope") not in _BODY_SCOPES:
                raise ValueError("mail_body_intent_scope_invalid")
            return
        allowed = {
            "action",
            "message_refs",
            "add_keywords",
            "remove_keywords",
            "destination_mailbox_ref_ids",
            "if_in_state",
            "permanent",
            "intent_ref",
            "audit_ref",
            "confirmation_ref",
        }
        if set(payload) - allowed:
            raise ValueError("mail_mutation_intent_fields_invalid")
        if payload.get("action") not in _MUTATION_ACTIONS:
            raise ValueError("mail_mutation_intent_action_invalid")
        refs = payload.get("message_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("mail_mutation_intent_message_refs_required")
        if any(
            not isinstance(ref, Mapping)
            or not all(
                str(ref.get(field) or "")
                for field in ("mail_ref_id", "account_id", "protocol")
            )
            for ref in refs
        ):
            raise ValueError("mail_mutation_intent_message_ref_invalid")
        if not str(payload.get("intent_ref") or "") or not str(
            payload.get("audit_ref") or ""
        ):
            raise ValueError("mail_mutation_intent_audit_context_required")
        if (
            payload.get("action") == "delete_messages"
            and bool(payload.get("permanent"))
            and not str(payload.get("confirmation_ref") or "")
        ):
            raise ValueError("mail_permanent_delete_confirmation_required")


_INTENT_SERVICES: dict[str, MailOperationIntentService] = {}
_INTENT_LOCK = RLock()


def get_mail_operation_intent_service(
    *,
    root: str | Path | None = None,
) -> MailOperationIntentService:
    base = Path(root or ".").resolve()
    key = str(base)
    with _INTENT_LOCK:
        service = _INTENT_SERVICES.get(key)
        if service is None:
            service = MailOperationIntentService(
                store_path=base
                / "data"
                / "mail"
                / "operation-intents-v1.json"
            )
            _INTENT_SERVICES[key] = service
        return service


__all__ = [
    "MailOperationIntent",
    "MailOperationIntentService",
    "get_mail_operation_intent_service",
]
