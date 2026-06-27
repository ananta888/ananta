from __future__ import annotations

import pytest

from agent.db_models import OidcIdentityLinkDB, UserDB
from agent.services.oidc_identity_link_service import OidcIdentityLinkService


class FakeLinks:
    def __init__(self) -> None:
        self.items: list[OidcIdentityLinkDB] = []

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


class FakeUsers:
    def __init__(self) -> None:
        self.items = {
            "alice": UserDB(username="alice", password_hash="x", role="user"),
            "bob": UserDB(username="bob", password_hash="x", role="viewer"),
        }

    def get_by_username(self, username: str):
        return self.items.get(username)


@pytest.fixture
def service() -> OidcIdentityLinkService:
    return OidcIdentityLinkService(FakeLinks(), FakeUsers())


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
