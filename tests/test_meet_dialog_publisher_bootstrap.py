"""Optional multi-destination composition leaves media execution and default mode intact."""

import json
from unittest.mock import Mock

import pytest
from flask import Flask

from agent.bootstrap.meet import configure_meet_dialog
from agent.services.meet_dialog_worker_router import MeetDialogWorkerRouter
from agent.services.meet_media_transport import HttpMediaWorker
from ananta_contracts.meet_speech import speech_profile

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("preauthorization", [False, True])
@pytest.mark.parametrize("speaker_floor", [False, True])
@pytest.mark.parametrize("reconnect", [False, True])
def test_preflight_task_admission_and_dispatch_share_configured_destinations(
    monkeypatch, enabled, preauthorization, speaker_floor, reconnect
):
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["meet_binding_service"] = Mock()
    monkeypatch.setenv("ANANTA_MEET_DIALOG_ENABLED", "1")
    monkeypatch.setenv("ANANTA_MEET_SPEAKER_FLOOR", "1" if speaker_floor else "0")
    monkeypatch.setenv("ANANTA_MEET_DIALOG_RECONNECT", "1" if reconnect else "0")
    monkeypatch.setenv("ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED", "1" if preauthorization else "0")
    monkeypatch.setenv("ANANTA_MEET_DIALOG_POLICIES", "[]")
    monkeypatch.setenv("ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED", "1")
    monkeypatch.delenv("ANANTA_MEET_DIALOG_WORKER_URLS", raising=False)
    if enabled:
        monkeypatch.setenv("ANANTA_MEET_DIALOG_WORKER_URLS", json.dumps(["http://second:8091/v1/turns"]))
    monkeypatch.setattr("agent.repositories.meet_chat_reservations.SqlChatReservations", Mock())
    monkeypatch.setattr("agent.repositories.meet_chat_dispatches.SqlChatDispatches", Mock())
    worker = HttpMediaWorker("http://first:8091/v1/turns", b"synthetic" * 4)
    configure_meet_dialog(app, worker, Mock(), capacity=Mock(), speech_profile=speech_profile(max_seconds=7))
    service = app.extensions["meet_dialog_service"]
    assert (service.speaker_floor is not None) == speaker_floor
    assert service.spoken_replies.speaker_floor is service.speaker_floor
    assert (service.recovery is not None) == reconnect
    assert service.meet.recovery is service.recovery
    if reconnect:
        assert service.recovery.authority is service.authority
        assert service.recovery.meet is service.meet
        assert service.recovery.phases is service.phases
        assert service.recovery.speaker_floor is service.speaker_floor
    assert service.authority.preauthorization is app.extensions.get("meet_dialog_preauthorization")
    assert (service.authority.preauthorization is not None) == preauthorization
    from agent.services.meet_dialog_diagnostics import MeetDialogDiagnostics

    diagnostics = app.extensions["meet_dialog_diagnostics"]
    assert isinstance(diagnostics, MeetDialogDiagnostics)
    assert diagnostics.access is service.authority.binding
    preflight = app.extensions["meet_organization_principal_preflight"]
    assert service.media_worker is worker and service.replies.worker is worker
    assert service.tasks.publishers is preflight.publishers
    if enabled:
        assert isinstance(service.worker, MeetDialogWorkerRouter)
        assert (
            tuple(service.worker.workers)
            == service.tasks.publishers.origins
            == ("http://first:8091", "http://second:8091")
        )
        assert service.worker.authority is service.authority and service.worker.tasks is service.tasks
        assert service.tasks.role_assignments.rows is service.tasks.publishers.rows
    else:
        assert service.worker is worker and service.tasks.publishers is None
