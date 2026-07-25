from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from agent.services.jmap_client_service import JmapClient
from agent.services.mail_contract_service import MailMessageRefV2
from agent.services.mail_html_sanitizer import MailHtmlSanitizer
from agent.services.mail_provider_ports import (
    MailAttachment,
    MailBody,
    MailContentAccessRequest,
    MailProviderResult,
    MailProviderSession,
    VerifiedMailContentAccess,
)


class MailContentGrantVerifier(Protocol):
    """Hub-owned verifier client; the backend only consumes its signed decision."""

    def verify(
        self,
        request: MailContentAccessRequest,
    ) -> MailProviderResult[VerifiedMailContentAccess]:
        ...


class MailBodyService:
    def __init__(
        self,
        *,
        client: JmapClient,
        local_account_id: str,
        sanitizer: MailHtmlSanitizer | None = None,
        maximum_body_bytes: int = 1024 * 1024,
        now: Any = None,
    ) -> None:
        self._client = client
        self._local_account_id = str(local_account_id)
        self._sanitizer = sanitizer or MailHtmlSanitizer()
        self._maximum_body_bytes = max(1, int(maximum_body_bytes))
        self._now = now or (lambda: datetime.now(UTC))

    def get_body(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[MailBody]:
        invalid = self._validate(session, message_ref, access, allowed_scopes={"body_excerpt", "full_body"})
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        email_id = str(message_ref.protocol_locator.get("email_id") or "")
        fetched = self._client.get_objects(
            object_type="Email",
            provider_account_id=self._client.session.provider_account_id,
            ids=(email_id,),
            properties=("id", "textBody", "htmlBody", "bodyValues"),
            extra_arguments={
                "fetchTextBodyValues": True,
                "fetchHTMLBodyValues": True,
                "fetchAllBodyValues": False,
                "maxBodyValueBytes": self._maximum_body_bytes,
            },
        )
        if not fetched.ok or not fetched.value:
            return MailProviderResult(
                ok=False,
                reason_code=fetched.reason_code if not fetched.ok else "jmap_body_not_found",
                retryable=fetched.retryable,
                retry_after_ms=fetched.retry_after_ms,
            )
        raw = fetched.value[0]
        body_values = raw.get("bodyValues")
        if not isinstance(body_values, Mapping):
            return MailProviderResult(ok=False, reason_code="jmap_body_values_invalid")
        text, text_truncated = _collect_body(raw.get("textBody"), body_values)
        html_body, html_truncated = _collect_body(raw.get("htmlBody"), body_values)
        sanitized_html = self._sanitizer.sanitize(html_body) if html_body else ""
        body = MailBody(
            mail_ref_id=message_ref.mail_ref_id,
            text_body=text,
            html_body=sanitized_html,
            content_type="text/html" if sanitized_html else "text/plain",
            truncated=text_truncated or html_truncated,
        )
        return MailProviderResult(ok=True, reason_code="ok", value=body)

    def get_attachments(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[tuple[MailAttachment, ...]]:
        invalid = self._validate(
            session,
            message_ref,
            access,
            allowed_scopes={"attachment_ref", "full_body"},
        )
        if invalid:
            return MailProviderResult(ok=False, reason_code=invalid)
        email_id = str(message_ref.protocol_locator.get("email_id") or "")
        fetched = self._client.get_objects(
            object_type="Email",
            provider_account_id=self._client.session.provider_account_id,
            ids=(email_id,),
            properties=("id", "attachments"),
        )
        if not fetched.ok or not fetched.value:
            return MailProviderResult(
                ok=False,
                reason_code=fetched.reason_code if not fetched.ok else "jmap_message_not_found",
                retryable=fetched.retryable,
                retry_after_ms=fetched.retry_after_ms,
            )
        rows = fetched.value[0].get("attachments") or ()
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            return MailProviderResult(ok=False, reason_code="jmap_attachments_invalid")
        attachments = tuple(
            MailAttachment(
                attachment_ref=f"jmap-blob:{str(row.get('blobId') or '')}",
                mail_ref_id=message_ref.mail_ref_id,
                filename=_safe_filename(str(row.get("name") or "attachment")),
                content_type=str(row.get("type") or "application/octet-stream").lower(),
                size=max(0, int(row.get("size") or 0)),
                blob_locator={
                    "account_id": self._local_account_id,
                    "provider_account_id": self._client.session.provider_account_id,
                    "blob_id": str(row.get("blobId") or ""),
                },
            )
            for row in rows
            if str(row.get("blobId") or "")
        )
        return MailProviderResult(ok=True, reason_code="ok", value=attachments)

    def _validate(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        access: VerifiedMailContentAccess,
        *,
        allowed_scopes: set[str],
    ) -> str:
        if session.protocol != "jmap" or message_ref.protocol != "jmap":
            return "mail_provider_session_protocol_mismatch"
        if session.account_id != self._local_account_id or message_ref.account_id != self._local_account_id:
            return "mail_content_account_mismatch"
        if getattr(access, "account_id", "") != self._local_account_id:
            return "mail_content_access_account_mismatch"
        if getattr(access, "mail_ref_id", "") != message_ref.mail_ref_id:
            return "mail_content_access_message_mismatch"
        if getattr(access, "release_scope", "") not in allowed_scopes:
            return "mail_content_scope_denied"
        for field in ("grant_ref", "artifact_ref", "policy_decision_ref"):
            if not str(getattr(access, field, "") or ""):
                return "mail_content_access_invalid"
        if not str(message_ref.protocol_locator.get("email_id") or ""):
            return "jmap_email_locator_missing"
        try:
            expires = datetime.fromisoformat(str(access.expires_at).replace("Z", "+00:00"))
        except ValueError:
            return "mail_content_access_expiry_invalid"
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= self._now():
            return "mail_content_access_expired"
        return ""


def _collect_body(parts: Any, values: Mapping[str, Any]) -> tuple[str, bool]:
    if not isinstance(parts, list):
        return "", False
    output: list[str] = []
    truncated = False
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        part_id = str(part.get("partId") or "")
        body = values.get(part_id)
        if not isinstance(body, Mapping):
            continue
        output.append(str(body.get("value") or ""))
        truncated = truncated or bool(body.get("isTruncated"))
    return "\n".join(output), truncated


def _safe_filename(value: str) -> str:
    clean = str(value or "attachment").replace("\\", "_").replace("/", "_").replace("\x00", "")
    return clean[:255] or "attachment"


__all__ = ["MailBodyService", "MailContentGrantVerifier"]
