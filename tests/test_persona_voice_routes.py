"""Headless voice API with real Hub receipts/storage and an explicit JWT fixture."""

import base64
from types import SimpleNamespace

import pytest

from agent.models.persona_voice_assets import PersonaVoiceAsset
from agent.repositories.persona_voice_cursors import create_voice_cursors
from agent.repositories.persona_voice_retention import create_voice_retention_store
from agent.services.persona_asset_query import PersonaAssetQuery
from agent.services.persona_profile_voices import PersonaProfileVoices
from agent.services.persona_retention_service import PersonaRetentionService
from agent.services.persona_voice_erasure import create_voice_erasure_service
from ananta_contracts.persona_voice import MEDIA_TYPE
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_tasks import voice_task as voice_task

BASE = "/api/persona-media/v1/projects/project/voices"


@pytest.fixture
def voice_api(request):
    http, app = request.getfixturevalue("client")
    fixture = request.getfixturevalue("voice_assets")
    policy = fixture.case.policy
    references = PersonaProfileVoices(fixture.service)
    cursors, retention = (
        create_voice_cursors(fixture.catalog.engine),
        create_voice_retention_store(fixture.catalog.engine),
    )
    cursors.initialize()
    retention.initialize()
    app.extensions.update(
        persona_voice_assets=fixture.service,
        persona_voice_policy=policy,
        persona_profile_voices=references,
        persona_voice_query=PersonaAssetQuery(
            policy=policy, catalog=fixture.catalog, references=references, cursors=cursors, kind="voice"
        ),
        persona_voice_erasure=create_voice_erasure_service(
            policy=policy, catalog=fixture.catalog, base_dir=fixture.storage.store.base_dir
        ),
        persona_voice_retention=PersonaRetentionService(policy=policy, catalog=fixture.catalog, store=retention),
    )
    return SimpleNamespace(http=http, app=app, fixture=fixture)


def body(api):
    case = api.fixture.case
    return {
        "content": base64.b64encode(case.content).decode(),
        "media_type": MEDIA_TYPE,
        "origin_binding": case.permission.source.source_id,
        "license_binding": case.permission.license.source_id,
        "consent_binding": case.permission.consent.source_id,
    }


@pytest.mark.parametrize("token", [None, "worker-token", "service-token", "invalid extra"])
def test_voice_admission_never_accepts_execution_or_missing_credentials(voice_api, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert voice_api.http.post(BASE, json=body(voice_api), headers=headers).status_code == 401
    voice_api.fixture.case.worker.execute.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"content": "not base64"},
        {"content": []},
        {"content": ""},
        {"content": "A" * 3000},
        {"media_type": "audio/wav"},
        {"media_type": "application/json"},
        {"origin_binding": {}},
        {"license_binding": None},
        {"consent_binding": "unregistered-name"},
    ],
)
def test_invalid_voice_contract_is_rejected_before_worker_execution(voice_api, change):
    assert voice_api.http.post(BASE, json=body(voice_api) | change, headers=HEADERS).status_code == 409
    voice_api.fixture.case.worker.execute.assert_not_called()


