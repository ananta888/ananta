"""Durable HTTP command replay does not confer live room or Worker authority."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from agent.models.meet_dialog_start import start_fingerprints
from agent.repositories.meet_dialog_starts import SqlDialogStarts
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_starts import MeetDialogStarts
from agent.services.source_control_access_policy import HubSourcePrincipal

pytestmark = pytest.mark.timeout(45)
PRINCIPAL = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
PAYLOAD = {"capabilities": ["screen.publish"], "duration_seconds": 300, "chat_mode": "off"}
RECEIPT = {"schema": "ananta.meet-dialog-start.v1", "task_id": "task", "session_id": "session", "status": "connecting"}


@pytest.fixture(name="store")
def start_store(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "start-receipts.sqlite"))
    value = SqlDialogStarts(engine)
    value.initialize()
    yield value
    engine.dispose()


def system(store):
    starter, access = Mock(), Mock()
    starter.start.return_value = RECEIPT
    return MeetDialogStarts(starter, store, access), starter, access


def test_replays_original_receipt_after_coordinator_restart_without_dispatch(store):
    service, starter, access = system(store)
    assert service.start(PRINCIPAL, "project", PAYLOAD, "parent", "key") == (RECEIPT, False)
    restarted = MeetDialogStarts(starter, SqlDialogStarts(store.engine), access)
    # JSON field order is irrelevant, but request values are not normalized away.
    assert restarted.start(PRINCIPAL, "project", dict(reversed(list(PAYLOAD.items()))), "parent", "key") == (
        RECEIPT,
        True,
    )
    starter.start.assert_called_once_with(PRINCIPAL, "project", PAYLOAD, "parent")
    assert access.require_write_access.call_count == 2


def test_changed_payload_conflicts_but_owner_tenant_project_parent_and_key_are_isolated(store):
    service, starter, _ = system(store)
    service.start(PRINCIPAL, "project", PAYLOAD, "parent", "key")
    with pytest.raises(MeetError, match="^meet_dialog_idempotency_conflict$"):
        service.start(PRINCIPAL, "project", PAYLOAD | {"duration_seconds": 600}, "parent", "key")
    for principal, project, parent, key in [
        (replace(PRINCIPAL, subject_id="another"), "project", "parent", "key"),
        (replace(PRINCIPAL, tenant_id="another"), "project", "parent", "key"),
        (PRINCIPAL, "another", "parent", "key"),
        (PRINCIPAL, "project", "another", "key"),
        (PRINCIPAL, "project", "parent", "another"),
    ]:
        assert service.start(principal, project, PAYLOAD, parent, key)[1] is False
    assert starter.start.call_count == 6


@pytest.mark.parametrize("key", ["", " ", "comma,key", "x" * 161, "ä", None, 1, []])
def test_invalid_keys_never_claim_or_invoke_the_dialog(key):
    store = Mock()
    service, starter, _ = system(store)
    with pytest.raises(MeetError, match="^meet_dialog_idempotency_key_invalid$"):
        service.start(PRINCIPAL, "project", PAYLOAD, "", key)
    store.claim.assert_not_called()
    starter.start.assert_not_called()


def test_access_loss_blocks_even_completed_replay_before_storage_read(store):
    service, starter, access = system(store)
    service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    service.receipts = Mock()
    access.require_write_access.side_effect = MeetError("synthetic_access_revoked", 403)
    with pytest.raises(MeetError, match="^synthetic_access_revoked$"):
        service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    service.receipts.claim.assert_not_called()
    assert starter.start.call_count == 1


def test_pending_crash_claim_cannot_be_taken_over_even_after_restart(store):
    claim = store.claim(*start_fingerprints(PRINCIPAL, "project", "", "key", PAYLOAD))
    assert claim.created
    service, starter, _ = system(SqlDialogStarts(store.engine))
    with pytest.raises(MeetError, match="^meet_dialog_start_pending$"):
        service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    starter.start.assert_not_called()


def test_failure_after_uncertain_dispatch_never_releases_the_key(store):
    service, starter, _ = system(store)
    starter.start.side_effect = RuntimeError("synthetic_uncertain_dispatch")
    with pytest.raises(RuntimeError):
        service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    starter.start.side_effect = None
    with pytest.raises(MeetError, match="^meet_dialog_start_failed$"):
        service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    assert starter.start.call_count == 1


def test_uncertain_completion_write_leaves_pending_fence(store, monkeypatch):
    service, starter, access = system(store)
    monkeypatch.setattr(store, "finish", Mock(side_effect=MeetError("synthetic_storage_unavailable", 503)))
    with pytest.raises(MeetError, match="^synthetic_storage_unavailable$"):
        service.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    restarted = MeetDialogStarts(starter, SqlDialogStarts(store.engine), access)
    with pytest.raises(MeetError, match="^meet_dialog_start_pending$"):
        restarted.start(PRINCIPAL, "project", PAYLOAD, "", "key")
    assert starter.start.call_count == 1


@pytest.mark.parametrize("payload", [[], {"value": float("nan")}, {"value": "x" * 2049}, {"value": "\ud800"}])
def test_payload_fingerprints_are_bounded_closed_json(payload):
    with pytest.raises(MeetError, match="^meet_dialog_payload_invalid$"):
        start_fingerprints(PRINCIPAL, "project", "", "key", payload)
