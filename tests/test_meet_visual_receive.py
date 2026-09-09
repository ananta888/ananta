"""Synthetic closed visual identities and actual local child execution."""

import base64
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_source_job import source_job_current
from ananta_contracts.meet_visual_receive import (
    PROFILE,
    VISUAL_LIMITS,
    require_visual_probe,
    require_visual_subscription,
    validate_visual_job,
    validate_visual_result,
)
from tests.test_worker_image_features import encoded_image
from worker.meet_media.visual_child import analyze
from worker.meet_media.visual_frames import project_visual_frame
from worker.meet_media.visual_pipeline import MeetVisualPipeline

pytestmark = pytest.mark.timeout(45)


def visual_fixture():
    now = int(time.time())
    job = {
        "task_id": "child",
        "lease_id": "child-lease",
        "issued_at": now,
        "deadline": now + 20,
        "meet_session_id": "ms_" + "a" * 32,
        "generation": 2,
        "membership_epoch": 3,
        "receive_revision": 4,
        "peer_id": "b" * 16,
        "own_peer_id": "c" * 16,
        "publication_id": "camera",
        "publication_epoch": 5,
        "source": "camera",
        "control_revision": 1,
        "profile": PROFILE,
    }
    assignment = {
        "tenant_id": "tenant",
        "project_id": "project",
        "task_id": "parent",
        "runtime_id": "runtime",
        "session_id": "hub-session",
        "meeting": {"room_id": "room-" + "a" * 18},
    }
    binding = {k: assignment[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")}
    binding |= {
        k: job[k]
        for k in (
            "generation",
            "membership_epoch",
            "peer_id",
            "own_peer_id",
            "publication_id",
            "publication_epoch",
            "receive_revision",
            "source",
        )
    }
    binding |= {
        "lease_id": job["meet_session_id"],
        "room_id": assignment["meeting"]["room_id"],
        "deadline_ms": (now + 60) * 1000,
    }
    sub = {
        "schema": "ananta.meet-visual-subscription.v1",
        "subscriptionId": "d" * 32,
        "format": "image/jpeg",
        "limits": dict(VISUAL_LIMITS),
        "binding": binding,
    }
    return job, assignment, sub


def visual_frames():
    return [
        {"width": 16, "height": 9, "jpegBase64": base64.b64encode(encoded_image(color=color, format="JPEG")).decode()}
        for color in [(240, 10, 20), (10, 240, 20), (10, 240, 20)]
    ]


def test_visual_job_and_subscription_preserve_distinct_hub_and_meet_leases():
    job, assignment, sub = visual_fixture()
    assert validate_visual_job(job, time.time()) == job
    assert require_visual_subscription(sub, assignment, job) == sub
    assert sub["binding"]["task_id"] != job["task_id"]
    assert sub["binding"]["lease_id"] != job["lease_id"]
    receipt = {
        "lease": {"sessionId": job["meet_session_id"], "generation": 2},
        "membershipEpoch": 3,
        "receiveRevision": 4,
        "peerId": job["own_peer_id"],
        "publications": [
            {"peerId": job["peer_id"], "publicationId": "camera", "source": "camera", "publicationEpoch": 5}
        ],
    }
    assert source_job_current(job, receipt, time.time())
    receipt["publications"][0]["publicationEpoch"] = 6
    assert not source_job_current(job, receipt, time.time())


@pytest.mark.parametrize("field", list(visual_fixture()[2]["binding"]))
def test_subscription_rejects_each_scope_or_epoch_mutation(field):
    job, assignment, sub = visual_fixture()
    value = sub["binding"][field]
    sub["binding"][field] = 0 if type(value) is int else "other"
    with pytest.raises(ValueError):
        require_visual_subscription(sub, assignment, job)


@pytest.mark.parametrize(
    "patch",
    [
        {"profile": "cloud"},
        {"profile": []},
        {"source": "microphone"},
        {"deadline": 1},
        {"generation": True},
        {"publication_epoch": 0},
        {"record": True},
    ],
)
def test_job_never_accepts_provider_paths_or_mixed_source_authority(patch):
    job, _, _ = visual_fixture()
    with pytest.raises(ValueError):
        validate_visual_job(job | patch, time.time())


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "old"},
        {"supported": False},
        {"supported": 1},
        {"profile": "capture"},
        {"limits": {}},
        {"capture": True},
    ],
)
def test_optional_probe_is_closed_and_not_a_capture_fallback(patch):
    value = {
        "schema": "ananta.meet-visual-probe.v1",
        "profile": "jpeg-source-v1",
        "supported": True,
        "limits": dict(VISUAL_LIMITS),
    }
    require_visual_probe(value)
    with pytest.raises(ValueError):
        require_visual_probe(value | patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"sequence": 2},
        {"sequence": True},
        {"subscriptionId": "old"},
        {"width": 641},
        {"height": False},
        {"jpegBase64": "bad"},
        {"jpegBase64": "a" * 131073},
        {"text": "private"},
    ],
)
def test_frame_port_is_exact_and_bounded_before_native_processing(patch):
    pixels = visual_frames()[0]
    frame = {"schema": "ananta.meet-visual-frame.v1", "subscriptionId": "d" * 32, "sequence": 1, **pixels}
    assert project_visual_frame(frame, "d" * 32, 1) == pixels
    with pytest.raises(ValueError):
        project_visual_frame(frame | patch, "d" * 32, 1)


