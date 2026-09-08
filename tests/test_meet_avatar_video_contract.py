"""Synthetic structural clips: authority/transport checks, not decoder evidence."""

from copy import deepcopy

import pytest

from agent.models.meet_avatar_selection import parse_avatar_selection
from ananta_contracts.meet_avatar_image import (
    image_request_signature,
    image_response_signature,
    validate_avatar_projection,
)
from ananta_contracts.meet_avatar_video import (
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    decode_video_response,
    validate_video_projection,
    validate_video_request,
    video_request_signature,
    video_response_signature,
)
from ananta_contracts.persona_video import encode_video
from tests.test_meet_avatar_image_contract import NOW
from tests.test_meet_avatar_image_contract import packet as image_packet
from tests.test_meet_dialog_avatar_selection import PIN
from tests.test_persona_video_inspection import clip


def video_assignment():
    content = clip()
    return {
        "reference": {
            "tenant_id": "synthetic",
            "project_id": "test",
            "artifact_id": "test-clip",
            "revision": 1,
            "kind": "video",
            "sha256": content.video_sha256,
            "classification": "test_only",
        },
        "clip": encode_video(content),
        "origin_kind": "generated",
        "repeat_mode": "loop",
    }


def packet():
    request, _ = image_packet()
    request["schema"] = REQUEST_SCHEMA
    return request, {
        "schema": RESPONSE_SCHEMA,
        "nonce": request["nonce"],
        "binding": deepcopy(request["binding"]),
        "video": video_assignment(),
    }


def projection():
    request, response = packet()
    return {
        "mode": "persona-video-v1",
        "state": "ready",
        "binding": request["binding"],
        "reference": response["video"]["reference"],
        "repeat_mode": "loop",
    }


def test_exact_response_and_opted_in_projection_cannot_be_used_as_image_permission():
    request, response = packet()
    assert validate_video_request(request, NOW) is request
    assert (
        decode_video_response(response, request, response["video"]["reference"], "loop", NOW * 1000)
        == response["video"]
    )
    assert validate_video_projection(projection()) == projection()
    with pytest.raises(ValueError):
        validate_avatar_projection(projection())
    for mode in ("neutral-ai-v1", "persona-image-v1"):
        assert validate_video_projection({"mode": mode, "state": "paused", "binding": None, "reference": None})


@pytest.mark.parametrize("field", list(image_packet()[0]["binding"]))
def test_video_result_cannot_change_any_assignment_or_source_generation(field):
    request, response = packet()
    old = response["binding"][field]
    response["binding"][field] = old + 1 if type(old) is int else "wrong"
    with pytest.raises(ValueError):
        decode_video_response(response, request, response["video"]["reference"], "loop", NOW * 1000)


@pytest.mark.parametrize(
    "patch",
    [
        {"state": "other"},
        {"state": "blocked"},
        {"mode": "persona-image-v1"},
        {"binding": None},
        {"repeat_mode": True},
        {"repeat_mode": []},
        {"repeat_mode": "automatic"},
        {"url": "https://wrong"},
        {"reference": video_assignment()["reference"] | {"tenant_id": "foreign"}},
    ],
)
def test_closed_video_projection_rejects_implicit_policy_and_missing_ready_binding(patch):
    with pytest.raises(ValueError):
        validate_video_projection(projection() | patch)


@pytest.mark.parametrize("change", ["nonce", "schema", "extra", "expired", "reference", "bytes", "repeat"])
def test_stale_or_different_video_never_released(change):
    request, response = packet()
    reference = deepcopy(response["video"]["reference"])
    now = NOW * 1000
    if change in ("nonce", "schema"):
        response[change] = "wrong"
    elif change == "extra":
        response["url"] = "https://wrong"
    elif change == "expired":
        now = response["binding"]["deadline_ms"]
    elif change == "reference":
        response["video"]["reference"]["artifact_id"] = "wrong"
    elif change == "repeat":
        response["video"]["repeat_mode"] = "hold_last"
    else:
        response["video"]["clip"] = None
    with pytest.raises(ValueError):
        decode_video_response(response, request, reference, "loop", now)


def test_video_signatures_are_separate_from_image_and_bound_to_exact_request():
    key, request, response = b"test-only-key", b"request", b"response"
    assert video_request_signature(key, request) != image_request_signature(key, request)
    signed = video_response_signature(key, request, response)
    assert signed != image_response_signature(key, request, response)
    assert signed != video_request_signature(key, response)
    assert signed != video_response_signature(key, request + b" ", response)


def test_metadata_selection_requires_explicit_video_support_and_same_tenant_project():
    selection = {
        "mode": "persona-video-v1",
        "reference": video_assignment()["reference"],
        "profile": PIN,
        "repeat_mode": "loop",
    }
    assert parse_avatar_selection(selection, "synthetic", "test", videos=True) == selection
    for flags in ({}, {"videos": False}, {"videos": 1}):
        with pytest.raises(ValueError):
            parse_avatar_selection(selection, "synthetic", "test", **flags)
    for patch in ({"repeat_mode": "auto"}, {"mode": []}, {"reference": selection["reference"] | {"kind": "image"}}):
        with pytest.raises(ValueError):
            parse_avatar_selection(selection | patch, "synthetic", "test", videos=True)
    with pytest.raises(ValueError):
        parse_avatar_selection(selection, "foreign", "test", videos=True)
