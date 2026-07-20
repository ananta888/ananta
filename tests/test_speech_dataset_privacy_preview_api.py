from __future__ import annotations

from flask import Flask

from agent.routes.semantic_media_privacy import semantic_media_privacy_bp
from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.speech_dataset_privacy_preview_service import SpeechDatasetPrivacyPreviewService
from agent.services.user_session_tokens import issue_user_access_token
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    digest,
    manifest_record,
    principal,
)


class ManifestReader:
    def __init__(self, manifest: dict[str, object]) -> None:
        self.manifest = manifest

    def get_by_digest(self, _principal, manifest_digest: str):
        return self.manifest if manifest_digest == self.manifest["manifest_digest"] else None


class AllowPreviewGrant:
    def authorize_raw_audio_preview(self, **bindings: object) -> bool:
        return bindings["grant_ref"] == "preview-grant-route"


class PreviewArtifacts:
    def __init__(self, manifest: dict[str, object]) -> None:
        self.manifest = manifest

    def refs_for_manifest(self, _principal, *, manifest_digest: str):
        assert manifest_digest == self.manifest["manifest_digest"]
        return {
            str(record["record_digest"]): f"artifact://speech-preview/{record['record_digest']}"
            for record in self.manifest["records"]
        }


def _manifest() -> dict[str, object]:
    prefix = "speech-preview-route"
    manifest, _created = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
    ).build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[manifest_record(f"{prefix}-{index}", group_suffix=str(index)) for index in range(3)],
        curation_report_digest=digest(f"report-{prefix}"),
    )
    return manifest


def _client(manifest: dict[str, object]):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(semantic_media_privacy_bp)
    app.extensions["speech_dataset_manifest_reader"] = ManifestReader(manifest)
    token = issue_user_access_token(username="speech-preview-owner", role="admin")
    client = app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client, app


def test_aggregate_preview_is_reachable_and_raw_audio_defaults_closed() -> None:
    manifest = _manifest()
    client, _app = _client(manifest)
    endpoint = f"/v1/semantic-media/privacy/speech-datasets/{manifest['manifest_digest']}/preview"

    aggregate = client.get(endpoint)
    assert aggregate.status_code == 200
    assert aggregate.json["data"]["record_count"] == 3
    assert aggregate.json["data"]["raw_audio_preview"] == {"authorized": False, "refs": []}
    assert "records" not in aggregate.json["data"]

    denied = client.get(f"{endpoint}?include_raw_audio=true")
    assert denied.status_code == 403
    assert denied.json["error"]["code"] == "speech_preview_raw_audio_grant_missing"
    assert client.get(f"{endpoint}?unknown=true").status_code == 400


def test_raw_audio_refs_require_separate_server_verified_preview_grant() -> None:
    manifest = _manifest()
    client, app = _client(manifest)
    app.extensions["speech_dataset_privacy_preview_service"] = SpeechDatasetPrivacyPreviewService(
        AllowPreviewGrant()
    )
    app.extensions["speech_dataset_preview_artifact_port"] = PreviewArtifacts(manifest)
    endpoint = f"/v1/semantic-media/privacy/speech-datasets/{manifest['manifest_digest']}/preview"

    response = client.get(
        f"{endpoint}?include_raw_audio=true",
        headers={"X-Speech-Preview-Grant": "preview-grant-route"},
    )
    assert response.status_code == 200
    preview = response.json["data"]["raw_audio_preview"]
    assert preview["authorized"] is True
    assert len(preview["refs"]) == 3
    assert all(value.startswith("artifact://speech-preview/") for value in preview["refs"])
