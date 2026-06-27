"""Explicit OIDC-to-Hub account links.

The service owns the account-link policy.  Token validation remains in the
OIDC validator and Hub session issuance remains in user_session_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.db_models import OidcIdentityLinkDB, UserDB


class IdentityLinkRepository(Protocol):
    def get_by_subject(self, issuer: str, subject: str) -> OidcIdentityLinkDB | None: ...
    def get_for_user(self, username: str, issuer: str) -> OidcIdentityLinkDB | None: ...
    def save(self, link: OidcIdentityLinkDB) -> OidcIdentityLinkDB: ...
    def delete_for_user(self, username: str, issuer: str) -> bool: ...


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
        user = self._users.get_by_username(username)
        if user is None:
            raise ValueError("hub_user_not_found")

        subject_link = self._links.get_by_subject(issuer, subject)
        if subject_link is not None and subject_link.username != username:
            raise ValueError("oidc_identity_already_linked")

        user_link = self._links.get_for_user(username, issuer)
        if user_link is not None:
            if user_link.subject != subject:
                raise ValueError("hub_user_already_linked")
            return LinkResult(username=username, issuer=issuer, subject=subject)

        self._links.save(
            OidcIdentityLinkDB(
                username=username,
                issuer=issuer,
                subject=subject,
            )
        )
        return LinkResult(username=username, issuer=issuer, subject=subject)

    def resolve(self, *, issuer: str, subject: str) -> UserDB | None:
        link = self._links.get_by_subject(issuer, subject)
        if link is None:
            return None
        return self._users.get_by_username(link.username)

    def status(self, *, username: str, issuer: str) -> LinkResult | None:
        link = self._links.get_for_user(username, issuer)
        if link is None:
            return None
        return LinkResult(username=link.username, issuer=link.issuer, subject=link.subject)

    def unlink(self, *, username: str, issuer: str) -> bool:
        return self._links.delete_for_user(username, issuer)
