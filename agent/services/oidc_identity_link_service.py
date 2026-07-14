"""Explicit OIDC-to-Hub account links.

The service owns the account-link policy.  Token validation remains in the
OIDC validator and Hub session issuance remains in user_session_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.db_models import OidcIdentityLinkDB, UserDB
from agent.models.oidc_identity_provisioning import (
    OidcIdentityProvisioningResult,
    OidcIdentityProvisioningStatus,
)
from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)
from agent.services.user_session_tokens import local_user_tenant_id

OIDC_ISSUER_MAX_LENGTH = 2048
OIDC_SUBJECT_MAX_LENGTH = 512


class OidcIdentityValidationError(ValueError):
    """Raised when an external OIDC principal is not a canonical identity."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class OidcAccountProvisioningError(ValueError):
    """Raised when safe automatic provisioning cannot establish ownership."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class OidcAccountProvisioningUnavailableError(RuntimeError):
    """Raised when persistence cannot complete a provisioning transaction."""

    def __init__(self) -> None:
        super().__init__("oidc_identity_provisioning_unavailable")
        self.reason_code = "oidc_identity_provisioning_unavailable"


@dataclass(frozen=True)
class OidcExternalIdentity:
    issuer: str
    subject: str


def validate_oidc_external_identity(
    *,
    issuer: Any,
    subject: Any,
) -> OidcExternalIdentity:
    """Validate an OIDC principal without coercing, trimming or truncating it."""

    canonical_issuer = validate_oidc_issuer(issuer)
    try:
        canonical_subject = require_canonical_identity(
            subject,
            field_name="oidc_subject",
            max_length=OIDC_SUBJECT_MAX_LENGTH,
        )
    except IdentityValidationError as exc:
        raise OidcIdentityValidationError(exc.reason_code) from exc
    return OidcExternalIdentity(
        issuer=canonical_issuer,
        subject=canonical_subject,
    )


def validate_oidc_issuer(issuer: Any) -> str:
    """Validate an issuer used by status/unlink operations."""

    try:
        return require_canonical_identity(
            issuer,
            field_name="oidc_issuer",
            max_length=OIDC_ISSUER_MAX_LENGTH,
        )
    except IdentityValidationError as exc:
        raise OidcIdentityValidationError(exc.reason_code) from exc


class IdentityLinkRepository(Protocol):
    def get_by_subject(self, issuer: str, subject: str) -> OidcIdentityLinkDB | None: ...
    def get_for_user(self, username: str, issuer: str) -> OidcIdentityLinkDB | None: ...
    def save(self, link: OidcIdentityLinkDB) -> OidcIdentityLinkDB: ...
    def delete_for_user(self, username: str, issuer: str) -> bool: ...
    def provision_user_with_link(
        self,
        *,
        user: UserDB,
        issuer: str,
        subject: str,
    ) -> OidcIdentityProvisioningResult: ...


class UserLookup(Protocol):
    def get_by_username(self, username: str) -> UserDB | None: ...


@dataclass(frozen=True)
class LinkResult:
    username: str
    issuer: str
    subject: str


class OidcIdentityLinkService:
    def __init__(self, links: IdentityLinkRepository, users: UserLookup) -> None:
        self._links = links
        self._users = users

    def link(self, *, username: str, issuer: str, subject: str) -> LinkResult:
        canonical_username = local_user_tenant_id(username)
        identity = validate_oidc_external_identity(issuer=issuer, subject=subject)
        user = self._users.get_by_username(canonical_username)
        if user is None:
            raise ValueError("hub_user_not_found")

        subject_link = self._links.get_by_subject(identity.issuer, identity.subject)
        if subject_link is not None and subject_link.username != canonical_username:
            raise ValueError("oidc_identity_already_linked")

        user_link = self._links.get_for_user(canonical_username, identity.issuer)
        if user_link is not None:
            if user_link.subject != identity.subject:
                raise ValueError("hub_user_already_linked")
            return LinkResult(
                username=canonical_username,
                issuer=identity.issuer,
                subject=identity.subject,
            )

        self._links.save(
            OidcIdentityLinkDB(
                username=canonical_username,
                issuer=identity.issuer,
                subject=identity.subject,
            )
        )
        return LinkResult(
            username=canonical_username,
            issuer=identity.issuer,
            subject=identity.subject,
        )

    def resolve(self, *, issuer: str, subject: str) -> UserDB | None:
        identity = validate_oidc_external_identity(issuer=issuer, subject=subject)
        link = self._links.get_by_subject(identity.issuer, identity.subject)
        if link is None:
            return None
        user = self._users.get_by_username(link.username)
        if user is not None:
            local_user_tenant_id(user.username)
        return user

    def resolve_or_provision(
        self,
        *,
        username: str,
        issuer: str,
        subject: str,
        role: str,
        password_hash: str,
    ) -> UserDB:
        """Resolve a bound subject or provision a new, explicitly bound user.

        An existing unlinked username is never claimed automatically. This is
        the critical boundary that prevents two OIDC subjects sharing an email
        address from silently receiving the same local tenant.
        """

        identity = validate_oidc_external_identity(issuer=issuer, subject=subject)
        linked_user = self.resolve(
            issuer=identity.issuer,
            subject=identity.subject,
        )
        if linked_user is not None:
            return linked_user

        canonical_username = local_user_tenant_id(username)
        new_user = UserDB(
            username=canonical_username,
            password_hash=password_hash,
            role=role,
            mfa_secret=None,
            mfa_enabled=False,
            mfa_backup_codes=[],
            failed_login_attempts=0,
            lockout_until=None,
        )
        result = self._links.provision_user_with_link(
            user=new_user,
            issuer=identity.issuer,
            subject=identity.subject,
        )
        if result.status in {
            OidcIdentityProvisioningStatus.CREATED,
            OidcIdentityProvisioningStatus.IDENTITY_ALREADY_LINKED,
        }:
            persisted_user = (
                self._users.get_by_username(result.username)
                if result.username is not None
                else None
            )
            if persisted_user is None:
                raise OidcAccountProvisioningError("oidc_identity_link_conflict")
            local_user_tenant_id(persisted_user.username)
            return persisted_user
        if result.status == OidcIdentityProvisioningStatus.USERNAME_CONFLICT:
            raise OidcAccountProvisioningError(
                "oidc_local_account_requires_explicit_link"
            )
        if result.status == OidcIdentityProvisioningStatus.PERSISTENCE_UNAVAILABLE:
            raise OidcAccountProvisioningUnavailableError()
        raise OidcAccountProvisioningError("oidc_identity_link_conflict")

    def status(self, *, username: str, issuer: str) -> LinkResult | None:
        canonical_username = local_user_tenant_id(username)
        canonical_issuer = validate_oidc_issuer(issuer)
        link = self._links.get_for_user(canonical_username, canonical_issuer)
        if link is None:
            return None
        return LinkResult(username=link.username, issuer=link.issuer, subject=link.subject)

    def unlink(self, *, username: str, issuer: str) -> bool:
        canonical_username = local_user_tenant_id(username)
        canonical_issuer = validate_oidc_issuer(issuer)
        return self._links.delete_for_user(canonical_username, canonical_issuer)


__all__ = [
    "LinkResult",
    "OIDC_ISSUER_MAX_LENGTH",
    "OIDC_SUBJECT_MAX_LENGTH",
    "OidcAccountProvisioningError",
    "OidcAccountProvisioningUnavailableError",
    "OidcExternalIdentity",
    "OidcIdentityLinkService",
    "OidcIdentityValidationError",
    "validate_oidc_external_identity",
    "validate_oidc_issuer",
]
