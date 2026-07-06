from agent.services.text_quality.config import (
    normalize_text_quality_config,
    text_quality_read_model,
)


def test_config_defaults_are_safe_and_bounds_are_normalized():
    config = normalize_text_quality_config(
        {
            "enabled": True,
            "max_input_chars": -1,
            "max_slop_score": 9,
            "external_detectors": {
                "avoid_ai_writing": {
                    "network": "allowed",
                    "execution_plane": "host",
                    "context_mode": "invalid",
                }
            },
        }
    )
    assert config["max_input_chars"] == 100
    assert config["max_slop_score"] == 1
    provider = config["external_detectors"]["avoid_ai_writing"]
    assert provider["network"] == "none"
    assert provider["execution_plane"] == "sandbox"
    assert provider["context_mode"] == "technical"


def test_read_model_exposes_health_without_raw_pin_values():
    model = text_quality_read_model(
        {
            "external_detectors": {
                "avoid_ai_writing": {
                    "enabled": True,
                    "pinned_commit": "secret-ish-path-free-pin",
                    "detector_sha256": "digest",
                }
            }
        }
    )
    provider = model["external_detectors"]["avoid_ai_writing"]
    assert provider["pin_configured"]
    assert "pinned_commit" not in provider
