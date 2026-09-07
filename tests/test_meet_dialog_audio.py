"""Synthetic end-to-end authority projections; no real model or release claims."""

from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest

from ananta_contracts.meet_dialog_audio import validate_audio_job, audio_job_current
from agent.services.meet_dialog_audio import MeetDialogAudio
from agent.services.meet_contract import MeetError
from worker.meet_media.dialog_audio import AudioLease, bind_subscription
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_dialog_transport import assignment


def runtime():
    f = fixture(); f.context["audio_mode"] = "transcribe"; f.context["controls"]["audio"]["enabled"] = True
    peer, own = "a" * 16, "b" * 16
    receipt = {"lease": {"sessionId": "ms_" + "a" * 32, "generation": 2, "expiresAt": (f.now + 120) * 1000},
        "peerId": own, "roomId": f.context["room_id"], "membershipEpoch": 3, "receiveRevision": 4,
        "grants": [{"publisherPeerId": peer, "machinePeerId": own, "publicationIds": ["audio"], "chatRead": False, "expiresAt": (f.now + 120) * 1000}],
        "publications": [{"peerId": peer, "publicationId": "audio", "source": "microphone", "publicationEpoch": 7}]}
    meet = Mock(); meet.inspect.return_value = receipt
    children = {}
    f.tasks.get_by_id.side_effect = lambda task_id: f.task if task_id == "task" else children.get(task_id)
    def claim(scope, job, now):
        f.context["audio_job"] = job
        children[job["task_id"]] = SimpleNamespace(task_kind="meet_audio_receive", status="in_progress", tenant_id="tenant", project_id="project",
            parent_task_id=scope.task_id,
            worker_execution_context={"meet_audio": job, "parent_dispatch": scope.lease_id, "runtime_id": scope.runtime_id})
    f.tasks.claim_audio.side_effect = claim; f.tasks.finish_audio.return_value = True
    service = MeetDialogAudio(f.authority, f.tasks, meet, Mock(), Mock(), Mock(), Mock(), clock=lambda: f.now)
    payload = {"task_id": "task", "lease_id": "dispatch", "runtime_id": "runtime", "meet_session_id": receipt["lease"]["sessionId"],
               "publication_id": "audio", "nonce": "a" * 32}
    return f, receipt, service, payload


def test_hub_alone_assigns_source_epoch_child_task_and_bounded_audio_deadline():
    f, receipt, service, payload = runtime()
    job = service.start(payload)["job"]
    assert validate_audio_job(job, f.now) == job and job["deadline"] == f.now + 30
    assert job["publication_epoch"] == 7 and job["receive_revision"] == 4
    assert job["lease_id"] not in {payload["lease_id"], payload["meet_session_id"]}
    assert audio_job_current(job, receipt, f.now)
    service.current(("task", "dispatch", "runtime"), job)
    receipt["publications"][0]["publicationEpoch"] += 1
    with pytest.raises(MeetError): service.current(("task", "dispatch", "runtime"), job)


@pytest.mark.parametrize("change", ["no_policy", "no_source", "own_audio", "wrong_runtime", "expired"])
def test_worker_cannot_assign_itself_unapproved_audio(change):
    f, receipt, service, payload = runtime()
    if change == "no_policy": f.context["audio_mode"] = "off"
    if change == "no_source": receipt["publications"] = []
    if change == "own_audio": payload["publication_id"] = "generated-speech"
    if change == "wrong_runtime": payload["runtime_id"] = "other"
    if change == "expired": receipt["lease"]["expiresAt"] = (f.now + 2) * 1000
    with pytest.raises(MeetError): service.start(payload)
    f.tasks.claim_audio.assert_not_called()


def test_browser_and_hub_leases_are_mapped_not_confused_with_policy_or_publication_epochs():
    f, receipt, service, payload = runtime(); job = service.start(payload)["job"]
    assigned = assignment() | {"capabilities": ["audio.receive", "chat.send"], "audio_mode": "transcribe", "session_id": "hub-session"}
    sub = {"schema": "ananta.meet-audio-subscription.draft1", "subscriptionId": "a" * 32, "format": "pcm_s16le",
        "sampleRate": 16000, "channels": 1, "chunkSamples": 1600, "maxSeconds": 10,
        "binding": {"tenant_id": "tenant", "project_id": "project", "task_id": "task", "lease_id": payload["meet_session_id"],
            "runtime_id": "runtime", "session_id": "hub-session", "generation": 2, "room_id": receipt["roomId"], "membership_epoch": 3,
            "peer_id": job["peer_id"], "own_peer_id": job["own_peer_id"], "publication_id": "audio", "receive_revision": 4,
            "source": "microphone", "deadline_ms": (f.now + 120) * 1000}}
    bound = bind_subscription(sub, assigned, job)
    assert bound.task_id == job["task_id"] and bound.lease_id == job["lease_id"] and bound.publication_epoch == 7
    for patch in ({"lease_id": "dispatch"}, {"receive_revision": 7}, {"publication_epoch": 7}, {"source": "screen_audio"}, {"generation": 1}):
        with pytest.raises(ValueError): bind_subscription(sub | {"binding": sub["binding"] | patch}, assigned, job)
    lease = AudioLease(bound, job); lease.require(bound)
    lease.refresh(receipt, job | {"publication_epoch": 8})
    with pytest.raises(ValueError): lease.require(bound)


def test_transcribe_result_is_ephemeral_and_source_revocation_prevents_acceptance():
    f, receipt, service, payload = runtime(); job = service.start(payload)["job"]
    result = {**payload, "audio_task_id": job["task_id"], "audio_lease_id": job["lease_id"],
              "end_sample": 160000, "language": "de", "text": "synthetic private transcript"}
    answer = service.complete(result)
    assert answer["reply"] is None and "transcript" not in str(answer)
    assert "synthetic private transcript" not in str(f.tasks.finish_audio.call_args)
    service.media_worker.execute.assert_not_called()
    receipt["publications"] = []
    with pytest.raises(MeetError): service.complete(result)
