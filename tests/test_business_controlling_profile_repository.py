from __future__ import annotations

from dataclasses import replace

import pytest
from sqlmodel import SQLModel, create_engine

from agent.repositories.business_controlling_profiles import (
    BusinessControllingProfilePersistenceError,
    SqlBusinessControllingProfileRepository,
)
from agent.services.business_controlling_import_service import (
    BusinessControllingImportService,
    ColumnProfile,
    TabularProfile,
)


def _profile() -> TabularProfile:
    return TabularProfile(
        source_revision_id="srev_" + "a" * 64,
        revision_digest="b" * 64,
        row_count=2,
        duplicate_row_count=0,
        columns=(ColumnProfile("amount", "decimal", 0, 0),),
        profile_digest="c" * 64,
    )


def test_profile_and_mapping_are_restart_stable_and_idempotent(tmp_path) -> None:
    database = create_engine(f"sqlite:///{tmp_path / 'profiles.db'}")
    SQLModel.metadata.create_all(database)
    repository = SqlBusinessControllingProfileRepository(database, clock=lambda: 1.0)
    profile = _profile()
    assert repository.append_profile(tenant_id="tenant-a", project_id="project-a", profile=profile) == profile
    restarted = SqlBusinessControllingProfileRepository(database, clock=lambda: 2.0)
    assert restarted.append_profile(tenant_id="tenant-a", project_id="project-a", profile=profile) == profile
    confirmation = BusinessControllingImportService.confirm_mapping(
        profile, {"amount": "amount"}, confirmed_by="operator-a"
    )
    assert restarted.append_mapping(
        tenant_id="tenant-a", project_id="project-a", confirmation=confirmation
    ) == confirmation
    assert repository.append_mapping(
        tenant_id="tenant-a", project_id="project-a", confirmation=confirmation
    ) == confirmation


def test_mapping_conflict_and_cross_tenant_binding_fail_closed(tmp_path) -> None:
    database = create_engine(f"sqlite:///{tmp_path / 'profiles.db'}")
    SQLModel.metadata.create_all(database)
    repository = SqlBusinessControllingProfileRepository(database)
    profile = _profile()
    repository.append_profile(tenant_id="tenant-a", project_id="project-a", profile=profile)
    confirmation = BusinessControllingImportService.confirm_mapping(
        profile, {"amount": "amount"}, confirmed_by="operator-a"
    )
    repository.append_mapping(tenant_id="tenant-a", project_id="project-a", confirmation=confirmation)
    with pytest.raises(BusinessControllingProfilePersistenceError, match="mapping_identity_conflict"):
        repository.append_mapping(
            tenant_id="tenant-a",
            project_id="project-a",
            confirmation=replace(
                confirmation,
                confirmed_by="operator-b",
                confirmation_digest="d" * 64,
            ),
        )
    with pytest.raises(BusinessControllingProfilePersistenceError, match="profile_identity_conflict"):
        repository.append_profile(tenant_id="tenant-b", project_id="project-a", profile=profile)