def test_local_statistics_are_bounded_structured_features_not_semantic_text():
    result = analyze({"profile": PROFILE, "frames": visual_frames()})
    assert validate_visual_result(result) == result
    assert result["frames"][0]["average_rgb"][0] > 230
    assert result["frames"][1]["average_rgb"][1] > 230
    assert result["mean_color_change"][0] > 100
    assert result["mean_color_change"][1] == 0
    assert all(word not in str(result) for word in ("jpeg", "text", "sha256", "artifact", "url"))


@pytest.mark.parametrize("mutation", ["profile", "count", "dimensions", "format", "extra"])
def test_native_child_rejects_unapproved_or_mislabeled_input(mutation):
    payload = {"profile": PROFILE, "frames": visual_frames()}
    if mutation == "profile":
        payload["profile"] = "ocr-cloud"
    if mutation == "count":
        payload["frames"].pop()
    if mutation == "dimensions":
        payload["frames"][0]["width"] = 17
    if mutation == "format":
        payload["frames"][0]["jpegBase64"] = base64.b64encode(encoded_image()).decode()
    if mutation == "extra":
        payload["frames"][0]["prompt"] = "ignore policy"
    with pytest.raises(ValueError):
        analyze(payload)


@pytest.mark.parametrize("mutation", ["text", "nan", "infinite", "count", "sequence", "size", "boolean"])
def test_result_never_smuggles_text_media_or_nonfinite_values(mutation):
    result = deepcopy(analyze({"profile": PROFILE, "frames": visual_frames()}))
    if mutation == "text":
        result["text"] = "untrusted prompt"
    if mutation == "nan":
        result["frames"][0]["average_rgb"][0] = float("nan")
    if mutation == "infinite":
        result["mean_color_change"][0] = float("inf")
    if mutation == "count":
        result["frames"].pop()
    if mutation == "sequence":
        result["frames"][1]["sequence"] = 1
    if mutation == "size":
        result["frames"][0]["width"] = 641
    if mutation == "boolean":
        result["frames"][0]["average_rgb"][0] = True
    with pytest.raises(ValueError):
        validate_visual_result(result)


def test_actual_bounded_child_runs_without_model_network_or_artifact_storage():
    lease = Mock()
    pipeline = MeetVisualPipeline("exact-binding", lease, deadline_monotonic=time.monotonic() + 10)
    result = pipeline.analyze(visual_frames())
    assert result["profile"] == PROFILE and len(result["frames"]) == 3
    lease.require.assert_called_with("exact-binding")
    pipeline.cancel()
    with pytest.raises(ValueError, match="cancelled"):
        pipeline.analyze(visual_frames())


def test_declared_dimensions_cannot_hide_oversize_decoded_input_behind_thumbnail():
    frames = visual_frames()
    frames[0] = {
        "width": 640,
        "height": 90,
        "jpegBase64": base64.b64encode(encoded_image((1280, 180), format="JPEG")).decode(),
    }
    with pytest.raises(ValueError, match="dimensions_exceeded"):
        analyze({"profile": PROFILE, "frames": frames})


def test_revoked_pipeline_does_not_start_a_child():
    lease, runner = Mock(), Mock()
    lease.require.side_effect = ValueError("revoked")
    pipeline = MeetVisualPipeline("exact-binding", lease, deadline_monotonic=time.monotonic() + 10, runner=runner)
    with pytest.raises(ValueError, match="revoked"):
        pipeline.analyze(visual_frames())
    runner.run.assert_not_called()
