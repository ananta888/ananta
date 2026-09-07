"""Voice-only opt-in composes isolated policy/task/retirement without granting rights."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from flask import Flask
from sqlalchemy import inspect, select

from agent.bootstrap.persona_media import configure_persona_media
from agent.bootstrap.persona_voices import configure_persona_voices
from agent.models.persona_asset_policy import PersonaVoicePolicy
from agent.repositories.persona_voice_policies import heads
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_bootstrap import configure_test_ports
from tests.test_persona_voice_http import KEY


def environment(tmp_path):
    key_path = tmp_path / "voice-key"
    key_path.write_bytes(KEY)
    key_path.chmod(0o600)
    return {
        "ANANTA_PERSONA_IMAGES_ENABLED": "0",
        "ANANTA_PERSONA_VIDEOS_ENABLED": "0",
        "ANANTA_PERSONA_VOICES_ENABLED": "1",
        "ANANTA_PERSONA_VOICE_KEY_FILE": str(key_path),
        "ANANTA_PERSONA_VOICE_WORKER_URL": "http://voice-worker.test:8097/v1/persona-voices",
        "ANANTA_PERSONA_VOICE_REPOSITORY_REVISION": "1" * 40,
        "ANANTA_PERSONA_VOICE_EXECUTION_PROFILE_DIGEST": "a" * 64,
        "ANANTA_PERSONA_VOICE_ENVIRONMENT_DIGEST": "b" * 64,
    }


@pytest.mark.parametrize("role,enabled", [("hub", None), ("hub", "0"), ("hub", "true"), ("worker", "1")])
def test_voice_configuration_is_exactly_opt_in_and_hub_only(monkeypatch, role, enabled):
    app = Flask(__name__)
    app.config["ROLE"] = role
    if enabled is None:
        monkeypatch.delenv("ANANTA_PERSONA_VOICES_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ANANTA_PERSONA_VOICES_ENABLED", enabled)
    configure_persona_voices(app)
    assert not any(name.startswith("persona_voice") for name in app.extensions)


def test_voice_only_composition_includes_receipts_profiles_query_and_retirement(request, monkeypatch, tmp_path):
    base = request.getfixturevalue("runtime")
    app = Flask("voice-only-composition")
    app.config["ROLE"] = "hub"
    app.extensions["project_access_authority"] = Mock()
    with monkeypatch.context() as patch:
        for name, value in environment(tmp_path).items():
            patch.setenv(name, value)
        configure_test_ports(patch, base, tmp_path)
        configure_persona_media(app)
    assets = app.extensions["persona_voice_assets"]
    policy = app.extensions["persona_voice_policy"]
    assert assets.policy is policy and assets.tasks.policy is policy
    assert policy.domain.policy_type is PersonaVoicePolicy
    assert assets.tasks.registry is base.registry and assets.tasks.format.kind == "voice"
    assert policy.inspection_receipts.format.kind == "voice"
    assert app.extensions["persona_voice_leases"].policy is policy
    assert app.extensions["persona_voice_erasure"].catalog is assets.catalog
    assert app.extensions["persona_voice_worker_key"] == KEY
    profiles = app.extensions["persona_profiles"]
    assert profiles.images is None and profiles.videos is None
    assert profiles.voices is app.extensions["persona_profile_voices"]
    assert app.extensions["persona_voice_retention_runner"].tasks.kind == "voice"
    assert app.extensions["persona_voice_query"].references is profiles.voices
    tables = inspect(base.engine).get_table_names()
    assert "persona_voice_cursors" in tables and "persona_image_cursors" not in tables
    assert "persona_assets" not in app.extensions and "persona_video_assets" not in app.extensions
    assert "persona_voice_retention_reconciler" not in app.extensions
    with base.engine.connect() as connection:
        assert not list(connection.execute(select(heads)))
    assert "/api/persona-media/v1/internal/voice-lease" in {rule.rule for rule in app.url_map.iter_rules()}
    assert app.test_client().post("/api/persona-media/v1/projects/project/voices", json={}).status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("ANANTA_PERSONA_VOICE_WORKER_URL", "http://worker.test:8096/v1/persona-videos"),
        ("ANANTA_PERSONA_VOICE_REPOSITORY_REVISION", "main"),
        ("ANANTA_PERSONA_VOICE_EXECUTION_PROFILE_DIGEST", "unknown"),
        ("ANANTA_PERSONA_VOICE_ENVIRONMENT_DIGEST", ""),
    ],
)
def test_invalid_execution_pins_never_install_voice_authority(request, monkeypatch, tmp_path, field, value):
    base = request.getfixturevalue("runtime")
    app = Flask("invalid-voice-composition")
    app.config["ROLE"] = "hub"
    app.extensions["project_access_authority"] = Mock()
    with monkeypatch.context() as patch:
        for name, setting in (environment(tmp_path) | {field: value}).items():
            patch.setenv(name, setting)
        configure_test_ports(patch, base, tmp_path)
        with pytest.raises(ValueError):
            configure_persona_voices(app)
    assert not any(name.startswith("persona_voice") for name in app.extensions)
    assert "persona_voice_assets" not in inspect(base.engine).get_table_names()


def test_hub_overlay_does_not_enable_retention_images_video_or_public_ports():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "docker-compose.persona-voices-hub.yml").read_text())
    hub = config["services"]["ai-agent-hub"]
    assert hub["environment"]["ANANTA_PERSONA_VOICES_ENABLED"] == "${ANANTA_PERSONA_VOICES_ENABLED:-0}"
    assert (
        hub["environment"]["ANANTA_PERSONA_VOICE_RETENTION_ENABLED"] == "${ANANTA_PERSONA_VOICE_RETENTION_ENABLED:-0}"
    )
    assert (
        "ANANTA_PERSONA_IMAGES_ENABLED" not in hub["environment"]
        and "ANANTA_PERSONA_VIDEOS_ENABLED" not in hub["environment"]
    )
    assert "ports" not in hub and set(hub["networks"]) == {"persona-voices"}
