"""Repeated authenticated HTTP start, real private Hub/Worker/Meet transport."""

import os
from unittest.mock import Mock

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.repositories.meet_dialog_starts import SqlDialogStarts
from agent.services.meet_dialog_starts import MeetDialogStarts


@pytest.mark.timeout(240)
@pytest.mark.skipif(os.environ.get("MEET_DIALOG_START_GATE") != "1", reason="opt-in private start replay transport")
def test_repeated_http_start_has_one_live_worker_and_one_hub_task(app, tmp_path, monkeypatch, record_property):
    from agent.database import engine
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "HTTP replay regression has a fixed short budget"
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    observations = []

    def start_twice(service, principal, project, payload, parent=""):
        import agent.auth as auth

        store = SqlDialogStarts(engine)
        store.initialize()
        starts = MeetDialogStarts(service, store, service.authority.binding)
        monkeypatch.setitem(app.extensions, "meet_dialog_starts", starts)
        dispatch = Mock(wraps=service.worker.start_dialog)
        monkeypatch.setattr(service.worker, "start_dialog", dispatch)
        path = f"/api/meet/v1/projects/{project}/dialogs"
        headers = {"Authorization": "Bearer synthetic-start-user", "Idempotency-Key": "synthetic-start-command"}
        # Authentication and the existing project policy are synthetic fixtures.
        # Task lifecycle, grant issuance, SQL and signed Worker requests stay real.
        with monkeypatch.context() as authentication:
            authentication.setattr(
                auth,
                "_validate_user_jwt",
                lambda token: (
                    {
                        "sub": principal.subject_id,
                        "tenant_id": principal.tenant_id,
                        "project_id": project,
                        "role": "user",
                    }
                    if token == "synthetic-start-user"
                    else None
                ),
            )
            authentication.setattr(auth, "_user_token_allows_current_request", lambda _: True)
            client = app.test_client()
            first = client.post(path, json=payload, headers=headers)
            assert first.status_code == 202, first.json
            # Reconstruct the coordinator/repository between HTTP requests.
            app.extensions["meet_dialog_starts"] = MeetDialogStarts(
                service, SqlDialogStarts(engine), service.authority.binding
            )
            replay = client.post(path, json=payload, headers=headers)
        assert replay.status_code == 202 and replay.json == first.json
        assert first.headers["Idempotency-Replayed"] == "false"
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert dispatch.call_count == 1
        with Session(engine) as session:
            tasks = session.exec(select(TaskDB).where(TaskDB.task_kind == "meet_dialog_session")).all()
            assert len(tasks) == 1 and tasks[0].id == first.json["task_id"]
        observations.append({"http_starts": 2, "worker_dispatches": 1, "hub_tasks": 1, "receipt_replayed": True})
        return first.json

    run_gate(app, tmp_path, monkeypatch, False, False, None, False, False, record_property, start_scenario=start_twice)
    assert len(observations) == 1
    record_property(
        "synthetic_dialog_start_replay", observations[0] | {"synthetic": True, "production_release_evidence": False}
    )
