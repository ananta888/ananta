"""Headless revision CAS, tenancy and persisted-payload integrity checks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, update

from agent.models.persona_media import PersonaMediaProfile
from agent.repositories.persona_media import SqlPersonaProfiles, profiles


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'profiles.db'}")
    result = SqlPersonaProfiles(engine)
    result.initialize()
    yield result
    engine.dispose()


def profile(**changes):
    return PersonaMediaProfile(
        **(
            dict(
                tenant_id="tenant",
                project_id="project",
                owner_kind="team",
                owner_id="team",
                persona_id="presentation",
                revision=1,
            )
            | changes
        )
    )


def read(repository, value, **changes):
    return repository.get(
        **(
            dict(
                tenant_id=value.tenant_id,
                project_id=value.project_id,
                owner_kind=value.owner_kind,
                owner_id=value.owner_id,
                revision=value.revision,
                content_hash=value.content_hash(),
            )
            | changes
        )
    )


def test_append_preserves_prior_revision_and_cas_rejects_stale_writes(repository):
    first, second = profile(), profile(revision=2, persona_id="new-presentation")
    repository.append(first, expected_revision=0)
    repository.append(second, expected_revision=1)
    assert read(repository, first) == first and read(repository, second) == second
    with pytest.raises(ValueError, match="conflict"):
        repository.append(profile(revision=2, persona_id="stale-writer"), expected_revision=1)
    assert read(repository, second) == second
    repository.append(profile(revision=3), expected_revision=2)


@pytest.mark.parametrize(
    "changes", [{"tenant_id": "foreign"}, {"project_id": "foreign"}, {"owner_id": "foreign"}, {"revision": 2}]
)
def test_revision_lookup_never_falls_back_to_other_owner_or_scope(repository, changes):
    value = profile()
    repository.append(value, expected_revision=0)
    with pytest.raises(ValueError, match="unavailable"):
        read(repository, value, **changes)


def test_duplicate_first_revision_is_never_overwritten(repository):
    value = profile()
    repository.append(value, expected_revision=0)
    with pytest.raises(ValueError, match="conflict"):
        repository.append(profile(persona_id="replacement"), expected_revision=0)
    assert read(repository, value) == value


def test_two_concurrent_writers_cannot_claim_the_same_next_revision(repository):
    repository.append(profile(), expected_revision=0)
    barrier = Barrier(2)

    def append(persona):
        barrier.wait(timeout=3)
        value = profile(revision=2, persona_id=persona)
        try:
            repository.append(value, expected_revision=1)
            return value
        except ValueError as error:
            assert str(error) == "persona_revision_conflict"
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(append, ("first", "second")))
    winners = [value for value in values if value is not None]
    assert len(winners) == 1 and read(repository, winners[0]) == winners[0]


def test_expected_hash_and_stored_payload_are_both_checked(repository):
    value = profile()
    repository.append(value, expected_revision=0)
    with pytest.raises(ValueError, match="integrity_failed"):
        read(repository, value, content_hash="b" * 64)
    with repository.engine.begin() as connection:
        connection.execute(update(profiles).values(payload=profile(persona_id="mutated").model_dump_json()))
    with pytest.raises(ValueError, match="integrity_failed"):
        read(repository, value)


@pytest.mark.parametrize("expected", [True, -1, 1, "0"])
def test_invalid_revision_transition_fails_before_writing(repository, expected):
    with pytest.raises(ValueError, match="revision_invalid"):
        repository.append(profile(), expected_revision=expected)
    repository.append(profile(), expected_revision=0)
