from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agent.services.jmap_contract_service import JmapSessionDocument
from agent.services.jmap_endpoint_policy import JmapEndpointPolicy, JmapEndpointPolicyError
from agent.services.jmap_http_transport import JmapHttpTransport, JmapTransportError
from agent.services.mail_provider_ports import (
    MailAttachment,
    MailProviderResult,
    VerifiedMailContentAccess,
)


@dataclass(frozen=True, slots=True)
class DownloadedMailBlob:
    attachment_ref: str
    name: str
    content_type: str
    content: bytes


class JmapBlobService:
    def __init__(
        self,
        *,
        session: JmapSessionDocument,
        transport: JmapHttpTransport,
        endpoint_policy: JmapEndpointPolicy,
        authorization_headers: dict[str, str],
        allowed_content_types: Sequence[str] = (
            "text/",
            "image/",
            "application/pdf",
            "application/json",
            "application/octet-stream",
        ),
        maximum_blob_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self._session = session
        self._transport = transport
        self._endpoint_policy = endpoint_policy
        self._headers = dict(authorization_headers)
        self._allowed_types = tuple(str(value).lower() for value in allowed_content_types)
        self._maximum_blob_bytes = max(1, int(maximum_blob_bytes))

    def download(
        self,
        attachment: MailAttachment,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[DownloadedMailBlob]:
        if getattr(access, "release_scope", "") not in {"attachment_ref", "full_body"}:
            return MailProviderResult(ok=False, reason_code="mail_content_scope_denied")
        if not all(
            str(getattr(access, field, "") or "")
            for field in ("account_id", "grant_ref", "artifact_ref", "policy_decision_ref")
        ):
            return MailProviderResult(ok=False, reason_code="mail_content_access_invalid")
        if str(attachment.blob_locator.get("account_id") or "") != str(access.account_id):
            return MailProviderResult(ok=False, reason_code="mail_content_access_account_mismatch")
        if str(getattr(access, "mail_ref_id", "") or "") != attachment.mail_ref_id:
            return MailProviderResult(ok=False, reason_code="mail_content_access_message_mismatch")
        blob_id = str(attachment.blob_locator.get("blob_id") or "")
        content_type = str(getattr(attachment, "content_type", "") or "application/octet-stream").lower()
        name = str(getattr(attachment, "filename", "") or "attachment")
        size = max(0, int(getattr(attachment, "size", 0) or 0))
        if not blob_id:
            return MailProviderResult(ok=False, reason_code="jmap_blob_id_missing")
        if size > self._maximum_blob_bytes:
            return MailProviderResult(ok=False, reason_code="jmap_blob_too_large")
        if not any(content_type == value or (value.endswith("/") and content_type.startswith(value)) for value in self._allowed_types):
            return MailProviderResult(ok=False, reason_code="jmap_blob_content_type_forbidden")
        try:
            endpoint = self._endpoint_policy.expand_template(
                self._session.download_url_template,
                variables={
                    "accountId": self._session.provider_account_id,
                    "blobId": blob_id,
                    "name": name,
                    "type": content_type,
                },
                trusted_origin=self._session.trusted_origin,
                purpose="download",
            )
            response = self._transport.request_bytes(
                method="GET",
                url=endpoint.url,
                headers={**self._headers, "Accept": content_type},
                purpose="download",
                trusted_origin=self._session.trusted_origin,
                maximum_response_bytes=self._maximum_blob_bytes,
            )
        except (JmapEndpointPolicyError, JmapTransportError) as exc:
            return MailProviderResult(
                ok=False,
                reason_code=exc.reason_code,
                retryable=bool(getattr(exc, "retryable", False)),
                retry_after_ms=getattr(exc, "retry_after_ms", None),
            )
        actual_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if actual_type != content_type:
            return MailProviderResult(ok=False, reason_code="jmap_blob_content_type_mismatch")
        return MailProviderResult(
            ok=True,
            reason_code="ok",
            value=DownloadedMailBlob(
                attachment_ref=str(getattr(attachment, "attachment_ref", "") or ""),
                name=_safe_filename(name),
                content_type=actual_type,
                content=response.body,
            ),
        )


def _safe_filename(value: str) -> str:
    clean = str(value or "attachment").replace("\\", "_").replace("/", "_").replace("\x00", "")
    return clean[:255] or "attachment"


__all__ = ["DownloadedMailBlob", "JmapBlobService"]