def test_admit_query_reference_metadata_preview_revoke_and_purge_are_headless(voice_api):
    api, http = voice_api, voice_api.http
    response = http.post(BASE, json=body(api), headers=HEADERS)
    assert response.status_code == 201
    asset = PersonaVoiceAsset.model_validate(response.json["asset"])
    api.fixture.case.receipts.require_asset(api.fixture.case.principal, asset)
    path = BASE + "/" + asset.voice.artifact_id
    assert http.get(path + "/reference", headers=HEADERS).json == {"reference": asset.voice.model_dump(mode="json")}
    preview = http.get(path + "/preview", headers=HEADERS)
    assert preview.data == api.fixture.case.content and preview.mimetype == MEDIA_TYPE
    assert preview.headers["Cache-Control"] == "no-store" and preview.headers["X-Content-Type-Options"] == "nosniff"
    page = http.post(BASE + "/query", json={"cursor": None, "limit": 20}, headers=HEADERS)
    assert page.json == {"items": [asset.voice.model_dump(mode="json")], "next_cursor": None, "purpose": "preview"}
    for suffix in ("publish", "synthesize", "download"):
        assert http.get(path + "/" + suffix, headers=HEADERS).status_code == 404
    assert http.get(path + "/preview?purpose=publish", headers=HEADERS).status_code == 400
    assert http.post(path + "/purge", json={"expected_revision": 2}, headers=HEADERS).status_code == 409
    assert http.delete(path, json={"expected_revision": 2}, headers=HEADERS).json == {"revision": 3, "state": "revoked"}
    assert http.get(path + "/preview", headers=HEADERS).status_code == 409
    assert http.post(path + "/purge", json={"expected_revision": 3}, headers=HEADERS).json == {
        "revision": 5,
        "state": "purged",
        "secure_device_erasure": False,
    }
    assert http.get(path + "/purge", headers=HEADERS).json == {"revision": 5, "state": "purged"}
    assert not list(api.fixture.storage.store.base_dir.rglob("v0001__*"))


def test_voice_retention_schedule_status_and_cancel_never_require_human_input(voice_api):
    api, http = voice_api, voice_api.http
    response = http.post(BASE, json=body(api), headers=HEADERS)
    path = BASE + "/" + response.json["asset"]["voice"]["artifact_id"]
    assert http.delete(path, json={"expected_revision": 2}, headers=HEADERS).status_code == 200
    schedule = {"asset_revision": 3, "expected_revision": 0, "delete_after_seconds": 60}
    assert http.put(path + "/retention", json=schedule, headers=HEADERS).json["state"] == "scheduled"
    assert http.get(path + "/retention", headers=HEADERS).json["state"] == "scheduled"
    assert http.delete(path + "/retention", json={"expected_revision": 1}, headers=HEADERS).json == {
        "revision": 2,
        "state": "cancelled",
    }
    assert len(list(api.fixture.storage.store.base_dir.rglob("v0001__*"))) == 1


@pytest.mark.parametrize("revision", [True, 0, "2", 2**53])
def test_invalid_revision_cannot_revoke_or_erase_voice(voice_api, revision):
    for method, path in (("delete", BASE + "/unknown"), ("post", BASE + "/unknown/purge")):
        assert (
            getattr(voice_api.http, method)(path, json={"expected_revision": revision}, headers=HEADERS).status_code
            == 409
        )


def test_voice_policy_requires_explicit_kind_and_matching_project(voice_api):
    http, case = voice_api.http, voice_api.fixture.case
    policy = case.permission.model_dump(mode="json") | {"revision": 2}
    path = "/api/persona-media/v1/projects/project/voice-policy"
    assert http.put(path, json={"policy": policy, "expected_revision": 1}, headers=HEADERS).json == {"revision": 2}
    assert (
        http.put(
            path, json={"policy": policy | {"media_kind": "image"}, "expected_revision": 1}, headers=HEADERS
        ).status_code
        == 409
    )
    assert (
        http.put(
            path.replace("/project/", "/other/"), json={"policy": policy, "expected_revision": 1}, headers=HEADERS
        ).status_code
        == 403
    )
    assert http.delete(
        path + "/" + policy["source"]["source_id"], json={"expected_revision": 2}, headers=HEADERS
    ).json == {"revision": 3, "state": "revoked"}


def test_disabled_voice_service_never_falls_back_to_image_or_video(voice_api):
    voice_api.app.extensions.pop("persona_voice_assets")
    assert voice_api.http.post(BASE, json=body(voice_api), headers=HEADERS).status_code == 409
    voice_api.app.extensions["persona_assets"].admit_image.assert_not_called()
    voice_api.app.config["ROLE"] = "worker"
    assert voice_api.http.post(BASE, json=body(voice_api), headers=HEADERS).status_code == 403
