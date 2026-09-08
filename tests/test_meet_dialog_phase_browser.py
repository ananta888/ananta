"""Actual private Meet/Worker with SQL-persisted Hub phase transitions, no human gate."""

import os

import pytest

from agent.repositories.meet_dialog_phases import TaskDialogPhases
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_phases import MeetDialogPhases
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.meet_publication_observation_wait import wait_for_publications


class PhaseScenario:
    parent_id = "meet-test-parent"

    def __init__(self):
        self.result = None

    def prepare(self, engine, *, publisher):
        seed_parent(engine, tenant="synthetic", project="synthetic", create_project=False, publisher=publisher)

    def revoke(self, _engine, service, started, completed):
        principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
        task_id = started["task_id"]
        phases = service.phases
        assert phases.inspect(principal, "synthetic", task_id)["phase"] == "joined"
        rows = [phases.inspect(principal, "synthetic", task_id, refresh=True)]
        assert rows[0]["phase"] == "publishing" and rows[0]["registered_sources"] == ["screen"]
        times = []
        for enabled in (False, True):
            controls = service.inspect(principal, "synthetic", task_id)["controls"]
            service.control(
                principal,
                "synthetic",
                task_id,
                {
                    "expected_revision": controls["revision"],
                    "chat": controls["chat"]["enabled"],
                    "audio": controls["audio"]["enabled"],
                    "screen": enabled,
                },
            )

            def observe():
                value = phases.inspect(principal, "synthetic", task_id, refresh=True)
                return {
                    "publications": value["registered_sources"],
                    "publicationRevision": value["publication_revision"],
                    "phase_result": value,
                }

            observed, elapsed = wait_for_publications(observe, enabled)
            row = observed["phase_result"]
            assert row["phase"] == ("publishing" if enabled else "joined")
            assert (
                row["revision"] > rows[-1]["revision"]
                and row["publication_revision"] > rows[-1]["publication_revision"]
            )
            rows.append(row)
            times.append(elapsed)
        assert service.inspect(principal, "synthetic", task_id, stop=True)["status"] == "cancelled"
        assert completed.wait(8), "bounded Worker stop missing"
        # Reconstruct both ports: terminal state comes from the persisted Task,
        # not the former coordinator or the process-local browser observer.
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        restarted = MeetDialogPhases(
            TaskDialogPhases(service.tasks, task_status_cas=compare_and_set_local_task_status),
            service.authority,
            service.meet,
        )
        terminal = restarted.inspect(principal, "synthetic", task_id)
        assert terminal["phase"] == "cancelled" and not terminal["observation_fresh"]
        with pytest.raises(MeetError, match="inactive"):
            restarted.inspect(principal, "synthetic", task_id, refresh=True)
        self.result = {
            "synthetic": True,
            "production_release_evidence": False,
            "phases": [row["phase"] for row in rows] + [terminal["phase"]],
            "phase_revisions": [row["revision"] for row in rows] + [terminal["revision"]],
            "source_convergence_ms": times,
            "reconstructed_terminal": True,
            "decoded_resume_media_claim": False,
        }


@pytest.mark.timeout(240)
@pytest.mark.skipif(os.environ.get("MEET_DIALOG_PHASE_GATE") != "1", reason="opt-in private persistent phases")
def test_real_dialog_has_persistent_phase_revisions_across_source_changes_and_stop(
    app, tmp_path, monkeypatch, record_property
):
    from agent.services.task_runtime_service import compare_and_set_local_task_status
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    scenario = PhaseScenario()

    def start(service, principal, project, payload, parent=""):
        service.phases = MeetDialogPhases(
            TaskDialogPhases(service.tasks, task_status_cas=compare_and_set_local_task_status),
            service.authority,
            service.meet,
        )
        return service.start(principal, project, payload, parent)

    run_gate(
        app,
        tmp_path,
        monkeypatch,
        False,
        False,
        None,
        False,
        False,
        record_property,
        lifecycle_scenario=scenario,
        start_scenario=start,
    )
    assert scenario.result is not None
    record_property("synthetic_persistent_dialog_phases", scenario.result)
