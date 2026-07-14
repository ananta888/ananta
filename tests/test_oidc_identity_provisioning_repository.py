from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError

from agent.db_models import OidcIdentityLinkDB, UserDB
from agent.models.oidc_identity_provisioning import (
    OidcIdentityProvisioningStatus,
)
from agent.repositories.auth import OidcIdentityLinkRepository, UserRepository


def _new_user(username: str) -> UserDB:
    return UserDB(username=username, password_hash="generated", role="viewer")


def test_atomic_oidc_provisioning_creates_user_and_link_together() -> None:
    links = OidcIdentityLinkRepository()
    users = UserRepository()

    result = links.provision_user_with_link(
        user=_new_user("atomic@example.test"),
        issuer="https://issuer.example",
        subject="atomic-subject",
    )

    assert result.status == OidcIdentityProvisioningStatus.CREATED
    assert users.get_by_username("atomic@example.test") is not None
    assert links.get_by_subject("https://issuer.example", "atomic-subject") is not None


def test_atomic_oidc_provisioning_rolls_back_user_when_link_insert_fails() -> None:
    links = OidcIdentityLinkRepository()
    users = UserRepository()

    def reject_link_insert(*_args, **_kwargs):
        raise IntegrityError("forced_link_conflict", {}, RuntimeError("forced"))

    event.listen(OidcIdentityLinkDB, "before_insert", reject_link_insert)
    try:
        result = links.provision_user_with_link(
            user=_new_user("must-not-orphan@example.test"),
            issuer="https://issuer.example",
            subject="conflicting-subject",
        )
    finally:
        event.remove(OidcIdentityLinkDB, "before_insert", reject_link_insert)

    assert result.status == OidcIdentityProvisioningStatus.LINK_CONFLICT
    assert users.get_by_username("must-not-orphan@example.test") is None
    assert links.get_by_subject("https://issuer.example", "conflicting-subject") is None


def test_atomic_oidc_provisioning_hides_operational_failure_and_rolls_back() -> None:
    links = OidcIdentityLinkRepository()
    users = UserRepository()

    def reject_link_insert(*_args, **_kwargs):
        raise OperationalError("forced_database_outage", {}, RuntimeError("forced"))

    event.listen(OidcIdentityLinkDB, "before_insert", reject_link_insert)
    try:
        result = links.provision_user_with_link(
            user=_new_user("unavailable@example.test"),
            issuer="https://issuer.example",
            subject="unavailable-subject",
        )
    finally:
        event.remove(OidcIdentityLinkDB, "before_insert", reject_link_insert)

    assert result.status == OidcIdentityProvisioningStatus.PERSISTENCE_UNAVAILABLE
    assert users.get_by_username("unavailable@example.test") is None
    assert links.get_by_subject("https://issuer.example", "unavailable-subject") is None
