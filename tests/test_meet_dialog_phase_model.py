"""Deterministic phase transitions, terminal projection and closed metadata."""

from copy import deepcopy

import pytest

from agent.models.meet_dialog_phase import projection, publication_observation, queued, transition, validate_record
from agent.services.meet_contract import MeetError

BINDING = "a" * 64
MEMBERSHIP = {"session_id": "ms_" + "a" * 32, "peer_id": "a" * 16}


def joined():
    record = queued(BINDING, 100_000)
    record = transition(record, "admitted", 100_001)
    record = transition(record, "connecting", 100_002)
    return transition(record, "joined", 100_003, membership=MEMBERSHIP)


def observed(revision=1, active=True, now=100_004):
    state = {
        "publicationRevision": revision,
        "lease": {"expiresAt": 200_000},
        "publications": [{"publicationId": "screen", "source": "screen", "publicationEpoch": revision}]
        if active
        else [],
    }
    return publication_observation(state, now, 300)


def test_full_lifecycle_noop_terminal_revision_and_publication_freshness():
    record = joined()
    assert record["revision"] == 4
    assert transition(record, "joined", 100_004, membership=MEMBERSHIP) is record
    record = transition(record, "publishing", 100_004, observation=observed())
    assert record["revision"] == 5
    assert projection("task", "in_progress", record, 105_003)["observation_fresh"]
    assert not projection("task", "in_progress", record, 105_004)["observation_fresh"]
    assert not projection("task", "in_progress", record, 100_003)["observation_fresh"]
    record = transition(record, "joined", 100_005, observation=observed(2, False, 100_005))
    record = transition(record, "publishing", 100_006, observation=observed(3, True, 100_006))
    record = transition(record, "stopping", 100_007)
    for status in ("completed", "failed", "cancelled"):
        value = projection("task", status, record, 100_008)
        assert value["phase"] == status and value["revision"] == 9 and not value["observation_fresh"]
        assert value == projection("task", status, deepcopy(record), 100_009)
        assert "membership" not in value and "binding" not in value
    with pytest.raises(MeetError, match="transition_invalid"):
        transition(record, "joined", 100_009, membership=MEMBERSHIP)


@pytest.mark.parametrize("phase", ["joined", "publishing", "completed", "failed", "bogus"])
def test_queued_cannot_skip_admission_or_be_promoted_by_worker(phase):
    with pytest.raises(MeetError):
        transition(queued(BINDING, 1), phase, 2, membership=MEMBERSHIP)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "wrong"),
        ("binding", "b" * 64),
        ("revision", True),
        ("revision", 0),
        ("revision", 2**53),
        ("since", -1),
        ("since", float("nan")),
        ("phase", []),
        ("membership", None),
        ("membership", MEMBERSHIP | {"token": "never-store"}),
        ("membership", MEMBERSHIP | {"peer_id": "other"}),
        ("observation", {}),
    ],
)
def test_corrupt_persisted_metadata_fails_closed(field, value):
    with pytest.raises(MeetError):
        validate_record(joined() | {field: value}, BINDING)


@pytest.mark.parametrize("mutation", ["lease", "peer", "revision", "same_revision_content", "clock"])
def test_membership_and_publication_replay_cannot_rewrite_newer_observation(mutation):
    record = transition(joined(), "publishing", 100_010, observation=observed(3, True, 100_010))
    membership = MEMBERSHIP.copy()
    observation = observed(4, True, 100_011)
    if mutation == "lease":
        membership["session_id"] = "ms_" + "b" * 32
    elif mutation == "peer":
        membership["peer_id"] = "b" * 16
    elif mutation == "revision":
        observation = observed(2, True, 100_011)
    elif mutation == "same_revision_content":
        observation = observed(3, True, 100_011) | {"digest": "b" * 64}
    else:
        observation = observed(4, True, 100_009)
    with pytest.raises(MeetError):
        transition(record, "publishing", 100_011, membership=membership, observation=observation)


def test_revision_overflow_clock_rollback_and_unsupported_task_status_are_bounded():
    record = joined() | {"revision": 2**53 - 2}
    with pytest.raises(MeetError, match="revision_exhausted"):
        transition(record, "stopping", 100_004)
    with pytest.raises(MeetError, match="clock_invalid"):
        transition(joined(), "stopping", 100_002)
    with pytest.raises(MeetError, match="task_inactive"):
        projection("task", "queued", joined(), 100_004)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sources", ["screen", "screen"]),
        ("sources", ["human-camera"]),
        ("sources", [[]]),
        ("digest", "bad"),
        ("revision", True),
        ("valid_until", 100_004),
        ("observed_at", None),
    ],
)
def test_observation_subcontract_rejects_mutation(field, value):
    with pytest.raises(MeetError):
        transition(joined(), "publishing", 100_004, observation=observed() | {field: value})
