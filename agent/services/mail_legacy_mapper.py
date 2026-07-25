from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
    stable_mail_ref_id,
    subject_hash,
)


@dataclass(frozen=True, slots=True)
class LegacyMailRecord:
    message_ref: MailMessageRefV2
    metadata: MailMessageMetadata


@dataclass(frozen=True, slots=True)
class MailDedupeDecision:
    outcome: str
    reason_code: str
    mail_ref_id: str = ""
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class MailDedupeSummary:
    matched: int
    ambiguous: int
    unmatched: int
    decisions: tuple[MailDedupeDecision, ...] = ()


class MailLegacyMapper:
    @staticmethod
    def message_from_v1(
        message_ref: Mapping[str, Any],
        header_meta: Mapping[str, Any] | None = None,
    ) -> LegacyMailRecord:
        ref = dict(message_ref)
        headers = dict(header_meta or {})
        account_id = str(ref.get("account_id") or "")
        locator: dict[str, Any] = {
            "mailbox": str(ref.get("mailbox") or ""),
            "uid": int(ref.get("uid") or 0),
        }
        if ref.get("uidvalidity") not in (None, ""):
            locator["uidvalidity"] = int(ref["uidvalidity"])
        message_id = str(ref.get("message_id") or headers.get("message_id") or "")
        date = str(ref.get("date") or headers.get("date") or "")
        content_hash = str(ref.get("content_hash") or headers.get("content_hash") or "")
        size_raw = ref.get("size", headers.get("size"))
        size = int(size_raw) if size_raw not in (None, "") else None
        if message_id and date and content_hash and size is not None:
            identity: Mapping[str, Any] | str = {
                "message_id": message_id,
                "date": date,
                "size": size,
                "content_hash": content_hash,
            }
        else:
            identity = locator
        thread_id = str(headers.get("thread_id") or "")
        thread_ref_id = (
            stable_mail_ref_id(account_id=account_id, protocol="imap", stable_identity=f"thread:{thread_id}")
            if thread_id
            else ""
        )
        normalized_ref = MailMessageRefV2(
            mail_ref_id=stable_mail_ref_id(account_id=account_id, protocol="imap", stable_identity=identity),
            account_id=account_id,
            protocol="imap",
            protocol_locator=locator,
            locator_version=1,
            thread_ref_id=thread_ref_id,
        )
        raw_to = ref.get("to", headers.get("to", ()))
        recipients = (str(raw_to),) if isinstance(raw_to, str) and raw_to else tuple(
            str(item) for item in list(raw_to or []) if str(item)
        )
        subject = str(headers.get("subject") or "")
        metadata = MailMessageMetadata(
            message_id_header=message_id,
            date=date,
            from_address=str(ref.get("from") or headers.get("from") or ""),
            to_addresses=recipients,
            subject=subject,
            subject_hash=str(ref.get("subject_hash") or subject_hash(subject)),
            size=size,
            content_hash=content_hash,
            mailbox_ref_ids=(str(locator["mailbox"]),) if locator["mailbox"] else (),
            keywords=tuple(str(item) for item in list(headers.get("keywords") or []) if str(item)),
            body_structure=dict(headers.get("body_structure") or {}),
        )
        return LegacyMailRecord(message_ref=normalized_ref, metadata=metadata)

    @staticmethod
    def row_from_v1(row: Mapping[str, Any]) -> LegacyMailRecord:
        return MailLegacyMapper.message_from_v1(
            dict(row.get("message_ref") or {}),
            dict(row.get("header_meta") or {}),
        )

    @staticmethod
    def conservative_fingerprint(record: LegacyMailRecord) -> str | None:
        metadata = record.metadata
        if not all((metadata.message_id_header, metadata.date, metadata.content_hash, metadata.size is not None)):
            return None
        return json.dumps(
            {
                "account_id": record.message_ref.account_id,
                "message_id": metadata.message_id_header,
                "date": metadata.date,
                "size": metadata.size,
                "content_hash": metadata.content_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def classify_match(candidate: LegacyMailRecord, existing: list[LegacyMailRecord]) -> MailDedupeDecision:
        locator_matches = [
            item
            for item in existing
            if item.message_ref.account_id == candidate.message_ref.account_id
            and item.message_ref.protocol == candidate.message_ref.protocol
            and dict(item.message_ref.protocol_locator) == dict(candidate.message_ref.protocol_locator)
            and item.message_ref.locator_version == candidate.message_ref.locator_version
        ]
        if len(locator_matches) == 1:
            return MailDedupeDecision("matched", "locator_alias_exact", locator_matches[0].message_ref.mail_ref_id)
        if len(locator_matches) > 1:
            return MailDedupeDecision("ambiguous", "duplicate_locator_alias")
        fingerprint = MailLegacyMapper.conservative_fingerprint(candidate)
        same_message_id = [
            item
            for item in existing
            if item.message_ref.account_id == candidate.message_ref.account_id
            and item.metadata.message_id_header
            and item.metadata.message_id_header == candidate.metadata.message_id_header
        ]
        if fingerprint is None:
            if same_message_id:
                return MailDedupeDecision("ambiguous", "dedupe_evidence_incomplete")
            return MailDedupeDecision("unmatched", "dedupe_evidence_incomplete")
        exact = [
            item
            for item in existing
            if MailLegacyMapper.conservative_fingerprint(item) == fingerprint
        ]
        if len(exact) == 1:
            return MailDedupeDecision("matched", "dedupe_strong_match", exact[0].message_ref.mail_ref_id, fingerprint)
        if len(exact) > 1:
            return MailDedupeDecision("ambiguous", "dedupe_multiple_strong_matches", fingerprint=fingerprint)
        return MailDedupeDecision("unmatched", "dedupe_no_strong_match", fingerprint=fingerprint)
