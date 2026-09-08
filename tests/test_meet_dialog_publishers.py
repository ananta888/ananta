"""Bounded configured selection never discovers or falls back across scopes."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.bootstrap.meet_dialog_publishers import configured_dialog_workers
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_publishers import MeetDialogPublishers
from agent.services.meet_media_transport import HttpMediaWorker
from tests.test_meet_role_assignment_policy import facts

FIRST, SECOND = "http://publisher:8091", "http://second:8091"
pytestmark = pytest.mark.timeout(45)
FIELDS = {"organization_id": "org", "unit_id": "unit", "team_id": "team", "role_slot_id": "slot"}


def test_exact_unique_assignment_selects_second_target_without_directory_writes():
    rows = Mock()
    rows.read.side_effect = lambda scope, origin: replace(facts(), publisher_url=SECOND) if origin == SECOND else None
    service = MeetDialogPublishers(rows, [FIRST, SECOND], FIRST)
    assert service.select("tenant", "project", FIELDS) == SECOND
    assert rows.read.call_count == 2
    for call in rows.read.call_args_list:
        scope, origin = call.args
        assert (scope.tenant_id, scope.project_id, scope.organization_id, scope.role_slot_id) == (
            "tenant",
            "project",
            "org",
            "slot",
        )
        assert origin in (FIRST, SECOND)
    rows.reset_mock()
    assert service.select("tenant", "project", {}) == FIRST
    rows.read.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "ambiguous", "unavailable", "wrong_type", "wrong_origin", "revoked"])
def test_bad_directory_never_becomes_first_available_fallback(case):
    rows = Mock()

    def read(scope, origin):
        if case == "missing":
            return None
        if case == "unavailable":
            raise RuntimeError("PRIVATE_DIRECTORY_DETAIL")
        if case == "wrong_type":
            return {"publisher_url": origin}
        if case == "wrong_origin":
            return replace(facts(), publisher_url="http://foreign:8091")
        return replace(facts(), publisher_url=origin, lifecycle="ended" if case == "revoked" else "active")

    rows.read.side_effect = read
    with pytest.raises(MeetError, match="^meet_dialog_publisher_selection_denied$"):
        MeetDialogPublishers(rows, [FIRST, SECOND], FIRST).select("tenant", "project", FIELDS)


def test_organization_without_role_cannot_use_legacy_default():
    rows = Mock()
    with pytest.raises(MeetError, match="principal_assignment_required"):
        MeetDialogPublishers(rows, [FIRST], FIRST).select("tenant", "project", {"organization_id": "org"})
    rows.read.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "{}",
        '"string"',
        "[1]",
        "[null]",
        "[[]]",
        "[true]",
        "[",
        " " * 8193,
        json.dumps([SECOND + "/v1/turns"] * 2),
        json.dumps(["http://user:secret@host:80/v1/turns"]),
        json.dumps(["http://host:80/v1/turns\n"]),
        json.dumps([f"http://h{i}:80/v1/turns" for i in range(8)]),
    ],
)
def test_closed_bounded_configuration(raw):
    default = HttpMediaWorker(FIRST + "/v1/turns", b"synthetic" * 4)
    with pytest.raises(ValueError, match="^meet_dialog_publishers_invalid$"):
        configured_dialog_workers(raw, default, Mock())


def test_absent_option_preserves_original_transport_and_explicit_option_shares_only_existing_trust_group():
    assert configured_dialog_workers(None, Mock(), Mock()) == (None, None)
    key = b"synthetic" * 4
    default = HttpMediaWorker(FIRST + "/v1/turns", key)
    selector, workers = configured_dialog_workers(json.dumps([default.endpoint, SECOND + "/v1/turns"]), default, Mock())
    assert selector.origins == (FIRST, SECOND) and workers[FIRST] is default
    assert workers[SECOND].key is key


@pytest.mark.parametrize("origins", [[], [FIRST, FIRST], [FIRST] * 9, "not-a-list", ["https://host:80"]])
def test_invalid_selection_allowlist(origins):
    with pytest.raises(ValueError):
        MeetDialogPublishers(Mock(), origins, FIRST)
