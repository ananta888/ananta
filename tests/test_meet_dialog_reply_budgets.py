"""Dialog composition preserves existing SQL media admission and pinned speech."""

from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import select

from agent.bootstrap.meet import configure_meet_dialog
from agent.repositories.meet_capacity import SqlMeetCapacity, slots
from agent.services.meet_capacity_admission import MeetCapacityAdmission
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_replies import MeetDialogReplies
from agent.services.meet_dialog_service import MeetDialogService
from ananta_contracts.meet_speech import speech_profile
from tests.test_meet_chat_admission import NOW
from tests.test_meet_chat_replies import PRINCIPAL
from tests.test_meet_chat_replies import runtime as runtime
from tests.test_meet_speech_binding import speech_result


def test_dialog_reply_uses_sql_capacity_and_exact_pinned_voice(request):
    case = request.getfixturevalue("runtime")
    repository = SqlMeetCapacity(case.engine, "test-dialog-media")
    repository.initialize()
    capacity = MeetCapacityAdmission(repository, case.tasks, clock=lambda: NOW / 1000)
    profile = speech_profile(max_seconds=5)

    def execute(turn):
        with case.engine.connect() as connection:
            row = connection.execute(select(slots).where(slots.c.lease_type == "meet_media")).mappings().one()
            assert row["status"] == "active" and row["parent_task_id"] == turn["task_id"]
        assert turn["speech_profile"] == profile
        return speech_result(profile=profile) | {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "usage": {"input_tokens": 50, "output_tokens": 8},
        }

    case.worker.execute.side_effect = execute
    replies = MeetDialogReplies(
        case.binding,
        case.worker,
        case.tasks,
        case.delivery,
        capacity=capacity,
        speech_profile=profile,
        clock=lambda: NOW / 1000,
    )
    response = replies.execute(case.authority, PRINCIPAL, case.admission)
    assert response["published"] is False and response["media"]["speech"]["profile"] == profile
    assert case.tasks.require_current.call_count >= 3
    with case.engine.connect() as connection:
        assert (
            connection.execute(select(slots.c.status).where(slots.c.lease_type == "meet_media")).scalar_one()
            == "released"
        )


def test_capacity_denial_does_not_dispatch_or_retry(request):
    case = request.getfixturevalue("runtime")
    capacity = Mock()
    capacity.run.side_effect = MeetError("meet_capacity_wait_expired", 429)
    replies = MeetDialogReplies(
        case.binding,
        case.worker,
        case.tasks,
        case.delivery,
        capacity=capacity,
        speech_profile=speech_profile(max_seconds=5),
        clock=lambda: NOW / 1000,
    )
    with pytest.raises(MeetError, match="capacity_wait_expired"):
        replies.execute(case.authority, PRINCIPAL, case.admission)
    case.worker.execute.assert_not_called()
    assert case.tasks.finish.call_args.args[1] == "failed"
    with pytest.raises(MeetError, match="already_dispatched"):
        replies.execute(case.authority, PRINCIPAL, case.admission)
    capacity.run.assert_called_once()


def test_chat_and_audio_use_the_same_injected_reply_composition():
    replies = Mock(speech_profile=None)
    service = MeetDialogService(Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), replies=replies)
    assert service.replies is replies and service.audio_coordinator.replies is replies


@pytest.mark.parametrize("capacity,voice", [(None, None), (Mock(), None), (None, speech_profile())])
def test_enabled_production_dialog_cannot_omit_media_budgets(monkeypatch, capacity, voice):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    monkeypatch.setenv("ANANTA_MEET_DIALOG_ENABLED", "1")
    with pytest.raises(ValueError, match="media_budgets_required"):
        configure_meet_dialog(app, Mock(), Mock(), capacity=capacity, speech_profile=voice)


@pytest.mark.parametrize("voice_assets,profiles", [(False, False), (True, False), (False, True), (True, True)])
def test_bootstrap_passes_exact_capacity_and_voice_to_both_dialog_paths(monkeypatch, voice_assets, profiles):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["meet_binding_service"] = Mock()
    if voice_assets:
        app.extensions["persona_voice_assets"] = Mock()
    if profiles:
        app.extensions["persona_profiles"] = Mock()
    monkeypatch.setenv("ANANTA_MEET_DIALOG_ENABLED", "1")
    monkeypatch.setenv("ANANTA_MEET_DIALOG_POLICIES", "[]")
    monkeypatch.setattr("agent.repositories.meet_chat_reservations.SqlChatReservations", Mock())
    monkeypatch.setattr("agent.repositories.meet_chat_dispatches.SqlChatDispatches", Mock())
    capacity, voice = Mock(), speech_profile(max_seconds=7)
    configure_meet_dialog(app, Mock(), Mock(), capacity=capacity, speech_profile=voice)
    from agent.repositories.meet_dialog_deadlines import SqlDialogDeadlines
    from agent.services.meet_dialog_deadlines import MeetDialogDeadlines

    assert isinstance(app.extensions["meet_dialog_deadlines"], MeetDialogDeadlines)
    assert isinstance(app.extensions["meet_dialog_deadlines"].store, SqlDialogDeadlines)
    assert "meet_dialog_deadline_reconciler" not in app.extensions
    starts = app.extensions["meet_dialog_starts"]
    assert starts.starter is app.extensions["meet_dialog_service"]
    assert starts.access is app.extensions["meet_binding_service"]
    replies = app.extensions["meet_dialog_service"].replies
    assert replies.capacity is capacity and replies.speech_profile == voice
    assert app.extensions["meet_dialog_service"].audio_coordinator.replies is replies
    bridge = app.extensions["meet_dialog_service"].voices
    assert bridge.configured_profile == voice
    assert (bridge.profiles is not None) == (voice_assets and profiles)
    if bridge.profiles is not None:
        assert bridge.profiles.voices.assets is app.extensions["persona_voice_assets"]
