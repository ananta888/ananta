from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Generic, Mapping, Protocol, Sequence, TypeVar

from agent.services.mail_contract_service import MailAccountV2, MailMessageMetadata, MailMessageRefV2

T = TypeVar("T")
_ACCESS_ISSUER = object()
_CONTENT_SCOPES = {"body_excerpt", "full_body", "attachment_ref"}


@dataclass(frozen=True, slots=True)
class MailProviderResult(Generic[T]):
    ok: bool
    reason_code: str
    value: T | None = None
    retryable: bool = False
    retry_after_ms: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, value: T | None = None, *, reason_code: str = "ok") -> MailProviderResult[T]:
        return cls(ok=True, reason_code=reason_code, value=value)

    @classmethod
    def failure(
        cls,
        reason_code: str,
        *,
        retryable: bool = False,
        retry_after_ms: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> MailProviderResult[T]:
        return cls(
            ok=False,
            reason_code=str(reason_code),
            retryable=bool(retryable),
            retry_after_ms=retry_after_ms,
            details=dict(details or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason_code": self.reason_code,
            "value": self.value,
            "retryable": self.retryable,
            "retry_after_ms": self.retry_after_ms,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True, repr=False)
class MailAuthMaterial:
    username: str
    credential: str

    def __repr__(self) -> str:
        return "MailAuthMaterial(username='[REDACTED]', credential='[REDACTED]')"


@dataclass(frozen=True, slots=True)
class MailProviderSession:
    session_id: str
    account_id: str
    protocol: str
    provider_account_id: str = ""


@dataclass(frozen=True, slots=True)
class MailProviderCapabilities:
    provider: str
    using: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()
    limits: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MailMailbox:
    mailbox_ref_id: str
    name: str
    role: str = ""
    parent_ref_id: str = ""
    sort_order: int = 0
    total_emails: int = 0
    unread_emails: int = 0
    provider_locator: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class MailQuery:
    filters: Mapping[str, Any] = field(default_factory=dict)
    sort: tuple[str, ...] = ()
    limit: int = 50
    position: int = 0


@dataclass(frozen=True, slots=True)
class MailQueryPage:
    message_ref_ids: tuple[str, ...]
    total: int | None = None
    next_position: int | None = None
    query_state: str = ""


@dataclass(frozen=True, slots=True)
class MailMessage:
    message_ref: MailMessageRefV2
    metadata: MailMessageMetadata


@dataclass(frozen=True, slots=True)
class MailBody:
    mail_ref_id: str
    text_body: str = ""
    html_body: str = ""
    content_type: str = "text/plain"
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class MailAttachment:
    attachment_ref: str
    mail_ref_id: str
    filename: str
    content_type: str
    size: int
    blob_locator: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class MailContentAccessRequest:
    account_id: str
    workspace_id: str
    artifact_ref: str
    mail_ref_id: str
    grant_ref: str
    release_scope: str


@dataclass(frozen=True, slots=True)
class MailContentAccessDecision:
    allowed: bool
    reason_code: str
    policy_decision_ref: str = ""
    expires_at: str = ""
    nonce: str = ""


@dataclass(frozen=True, slots=True, init=False)
class VerifiedMailContentAccess:
    account_id: str
    workspace_id: str
    artifact_ref: str
    mail_ref_id: str
    grant_ref: str
    release_scope: str
    policy_decision_ref: str
    expires_at: str
    nonce: str

    def __init__(
        self,
        *,
        account_id: str,
        workspace_id: str,
        artifact_ref: str,
        mail_ref_id: str,
        grant_ref: str,
        release_scope: str,
        policy_decision_ref: str,
        expires_at: str,
        nonce: str,
        _issuer: object,
    ) -> None:
        if _issuer is not _ACCESS_ISSUER:
            raise TypeError("verified_mail_content_access_requires_verifier")
        for name, value in {
            "account_id": account_id,
            "workspace_id": workspace_id,
            "artifact_ref": artifact_ref,
            "mail_ref_id": mail_ref_id,
            "grant_ref": grant_ref,
            "release_scope": release_scope,
            "policy_decision_ref": policy_decision_ref,
            "expires_at": expires_at,
            "nonce": nonce,
        }.items():
            object.__setattr__(self, name, str(value))


class MailContentAccessPolicy(Protocol):
    def authorize(self, request: MailContentAccessRequest) -> MailProviderResult[MailContentAccessDecision]:
        ...


class MailContentAccessVerifier:
    def __init__(
        self,
        *,
        policy: MailContentAccessPolicy,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._now = now or (lambda: datetime.now(UTC))

    def verify(self, request: MailContentAccessRequest) -> MailProviderResult[VerifiedMailContentAccess]:
        if request.release_scope not in _CONTENT_SCOPES:
            return MailProviderResult.failure("mail_content_scope_invalid")
        if not all(
            str(value).strip()
            for value in (
                request.account_id,
                request.workspace_id,
                request.artifact_ref,
                request.mail_ref_id,
                request.grant_ref,
            )
        ):
            return MailProviderResult.failure("mail_content_access_context_incomplete")
        if not request.artifact_ref.startswith(f"mail://{request.mail_ref_id}"):
            return MailProviderResult.failure("mail_content_artifact_mismatch")
        decision_result = self._policy.authorize(request)
        decision = decision_result.value
        if not decision_result.ok or decision is None or not decision.allowed:
            return MailProviderResult.failure(
                decision.reason_code if decision is not None else decision_result.reason_code or "mail_content_access_denied"
            )
        if not all((decision.policy_decision_ref, decision.expires_at, decision.nonce)):
            return MailProviderResult.failure("mail_content_access_decision_incomplete")
        try:
            expires = datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return MailProviderResult.failure("mail_content_access_expiry_invalid")
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= self._now():
            return MailProviderResult.failure("mail_content_access_expired")
        return MailProviderResult.success(
            VerifiedMailContentAccess(
                account_id=request.account_id,
                workspace_id=request.workspace_id,
                artifact_ref=request.artifact_ref,
                mail_ref_id=request.mail_ref_id,
                grant_ref=request.grant_ref,
                release_scope=request.release_scope,
                policy_decision_ref=decision.policy_decision_ref,
                expires_at=decision.expires_at,
                nonce=decision.nonce,
                _issuer=_ACCESS_ISSUER,
            ),
            reason_code="mail_content_access_verified",
        )


@dataclass(frozen=True, slots=True)
class MailKeywordChange:
    account_id: str
    message_ref: MailMessageRefV2
    add_keywords: tuple[str, ...]
    remove_keywords: tuple[str, ...]
    intent_ref: str
    audit_ref: str


@dataclass(frozen=True, slots=True)
class MailMoveRequest:
    account_id: str
    message_ref: MailMessageRefV2
    destination_mailbox_ref_ids: tuple[str, ...]
    intent_ref: str
    audit_ref: str


@dataclass(frozen=True, slots=True)
class MailDeleteRequest:
    account_id: str
    message_ref: MailMessageRefV2
    permanent: bool
    intent_ref: str
    audit_ref: str
    confirmation_ref: str = ""


@dataclass(frozen=True, slots=True)
class MailMutationItem:
    mail_ref_id: str
    ok: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class MailMutationReport:
    items: tuple[MailMutationItem, ...]
    old_state: str = ""
    new_state: str = ""


@dataclass(frozen=True, slots=True)
class MailSyncCursor:
    account_id: str
    protocol: str
    scope: str = "default"
    mailbox_state: str = ""
    email_state: str = ""
    query_state: str = ""


@dataclass(frozen=True, slots=True)
class MailSyncDelta:
    cursor: MailSyncCursor
    created: tuple[MailMessage, ...] = ()
    updated: tuple[MailMessage, ...] = ()
    destroyed_mail_ref_ids: tuple[str, ...] = ()
    rebuild_required: bool = False


class MailLifecyclePort(Protocol):
    def connect(
        self, account: MailAccountV2, auth: MailAuthMaterial
    ) -> MailProviderResult[MailProviderSession]:
        ...

    def disconnect(self, session: MailProviderSession) -> MailProviderResult[None]:
        ...


class MailCapabilitiesPort(Protocol):
    def capabilities(self, session: MailProviderSession) -> MailProviderResult[MailProviderCapabilities]:
        ...


class MailReadPort(Protocol):
    def list_mailboxes(self, session: MailProviderSession) -> MailProviderResult[tuple[MailMailbox, ...]]:
        ...

    def query_messages(self, session: MailProviderSession, query: MailQuery) -> MailProviderResult[MailQueryPage]:
        ...

    def get_messages(
        self,
        session: MailProviderSession,
        ids: Sequence[str],
        properties: Sequence[str] = (),
    ) -> MailProviderResult[tuple[MailMessage, ...]]:
        ...


class MailBodyPort(Protocol):
    def get_body(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[MailBody]:
        ...

    def get_attachments(
        self,
        session: MailProviderSession,
        message_ref: MailMessageRefV2,
        *,
        access: VerifiedMailContentAccess,
    ) -> MailProviderResult[tuple[MailAttachment, ...]]:
        ...


class MailMutationPort(Protocol):
    def set_keywords(
        self,
        session: MailProviderSession,
        changes: Sequence[MailKeywordChange],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        ...

    def move_messages(
        self,
        session: MailProviderSession,
        moves: Sequence[MailMoveRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        ...

    def delete_messages(
        self,
        session: MailProviderSession,
        deletes: Sequence[MailDeleteRequest],
        *,
        if_in_state: str | None = None,
    ) -> MailProviderResult[MailMutationReport]:
        ...


class MailSyncPort(Protocol):
    def sync(
        self,
        session: MailProviderSession,
        cursor: MailSyncCursor | None,
        policy: str,
    ) -> MailProviderResult[MailSyncDelta]:
        ...


@dataclass(frozen=True, slots=True)
class MailProviderBinding:
    protocol: str
    lifecycle: MailLifecyclePort
    capabilities: MailCapabilitiesPort
    reader: MailReadPort
    body: MailBodyPort | None = None
    mutator: MailMutationPort | None = None
    sync: MailSyncPort | None = None


class MailProviderFactory(Protocol):
    def create(self, account: MailAccountV2) -> MailProviderResult[MailProviderBinding]:
        ...
