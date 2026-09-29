"""Meet clip selection with real private storage/policy and Registry test receipts."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.services.meet_contract import MeetError
from agent.services.meet_persona_videos import MeetPersonaVideos
from agent.services.persona_policy_domains import PersonaImagePolicyDomain
from ananta_contracts.meet_persona_video import decode_assignment
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_asset_service import admit
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task


@pytest.mark.parametrize("purpose", ["preview", "publish"])
@pytest.mark.parametrize("repeat_mode", ["loop", "hold_last"])
def test_clip_selection_returns_exact_immutable_bundle_without_grant(request, purpose, repeat_mode):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    port = MeetPersonaVideos(f.service)
    value = port.prepare(f.case.principal, "project", asset.video.artifact_id, purpose, repeat_mode=repeat_mode)
    assert set(value) == {"reference", "clip", "origin_kind", "repeat_mode"}
    assert value["reference"] == asset.video.model_dump(mode="json")
    assert value["origin_kind"] == "generated" and value["repeat_mode"] == repeat_mode
    assert decode_assignment(value, tenant_id="tenant", project_id="project") == f.case.inspected
    port.require_current(f.case.principal, "project", value["reference"], purpose)


def test_local_clip_preview_never_authorizes_publication(request):
    f = request.getfixturevalue("video_assets")
    permission = f.case.permission.model_copy(update={"revision": 2, "purposes": ("inspect", "store", "preview")})
    f.case.policy.install(f.case.principal, permission, expected_revision=1)
    asset = admit(f)
    port = MeetPersonaVideos(f.service)
    value = port.prepare(f.case.principal, "project", asset.video.artifact_id, "preview", repeat_mode="hold_last")
    assert decode_assignment(value, tenant_id="tenant", project_id="project").video == f.case.inspected.video
    with pytest.raises(MeetError):
        port.prepare(f.case.principal, "project", asset.video.artifact_id, "publish", repeat_mode="hold_last")


@pytest.mark.parametrize("boundary", [1, 2])
def test_revocation_while_reading_either_part_never_releases_assignment(request, boundary):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    load = f.storage.store.load_immutable_bytes
    reads = 0

    def revoke(**kwargs):
        nonlocal reads
        content = load(**kwargs)
        reads += 1
        if reads == boundary:
            f.case.policy.revoke_policy(
                f.case.principal, "project", f.case.permission.source.source_id, expected_revision=1
            )
        return content

    f.storage.store.load_immutable_bytes = revoke
    with pytest.raises(MeetError):
        MeetPersonaVideos(f.service).prepare(
            f.case.principal, "project", asset.video.artifact_id, "preview", repeat_mode="loop"
        )
    assert reads == boundary


@pytest.mark.parametrize("mutation", ["image_policy", "revoked", "receipt", "membership"])
def test_current_authority_is_rechecked_after_selection(request, mutation):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    port = MeetPersonaVideos(f.service)
    if mutation == "image_policy":
        f.case.policy.domain = PersonaImagePolicyDomain()
    elif mutation == "revoked":
        f.service.revoke(f.case.principal, "project", asset.video.artifact_id, expected_revision=2)
    elif mutation == "receipt":
        with f.case.base.engine.begin() as connection:
            connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
    else:
        f.case.policy.access.require.side_effect = PermissionError("synthetic denied membership")
    f.service.storage = Mock()
    with pytest.raises(MeetError):
        port.require_current(f.case.principal, "project", asset.video.model_dump(mode="json"), "preview")
    with pytest.raises(MeetError):
        port.prepare(f.case.principal, "project", asset.video.artifact_id, "preview", repeat_mode="loop")
    f.service.storage.read.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("repeat_mode", None),
        ("repeat_mode", True),
        ("origin_kind", "camera"),
        ("clip", None),
        ("unknown", True),
    ],
)
def test_assignment_rejects_unknown_fields_and_implicit_modes(request, field, value):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    assignment = MeetPersonaVideos(f.service).prepare(
        f.case.principal, "project", asset.video.artifact_id, "preview", repeat_mode="loop"
    )
    assignment[field] = value
    with pytest.raises(ValueError):
        decode_assignment(assignment, tenant_id="tenant", project_id="project")


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other"),
        ("project_id", "other"),
        ("kind", "image"),
        ("revision", True),
        ("sha256", "0" * 64),
        ("classification", "production"),
    ],
)
def test_assignment_reference_cannot_change_scope_digest_or_label(request, field, value):
    f = request.getfixturevalue("video_assets")
    asset = admit(f)
    assignment = MeetPersonaVideos(f.service).prepare(
        f.case.principal, "project", asset.video.artifact_id, "preview", repeat_mode="loop"
    )
    assignment = deepcopy(assignment)
    assignment["reference"][field] = value
    with pytest.raises(ValueError):
        decode_assignment(assignment, tenant_id="tenant", project_id="project")
