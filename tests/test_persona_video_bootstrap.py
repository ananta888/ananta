"""Opt-in Hub composition never silently enables images or grants source rights."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from flask import Flask
from sqlalchemy import inspect, select

from agent.bootstrap.persona_media import configure_persona_media
from agent.bootstrap.persona_videos import configure_persona_videos
from agent.models.persona_asset_policy import PersonaVideoPolicy
from agent.repositories.persona_video_policies import heads
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_http import KEY


@pytest.mark.parametrize("role,enabled", [("hub", None), ("hub", "0"), ("hub", "true"), ("worker", "1")])
def test_video_composition_is_exactly_opt_in_and_hub_only(monkeypatch, role, enabled):
    app = Flask(__name__)
    app.config["ROLE"] = role
    if enabled is None:
        monkeypatch.delenv("ANANTA_PERSONA_VIDEOS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ANANTA_PERSONA_VIDEOS_ENABLED", enabled)
    configure_persona_videos(app)
    assert not any(name.startswith("persona_video") for name in app.extensions)


def environment(tmp_path):
    key_path = tmp_path / "private-video-key"
    key_path.write_bytes(KEY)
    key_path.chmod(0o600)
    return {
        "ANANTA_PERSONA_IMAGES_ENABLED": "0",
        "ANANTA_PERSONA_VIDEOS_ENABLED": "1",
        "ANANTA_PERSONA_VIDEO_KEY_FILE": str(key_path),
        "ANANTA_PERSONA_VIDEO_WORKER_URL": "http://video-worker.test:8096/v1/persona-videos",
        "ANANTA_PERSONA_VIDEO_REPOSITORY_REVISION": "1" * 40,
        "ANANTA_PERSONA_VIDEO_EXECUTION_PROFILE_DIGEST": "a" * 64,
        "ANANTA_PERSONA_VIDEO_ENVIRONMENT_DIGEST": "b" * 64,
    }


def configure_test_ports(patch, base, tmp_path):
    from agent.services.artifact_store import ArtifactStore

    store = ArtifactStore(tmp_path / "private-assets")
    patch.setattr("agent.database.engine", base.engine)
    patch.setattr(
        "agent.services.hub_evidence_registry_service.get_hub_evidence_registry_service", lambda: base.registry
    )
    patch.setattr("agent.services.artifact_store.ArtifactStore", lambda: store)


def test_video_only_bootstrap_composes_exact_policy_task_receipt_and_erasure_ports(request, monkeypatch, tmp_path):
    base = request.getfixturevalue("runtime")
    app = Flask("video-only-composition")
    app.config["ROLE"] = "hub"
    access = app.extensions["project_access_authority"] = Mock()
    with monkeypatch.context() as patch:
        for key, value in environment(tmp_path).items():
            patch.setenv(key, value)
        configure_test_ports(patch, base, tmp_path)
        configure_persona_media(app)
    service = app.extensions["persona_video_assets"]
    policy = app.extensions["persona_video_policy"]
    leases = app.extensions["persona_video_leases"]
    assert service.policy is policy and service.tasks.policy is policy and leases.policy is policy
    assert policy.access is access and policy.domain.policy_type is PersonaVideoPolicy
    assert service.tasks.registry is base.registry and service.tasks.format.kind == "video"
    assert policy.inspection_receipts.format.kind == "video"
    assert app.extensions["persona_video_erasure"].catalog is service.catalog
    assert app.extensions["persona_video_worker_key"] == KEY
    assert "persona_assets" not in app.extensions and "persona_profiles" not in app.extensions
    assert app.extensions["persona_video_retention"].catalog is service.catalog
    assert app.extensions["persona_video_retention_runner"].tasks.kind == "video"
    assert "persona_video_retention_reconciler" not in app.extensions
    with base.engine.connect() as connection:
        assert not list(connection.execute(select(heads)))
    assert "/api/persona-media/v1/internal/video-lease" in {rule.rule for rule in app.url_map.iter_rules()}
    assert app.test_client().post("/api/persona-media/v1/projects/project/videos", json={}).status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("ANANTA_PERSONA_VIDEO_WORKER_URL", "http://worker.test:8095/v1/persona-images"),
        ("ANANTA_PERSONA_VIDEO_REPOSITORY_REVISION", "main"),
        ("ANANTA_PERSONA_VIDEO_EXECUTION_PROFILE_DIGEST", "unknown"),
        ("ANANTA_PERSONA_VIDEO_ENVIRONMENT_DIGEST", ""),
    ],
)
def test_invalid_execution_configuration_never_installs_video_authority(request, monkeypatch, tmp_path, field, value):
    base = request.getfixturevalue("runtime")
    app = Flask("invalid-video-composition")
    app.config["ROLE"] = "hub"
    app.extensions["project_access_authority"] = Mock()
    with monkeypatch.context() as patch:
        for key, setting in (environment(tmp_path) | {field: value}).items():
            patch.setenv(key, setting)
        configure_test_ports(patch, base, tmp_path)
        with pytest.raises(ValueError):
            configure_persona_videos(app)
    assert not any(name.startswith("persona_video") for name in app.extensions)
    assert "persona_video_assets" not in inspect(base.engine).get_table_names()


def test_video_hub_overlay_is_separate_private_and_disabled_by_default():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "docker-compose.persona-videos-hub.yml").read_text())
    hub = config["services"]["ai-agent-hub"]
    assert hub["environment"]["ANANTA_PERSONA_VIDEOS_ENABLED"] == "${ANANTA_PERSONA_VIDEOS_ENABLED:-0}"
    assert (
        hub["environment"]["ANANTA_PERSONA_VIDEO_RETENTION_ENABLED"] == "${ANANTA_PERSONA_VIDEO_RETENTION_ENABLED:-0}"
    )
    assert "ANANTA_PERSONA_IMAGES_ENABLED" not in hub["environment"]
    assert hub["environment"]["ANANTA_PERSONA_VIDEO_WORKER_URL"].endswith(":8096/v1/persona-videos")
    assert "ports" not in hub and set(hub["networks"]) == {"persona-videos"}
    assert config["networks"]["persona-videos"]["external"] is True
