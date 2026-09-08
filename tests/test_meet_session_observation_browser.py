"""Real private Meet observation across source stop/resume and Hub cancellation."""

import os
import threading

import pytest

from agent.services.meet_authorization_client import MeetAuthorizationClient
from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.meet_publication_observation_wait import wait_for_publications


class PublicationObservationScenario:
    parent_id = "meet-test-parent"

    def __init__(self, monkeypatch):
        self.ids = None
        self.result = None
        self.lock = threading.Lock()
        original = MeetAuthorizationClient.inspect

        def remember(client, *ids):
            value = original(client, *ids)
            with self.lock:
                self.ids = ids
            return value

        monkeypatch.setattr(MeetAuthorizationClient, "inspect", remember)

    def prepare(self, engine, *, publisher):
        seed_parent(engine, tenant="synthetic", project="synthetic", create_project=False, publisher=publisher)

    def revoke(self, _engine, service, started, completed):
        with self.lock:
            ids = self.ids
        assert ids is not None and ids[0] == started["task_id"]
        principal = HubSourcePrincipal("owner", "synthetic", "synthetic", frozenset({"user"}))
        initial = service.meet.observe(*ids)
        assert [source["source"] for source in initial["publications"]] == ["screen"]
        assert initial["publicationRevision"] >= 1
        rows = [initial]
        convergence_ms = []
        for enabled in (False, True):
            status = service.inspect(principal, "synthetic", started["task_id"])
            controls = status["controls"]
            service.control(
                principal,
                "synthetic",
                started["task_id"],
                {
                    "expected_revision": controls["revision"],
                    "chat": controls["chat"]["enabled"],
                    "audio": controls["audio"]["enabled"],
                    "screen": enabled,
                },
            )
            observed, elapsed = wait_for_publications(lambda: service.meet.observe(*ids), enabled)
            convergence_ms.append(elapsed)
            assert observed["publicationRevision"] > rows[-1]["publicationRevision"]
            assert observed["peerId"] == initial["peerId"]
            assert observed["lease"]["sessionId"] == initial["lease"]["sessionId"]
            if enabled:
                assert [source["source"] for source in observed["publications"]] == ["screen"]
                assert observed["publications"][0]["publicationEpoch"] > initial["publications"][0]["publicationEpoch"]
            rows.append(observed)
        result = service.inspect(principal, "synthetic", started["task_id"], stop=True)
        assert result["status"] == "cancelled"
        assert completed.wait(8), "bounded Worker stop missing"
        with pytest.raises(MeetError, match="task_inactive"):
            service.meet.observe(*ids)
        self.result = {
            "synthetic": True,
            "production_release_evidence": False,
            "registered_source_counts": [len(row["publications"]) for row in rows],
            "publication_revisions": [row["publicationRevision"] for row in rows],
            "same_membership": True,
            "source_convergence_ms": convergence_ms,
            "cancelled_observation_denied": True,
            "decoded_resume_media_claim": False,
        }


@pytest.mark.timeout(240)
@pytest.mark.skipif(
    os.environ.get("MEET_SESSION_OBSERVATION_GATE") != "1", reason="opt-in private observation transport"
)
def test_actual_membership_observes_only_own_screen_and_scoped_source_changes(
    app, tmp_path, monkeypatch, record_property
):
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    scenario = PublicationObservationScenario(monkeypatch)
    run_gate(app, tmp_path, monkeypatch, False, False, None, False, False, record_property, lifecycle_scenario=scenario)
    assert scenario.result is not None
    record_property("synthetic_session_publication_observation", scenario.result)
