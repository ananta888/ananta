from __future__ import annotations

import pytest

from agent.db_models import OidcIdentityLinkDB, UserDB
from agent.models.oidc_identity_provisioning import (
    OidcIdentityProvisioningResult,
    OidcIdentityProvisioningStatus,
)
from agent.services.oidc_identity_link_service import (
    OidcAccountProvisioningError,
    OidcAccountProvisioningUnavailableError,
    OidcIdentityLinkService,
    OidcIdentityValidationError,
)


class FakeLinks:
    def __init__(self, users: "FakeUsers") -> None:
        self.items: list[OidcIdentityLinkDB] = []
        self.users = users

    def get_by_subject(self, issuer: str, subject: str):
        return next((x for x in self.items if x.issuer == issuer and x.subject == subject), None)

    def get_for_user(self, username: str, issuer: str):
        return next((x for x in self.items if x.username == username and x.issuer == issuer), None)

    def save(self, link: OidcIdentityLinkDB):
        self.items.append(link)
        return link

    def delete_for_user(self, username: str, issuer: str) -> bool:
        before = len(self.items)
        self.items = [x for x in self.items if not (x.username == username and x.issuer == issuer)]
        return len(self.items) != before

    def provision_user_with_link(
        self,
        *,
        user: UserDB,
        issuer: str,
        subject: str,
    ) -> OidcIdentityProvisioningResult:
        subject_link = self.get_by_subject(issuer, subject)
        if subject_link is not None:
            return OidcIdentityProvisioningResult(
                status=OidcIdentityProvisioningStatus.IDENTITY_ALREADY_LINKED,
                username=subject_link.username,
            )
        if self.users.get_by_username(user.username) is not None:
            return OidcIdentityProvisioningResult(
                status=OidcIdentityProvisioningStatus.USERNAME_CONFLICT,
            )
        self.users.items[user.username] = user
        self.items.append(
            OidcIdentityLinkDB(
                username=user.username,
                issuer=issuer,
                subject=subject,
            )
        )
        return OidcIdentityProvisioningResult(
            status=OidcIdentityProvisioningStatus.CREATED,
            username=user.username,
        )


class FakeUsers:
    def __init__(self) -> None:
        self.items = {
            "alice": UserDB(username="alice", password_hash="x", role="user"),
            "bob": UserDB(username="bob", password_hash="x", role="viewer"),
        }

    def get_by_username(self, username: str):
        return self.items.get(username)

    def save(self, user: UserDB):
        if user.username in self.items:
            raise ValueError("duplicate_username")
        self.items[user.username] = user
        return user


@pytest.fixture
def service() -> OidcIdentityLinkService:
    users = FakeUsers()
    return OidcIdentityLinkService(FakeLinks(users), users)


def test_link_is_explicit_and_resolves_to_existing_hub_user(service):
    link = service.link(username="alice", issuer="https://issuer", subject="kc-alice")

    assert link.username == "alice"
    assert service.resolve(issuer="https://issuer", subject="kc-alice").username == "alice"


def test_same_external_identity_cannot_link_to_two_hub_users(service):
    service.link(username="alice", issuer="https://issuer", subject="same-sub")

    with pytest.raises(ValueError, match="oidc_identity_already_linked"):
        service.link(username="bob", issuer="https://issuer", subject="same-sub")


def test_one_hub_user_cannot_silently_switch_subject(service):
    service.link(username="alice", issuer="https://issuer", subject="first")

    with pytest.raises(ValueError, match="hub_user_already_linked"):
        service.link(username="alice", issuer="https://issuer", subject="second")


def test_unlink_removes_only_requested_provider_link(service):
    service.link(username="alice", issuer="https://issuer", subject="kc-alice")

    assert service.unlink(username="alice", issuer="https://issuer") is True
    assert service.resolve(issuer="https://issuer", subject="kc-alice") is None


def test_auto_provision_creates_explicit_subject_binding(service):
    user = service.resolve_or_provision(
        username="new@example.test",
        issuer="https://issuer",
        subject="new-subject",
        role="viewer",
        password_hash="generated-hash",
    )

    assert user.username == "new@example.test"
    assert service.resolve(
        issuer="https://issuer",
        subject="new-subject",
    ).username == "new@example.test"


def test_auto_provision_never_claims_an_existing_unlinked_username(service):
    with pytest.raises(
        OidcAccountProvisioningError,
        match="oidc_local_account_requires_explicit_link",
    ):
        service.resolve_or_provision(
            username="alice",
            issuer="https://issuer",
            subject="different-subject",
            role="user",
            password_hash="generated-hash",
        )


def test_auto_provision_maps_persistence_failure_to_stable_unavailable_error() -> None:
    users = FakeUsers()
    links = FakeLinks(users)

    def unavailable(**_kwargs):
        return OidcIdentityProvisioningResult(
            status=OidcIdentityProvisioningStatus.PERSISTENCE_UNAVAILABLE,
        )

    links.provision_user_with_link = unavailable  # type: ignore[method-assign]
    service = OidcIdentityLinkService(links, users)

    with pytest.raises(
        OidcAccountProvisioningUnavailableError,
        match="oidc_identity_provisioning_unavailable",
    ):
        service.resolve_or_provision(
            username="new@example.test",
            issuer="https://issuer",
            subject="new-subject",
            role="viewer",
            password_hash="generated-hash",
        )


def test_external_subject_is_not_trimmed_or_coerced(service):
    with pytest.raises(OidcIdentityValidationError, match="oidc_subject_not_canonical"):
        service.resolve(issuer="https://issuer", subject=" subject ")

    with pytest.raises(OidcIdentityValidationError, match="oidc_subject_not_canonical"):
        service.resolve(issuer="https://issuer", subject=123)
