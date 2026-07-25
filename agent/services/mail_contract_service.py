from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator

MAIL_ACCOUNT_RECORD_SCHEMA = "mail_account.v2"
MAIL_ACCOUNT_STORE_SCHEMA = "mail_accounts.v2"
MAIL_MESSAGE_REF_SCHEMA = "mail_message_ref.v2"
MAIL_METADATA_STORE_SCHEMA = "mail_metadata_store.v2"

_MAIL_REF_NAMESPACE = uuid.UUID("62f73fe0-98b8-5f17-91de-a8bafc957272")
_FORBIDDEN_SECRET_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "session_token",
    "authorization",
}
_PROTOCOLS = {"jmap", "imap"}
_REQUESTED_PROTOCOLS = {"auto", *_PROTOCOLS}
_SYNC_POLICIES = {"manual", "headers_only", "limited_recent"}


def _issues(schema: dict[str, Any], payload: Mapping[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for error in sorted(Draft202012Validator(schema).iter_errors(dict(payload)), key=lambda item: list(item.path)):
        path = "/".join(str(part) for part in error.path) or "$"
        found.append(
            {
                "path": path,
                "reason_code": "missing_required_field" if error.validator == "required" else "schema_validation_error",
                "human_message": error.message,
            }
        )
    return found


def _secret_issues(value: Any, path: tuple[str, ...] = ()) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = (*path, key)
            if key.lower() in _FORBIDDEN_SECRET_KEYS and child not in (None, "", [], {}):
                found.append(
                    {
                        "path": "/".join(child_path),
                        "reason_code": "plaintext_credentials_forbidden",
                        "human_message": f"{key} must not be stored in mail configuration",
                    }
                )
            found.extend(_secret_issues(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_secret_issues(child, (*path, str(index))))
    return found


def mail_account_v2_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/mail-account-v2.json",
        "type": "object",
        "required": [
            "account_id",
            "display_name",
            "requested_protocol",
            "username_ref",
            "credential_ref",
            "sync_policy",
            "enabled",
            "provider_config",
        ],
        "additionalProperties": True,
        "properties": {
            "schema": {"type": "string", "enum": [MAIL_ACCOUNT_RECORD_SCHEMA]},
            "account_id": {"type": "string", "minLength": 1},
            "display_name": {"type": "string", "minLength": 1},
            "requested_protocol": {"type": "string", "enum": sorted(_REQUESTED_PROTOCOLS)},
            "resolved_protocol": {"type": ["string", "null"], "enum": ["jmap", "imap", None]},
            "username_ref": {"type": "string", "minLength": 1},
            "credential_ref": {"type": "string", "minLength": 1},
            "sync_policy": {"type": "string", "enum": sorted(_SYNC_POLICIES)},
            "enabled": {"type": "boolean"},
            "provider_config": {"type": "object"},
        },
    }


def mail_message_ref_v2_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/mail-message-ref-v2.json",
        "type": "object",
        "required": ["mail_ref_id", "account_id", "protocol", "protocol_locator", "locator_version"],
        "additionalProperties": False,
        "properties": {
            "schema": {"type": "string", "enum": [MAIL_MESSAGE_REF_SCHEMA]},
            "mail_ref_id": {"type": "string", "pattern": r"^mailref-[0-9a-f]{32}$"},
            "account_id": {"type": "string", "minLength": 1},
            "protocol": {"type": "string", "enum": sorted(_PROTOCOLS)},
            "protocol_locator": {"type": "object"},
            "locator_version": {"type": "integer", "minimum": 1},
            "thread_ref_id": {"type": "string"},
        },
    }


def validate_mail_account(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    candidate = dict(payload)
    candidate.setdefault("schema", MAIL_ACCOUNT_RECORD_SCHEMA)
    found = _issues(mail_account_v2_schema(), candidate)
    found.extend(_secret_issues(candidate))
    requested = str(candidate.get("requested_protocol") or "")
    resolved = candidate.get("resolved_protocol")
    if requested in _PROTOCOLS and resolved not in (None, requested):
        found.append(
            {
                "path": "resolved_protocol",
                "reason_code": "mail_protocol_resolution_conflict",
                "human_message": "A forced requested protocol cannot resolve to a different protocol",
            }
        )
    return found


def validate_mail_message_ref(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    candidate = dict(payload)
    candidate.setdefault("schema", MAIL_MESSAGE_REF_SCHEMA)
    return _issues(mail_message_ref_v2_schema(), candidate)


def stable_mail_ref_id(*, account_id: str, protocol: str, stable_identity: Mapping[str, Any] | str) -> str:
    identity = stable_identity if isinstance(stable_identity, str) else json.dumps(
        dict(stable_identity), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    material = f"{str(account_id).strip()}|{str(protocol).strip().lower()}|{identity}"
    return f"mailref-{uuid.uuid5(_MAIL_REF_NAMESPACE, material).hex}"


def subject_hash(subject: str) -> str:
    return hashlib.sha256(str(subject or "").strip().lower().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MailAccountV2:
    account_id: str
    display_name: str
    requested_protocol: str
    username_ref: str
    credential_ref: str
    sync_policy: str
    enabled: bool
    provider_config: Mapping[str, Any]
    resolved_protocol: str | None = None

    @property
    def effective_protocol(self) -> str | None:
        if self.requested_protocol in _PROTOCOLS:
            return self.requested_protocol
        return self.resolved_protocol

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": MAIL_ACCOUNT_RECORD_SCHEMA,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "requested_protocol": self.requested_protocol,
            "resolved_protocol": self.resolved_protocol,
            "username_ref": self.username_ref,
            "credential_ref": self.credential_ref,
            "sync_policy": self.sync_policy,
            "enabled": self.enabled,
            "provider_config": dict(self.provider_config),
        }
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> MailAccountV2:
        candidate = dict(payload)
        if "requested_protocol" not in candidate and "protocol" in candidate:
            candidate["requested_protocol"] = candidate.get("protocol")
        candidate.setdefault("requested_protocol", "auto")
        candidate.setdefault("resolved_protocol", None)
        candidate.setdefault("provider_config", {})
        issues = validate_mail_account(candidate)
        if issues:
            raise ValueError(f"mail_account_invalid:{issues[0]['reason_code']}")
        return cls(
            account_id=str(candidate["account_id"]).strip(),
            display_name=str(candidate["display_name"]).strip(),
            requested_protocol=str(candidate["requested_protocol"]).strip().lower(),
            resolved_protocol=(
                str(candidate["resolved_protocol"]).strip().lower()
                if candidate.get("resolved_protocol") is not None
                else None
            ),
            username_ref=str(candidate["username_ref"]).strip(),
            credential_ref=str(candidate["credential_ref"]).strip(),
            sync_policy=str(candidate["sync_policy"]).strip(),
            enabled=bool(candidate["enabled"]),
            provider_config=dict(candidate.get("provider_config") or {}),
        )


@dataclass(frozen=True, slots=True)
class MailMessageRefV2:
    mail_ref_id: str
    account_id: str
    protocol: str
    protocol_locator: Mapping[str, Any]
    locator_version: int
    thread_ref_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MAIL_MESSAGE_REF_SCHEMA,
            "mail_ref_id": self.mail_ref_id,
            "account_id": self.account_id,
            "protocol": self.protocol,
            "protocol_locator": dict(self.protocol_locator),
            "locator_version": self.locator_version,
            "thread_ref_id": self.thread_ref_id,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> MailMessageRefV2:
        candidate = dict(payload)
        issues = validate_mail_message_ref(candidate)
        if issues:
            raise ValueError(f"mail_message_ref_invalid:{issues[0]['reason_code']}")
        return cls(
            mail_ref_id=str(candidate["mail_ref_id"]),
            account_id=str(candidate["account_id"]),
            protocol=str(candidate["protocol"]).lower(),
            protocol_locator=dict(candidate["protocol_locator"]),
            locator_version=int(candidate["locator_version"]),
            thread_ref_id=str(candidate.get("thread_ref_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class MailMessageMetadata:
    message_id_header: str = ""
    date: str = ""
    from_address: str = ""
    to_addresses: tuple[str, ...] = ()
    subject: str = ""
    subject_hash: str = ""
    size: int | None = None
    content_hash: str = ""
    mailbox_ref_ids: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    body_structure: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id_header": self.message_id_header,
            "date": self.date,
            "from": self.from_address,
            "to": list(self.to_addresses),
            "subject": self.subject,
            "subject_hash": self.subject_hash or subject_hash(self.subject),
            "size": self.size,
            "content_hash": self.content_hash,
            "mailbox_ref_ids": list(self.mailbox_ref_ids),
            "keywords": list(self.keywords),
            "body_structure": dict(self.body_structure or {}),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> MailMessageMetadata:
        candidate = dict(payload)
        recipients = candidate.get("to_addresses", candidate.get("to", ()))
        if isinstance(recipients, str):
            to_addresses = (recipients,) if recipients else ()
        else:
            to_addresses = tuple(str(item) for item in list(recipients or []) if str(item))
        raw_size = candidate.get("size")
        return cls(
            message_id_header=str(candidate.get("message_id_header", candidate.get("message_id", "")) or ""),
            date=str(candidate.get("date") or ""),
            from_address=str(candidate.get("from_address", candidate.get("from", "")) or ""),
            to_addresses=to_addresses,
            subject=str(candidate.get("subject") or ""),
            subject_hash=str(candidate.get("subject_hash") or ""),
            size=int(raw_size) if raw_size not in (None, "") else None,
            content_hash=str(candidate.get("content_hash") or ""),
            mailbox_ref_ids=tuple(str(item) for item in list(candidate.get("mailbox_ref_ids") or []) if str(item)),
            keywords=tuple(str(item) for item in list(candidate.get("keywords") or []) if str(item)),
            body_structure=dict(candidate.get("body_structure") or {}),
        )


def normalize_mail_account(payload: Mapping[str, Any]) -> dict[str, Any]:
    return MailAccountV2.from_mapping(payload).to_dict()


def normalize_mail_message_ref(payload: Mapping[str, Any]) -> dict[str, Any]:
    return MailMessageRefV2.from_mapping(payload).to_dict()
