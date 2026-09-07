"""Headless user video surface, with explicit authentication/service test doubles."""

import base64
from unittest.mock import Mock

import pytest

from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_video_tasks import video_task as video_task

BASE = "/api/persona-media/v1/projects/project/videos"


@pytest.fixture
def video_api(request):
    http, app = request.getfixturevalue("client")
    for name in ("persona_video_assets", "persona_video_policy", "persona_video_erasure"):
        app.extensions[name] = Mock()
    return http, app


def body():
    # Syntactic API test only: these strings are never submitted as Registry evidence.
    return {
        "content": base64.b64encode(b"synthetic-video-input").decode(),
        "media_type": "video/mp4",
        "origin_binding": "SRC_structural-route-only",
        "license_binding": "SRC_structural-license-only",
        "consent_binding": None,
    }


@pytest.mark.parametrize("token", [None, "worker-token", "service-token", "invalid extra"])
def test_video_upload_never_accepts_machine_or_missing_credentials(video_api, token):
    http, app = video_api
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert http.post(BASE, json=body(), headers=headers).status_code == 401
    app.extensions["persona_video_assets"].admit_video.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"content": "not-base64"},
        {"content": []},
        {"content": ""},
        {"media_type": "image/png"},
        {"media_type": "video/webm"},
        {"origin_binding": {}},
        {"origin_binding": "https://untrusted/source"},
        {"license_binding": None},
        {"consent_binding": "unregistered-name"},
    ],
)
def test_video_upload_contract_rejects_invalid_inputs_before_service(video_api, change):
    http, app = video_api
    assert http.post(BASE, json=body() | change, headers=HEADERS).status_code == 409
    app.extensions["persona_video_assets"].admit_video.assert_not_called()


def test_video_upload_passes_only_authenticated_scope(video_api):
    http, app = video_api
    service = app.extensions["persona_video_assets"]
    service.admit_video.return_value.model_dump.return_value = {"classification": "test_only"}
    response = http.post(BASE, json=body(), headers=HEADERS)
    assert response.status_code == 201 and response.json["state"] == "active" and response.json["revision"] == 2
    args, kwargs = service.admit_video.call_args
    assert (args[0].tenant_id, args[0].subject_id, args[1]) == ("tenant", "actor", "project")
    assert kwargs["content"] == b"synthetic-video-input" and kwargs["media_type"] == "video/mp4"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_preview_is_png_and_has_no_clip_publication_or_download_escape(video_api):
    http, app = video_api
    service = app.extensions["persona_video_assets"]
    service.read_video.return_value = b"synthetic-png-preview"
    response = http.get(BASE + "/video/preview", headers=HEADERS)
    assert response.status_code == 200 and response.mimetype == "image/png"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert service.read_video.call_args.kwargs == {"purpose": "preview"}
    for suffix in ("publish", "download", "content"):
        assert http.get(BASE + "/video/" + suffix, headers=HEADERS).status_code == 404
    assert http.get(BASE + "/video/preview?purpose=publish", headers=HEADERS).status_code == 400
    service.read_video.assert_called_once()


@pytest.mark.parametrize("revision", [True, 0, "2", 2**53])
def test_revision_ambiguity_never_revokes_or_purges(video_api, revision):
    http, app = video_api
    assert http.delete(BASE + "/video", json={"expected_revision": revision}, headers=HEADERS).status_code == 409
    assert http.post(BASE + "/video/purge", json={"expected_revision": revision}, headers=HEADERS).status_code == 409
    app.extensions["persona_video_assets"].revoke.assert_not_called()
    app.extensions["persona_video_erasure"].purge.assert_not_called()


def test_revocation_and_purge_are_separate_authenticated_actions(video_api):
    http, app = video_api
    app.extensions["persona_video_assets"].revoke.return_value = 3
    erasure = app.extensions["persona_video_erasure"]
    erasure.purge.return_value = 5
    erasure.status.return_value = {"revision": 4, "state": "purging"}
    assert http.delete(BASE + "/video", json={"expected_revision": 2}, headers=HEADERS).json == {
        "revision": 3,
        "state": "revoked",
    }
    assert http.get(BASE + "/video/purge", headers=HEADERS).json["state"] == "purging"
    assert http.post(BASE + "/video/purge", json={"expected_revision": 4}, headers=HEADERS).json == {
        "revision": 5,
        "state": "purged",
        "secure_device_erasure": False,
    }
    assert erasure.purge.call_args.kwargs == {"expected_revision": 4}
    assert http.post(BASE + "/video/purge", json={"expected_revision": 4}).status_code == 401
    erasure.purge.assert_called_once()


def test_video_api_does_not_fall_back_to_enabled_images(video_api):
    http, app = video_api
    app.extensions.pop("persona_video_assets")
    assert http.post(BASE, json=body(), headers=HEADERS).status_code == 409
    app.extensions["persona_assets"].admit_image.assert_not_called()
    app.config["ROLE"] = "worker"
    assert http.post(BASE, json=body(), headers=HEADERS).status_code == 403


@pytest.mark.parametrize("kind", ["images", "videos"])
def test_shared_json_boundary_rejects_duplicate_keys(video_api, kind):
    http, app = video_api
    response = http.delete(
        f"/api/persona-media/v1/projects/project/{kind}/asset",
        headers=HEADERS,
        data='{"expected_revision":1,"expected_revision":2}',
        content_type="application/json",
    )
    assert response.status_code == 409
    app.extensions["persona_assets"].revoke.assert_not_called()
    app.extensions["persona_video_assets"].revoke.assert_not_called()


def test_video_policy_requires_closed_video_kind_and_matching_project(video_api, request):
    http, app = video_api
    case = request.getfixturevalue("video_task")
    policy = case.permission.model_dump(mode="json")
    path = "/api/persona-media/v1/projects/project/video-policy"
    assert http.put(path, json={"policy": policy, "expected_revision": 0}, headers=HEADERS).json == {"revision": 1}
    service = app.extensions["persona_video_policy"]
    assert service.install.call_args.args[1].media_kind == "video"
    for changed in (policy | {"media_kind": "image"}, {k: v for k, v in policy.items() if k != "media_kind"}):
        assert http.put(path, json={"policy": changed, "expected_revision": 0}, headers=HEADERS).status_code == 409
    assert (
        http.put(
            path.replace("/project/", "/other/"), json={"policy": policy, "expected_revision": 0}, headers=HEADERS
        ).status_code
        == 403
    )
    service.install.assert_called_once()
    service.revoke_policy.return_value = 2
    assert http.delete(
        path + "/" + policy["source"]["source_id"], json={"expected_revision": 1}, headers=HEADERS
    ).json == {"revision": 2, "state": "revoked"}
