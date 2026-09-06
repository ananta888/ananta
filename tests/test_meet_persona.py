"""Optional static-image turns; explicit synthetic policy fixtures, no human devices."""

import copy
import json
import uuid
from unittest.mock import Mock

import pytest

from agent.services.meet_media_result import validate_response_budget, validate_result
from agent.services.meet_persona_images import MeetPersonaImages
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from ananta_contracts.meet_persona_image import decode_assignment
from tests.test_meet_media import result, turn
from tests.test_persona_assets import application
from tests.test_persona_assets import setup as setup
from tests.test_persona_inspection_tasks import runtime as runtime
from worker.meet_media.persona_video import static_frame
from worker.meet_media.video_frames import encode_frames

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def picture(request):
    fixture = request.getfixturevalue("setup")
    assets, principal, kwargs = application(fixture)
    asset = assets.admit_image(principal, "project", **kwargs)
    port = MeetPersonaImages(assets)
    assignment = port.prepare(principal, "project", asset.image.artifact_id, "preview")
    return assets, principal, asset, port, assignment


def turn_service(picture):
    _, _, _, port, _ = picture
    worker, tasks = Mock(), Mock()
    tasks.finish.return_value = True
    worker.execute.side_effect = lambda request: {
        "task_id": request["task_id"],
        "lease_id": request["lease_id"],
        "persona_image": request["persona_image"]["reference"],
    }
    return MeetTurnService(Mock(), worker, tasks, [("tenant", "project")], persona_images=port), worker, tasks


def test_optional_image_selection_remains_a_preview_without_publication(picture):
    assets, principal, asset, _, _ = picture
    service, worker, tasks = turn_service(picture)
    response = service.execute(principal, "project", {"text": "Hello", "persona_image_id": asset.image.artifact_id})
    assert response["persona_image"] == asset.image.model_dump(mode="json")
    dispatched = worker.execute.call_args.args[0]
    assert "meeting" not in dispatched
    assets.policy.require_asset.assert_called_with(principal, asset, "preview")
    assert tasks.finish.call_args.args[1] == "completed"


def test_image_revoked_during_generation_cannot_be_delivered(picture):
    assets, principal, asset, _, _ = picture
    service, worker, tasks = turn_service(picture)
    original = worker.execute.side_effect

    def revoke(request):
        response = original(request)
        assets.revoke(principal, "project", asset.image.artifact_id, expected_revision=2)
        return response

    worker.execute.side_effect = revoke
    with pytest.raises(ValueError, match="denied_or_unavailable"):
        service.execute(principal, "project", {"text": "Hello", "persona_image_id": asset.image.artifact_id})
    assert tasks.finish.call_args.args[1] == "failed"


def test_real_publication_lease_rechecks_the_exact_selected_asset(picture, request):
    request.getfixturevalue("runtime")  # Real project scope and active Hub app context.
    assets, principal, asset, port, assignment = picture
    tasks = HubMediaTasks()
    service = MeetTurnService(Mock(), Mock(), tasks, [("tenant", "project")], persona_images=port)
    envelope = turn() | {"task_id": str(uuid.uuid4()), "lease_id": str(uuid.uuid4()), "persona_image": assignment}
    tasks.start(envelope, principal.subject_id)
    assert service.lease_allowed(envelope["task_id"], envelope["lease_id"])
    from agent.services.repository_registry import get_repository_registry

    stored = get_repository_registry().task_repo.get_by_id(envelope["task_id"])
    assert assignment["png"] not in json.dumps(stored.worker_execution_context)
    assert stored.worker_execution_context["meet_media"]["persona_image"] == assignment["reference"]
    assets.revoke(principal, "project", asset.image.artifact_id, expected_revision=2)
    assert service.lease_allowed(envelope["task_id"], envelope["lease_id"]) is False
    tasks.finish(envelope, "failed")


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "foreign"),
        ("sha256", "f" * 64),
        ("revision", True),
        ("kind", "video"),
        ("classification", "human-camera"),
    ],
)
def test_static_image_assignment_rejects_scope_hash_and_classification_changes(picture, field, value):
    assignment = copy.deepcopy(picture[4])
    assignment["reference"][field] = value
    with pytest.raises(ValueError):
        decode_assignment(assignment, tenant_id="tenant", project_id="project")


def test_generated_media_response_is_bound_to_selected_image(picture):
    assignment = picture[4]
    response = result() | {"persona_image": assignment["reference"]}
    response["engines"] = response["engines"] | {"video": "persona-image-h264_nvenc"}
    validate_result(response)
    validate_response_budget(turn() | {"persona_image": assignment}, response)
    with pytest.raises(ValueError):
        validate_response_budget(turn(), response)


def test_static_frame_is_fixed_size_and_visibly_labelled_without_capture(picture):
    frame = static_frame(picture[4], tenant_id="tenant", project_id="project")
    assert frame.mode == "RGB" and frame.size == (256, 256)
    assert frame.getpixel((128, 104)) == (255, 0, 0)
    # Antialiased glyphs need not contain an exactly-white pixel.
    assert sum(value > 180 for value in frame.crop((0, 225, 256, 256)).convert("L").tobytes()) > 30


def test_frame_encoder_checks_authority_and_never_encodes_after_revocation(picture, tmp_path, monkeypatch):
    frame = static_frame(picture[4], tenant_id="tenant", project_id="project")
    encoder = Mock()
    monkeypatch.setattr("worker.meet_media.video_frames.subprocess.run", encoder)
    authority = Mock(side_effect=[None, PermissionError("revoked")])
    with pytest.raises(PermissionError, match="revoked"):
        encode_frames(tmp_path / "audio.wav", 1, tmp_path, frame_source=lambda _: frame, require_current=authority)
    encoder.assert_not_called()


def test_frame_encoder_has_fixed_fps_codec_and_frame_budget(picture, tmp_path, monkeypatch):
    frame = static_frame(picture[4], tenant_id="tenant", project_id="project")
    encoder = Mock()
    (tmp_path / "avatar.mp4").write_bytes(b"0000ftyp-synthetic-encoder-output")
    verifier = Mock()
    monkeypatch.setattr("worker.meet_media.video_frames.verify_encoded_media", verifier)
    monkeypatch.setattr("worker.meet_media.video_frames.subprocess.run", encoder)
    encode_frames(tmp_path / "audio.wav", 0.2, tmp_path, frame_source=lambda _: frame, require_current=Mock())
    assert (tmp_path / "frames.rgb").stat().st_size == 3 * 256 * 256 * 3
    args = encoder.call_args.args[0]
    assert args[args.index("-c:v") + 1] == "h264_nvenc"
    assert args[args.index("-framerate") + 1] == "12"
    assert encoder.call_args.kwargs["timeout"] == 30
    assert verifier.call_args.kwargs["expected_samples"] == 4410
