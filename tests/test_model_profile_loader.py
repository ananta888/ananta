"""Tests for ModelProfileLoader and ModelProfile — AMR-007."""
import pytest

from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader

MINIMAL_LOCAL = {
    "profiles": [
        {
            "profile_id": "local-ollama",
            "provider_id": "ollama",
            "model": "qwen2.5-coder:7b",
        }
    ]
}

CLOUD_COMPLETE = {
    "profiles": [
        {
            "profile_id": "openai-gpt4",
            "provider_id": "openai",
            "model": "gpt-4o",
            "cloud": True,
            "cloud_allowed": True,
            "block_secret_context": True,
        }
    ]
}

CLOUD_MISSING_FIELDS = {
    "profiles": [
        {
            "profile_id": "cloud-bad",
            "provider_id": "openai",
            "model": "gpt-4",
            # cloud=True implicit via provider, but no cloud_allowed or block_secret_context
        }
    ]
}


def test_load_minimal_local_profile():
    loader = ModelProfileLoader()
    result = loader.load_dict(MINIMAL_LOCAL)
    assert result.ok
    assert len(result.profiles) == 1
    p = result.profiles[0]
    assert p.profile_id == "local-ollama"
    assert p.provider_id == "ollama"
    assert p.model == "qwen2.5-coder:7b"
    assert p.supports_seed is True
    assert not p.is_cloud()


def test_profile_can_declare_runtime_without_per_request_seed():
    data = {
        "profiles": [{
            "profile_id": "local-colibri",
            "provider_id": "openai_compatible",
            "model": "kat",
            "supports_seed": False,
        }]
    }

    result = ModelProfileLoader().load_dict(data)

    assert result.ok
    assert result.profiles[0].supports_seed is False
    assert "supports_seed" not in result.profiles[0].extra


def test_unsafe_research_profile_requires_explicit_safety_modified() -> None:
    invalid = ModelProfileLoader().load_dict({"profiles": [{
        "profile_id": "unsafe", "provider_id": "ollama", "model": "modified",
        "trust_class": "unsafe_research", "safety_modified": False,
    }]})
    assert not invalid.ok
    assert "unsafe_research_requires_safety_modified" in " ".join(invalid.errors)

    valid = ModelProfileLoader().load_dict({"profiles": [{
        "profile_id": "unsafe", "provider_id": "ollama", "model": "modified",
        "trust_class": "unsafe_research", "safety_modified": True, "enabled": False,
    }]})
    assert valid.ok
    assert valid.profiles[0].trust_class == "unsafe_research"


def test_load_cloud_profile_complete():
    loader = ModelProfileLoader()
    result = loader.load_dict(CLOUD_COMPLETE)
    assert result.ok
    p = result.profiles[0]
    assert p.cloud_allowed is True
    assert p.block_secret_context is True
    assert p.is_cloud()


def test_cloud_profile_missing_cloud_allowed_produces_error():
    loader = ModelProfileLoader()
    result = loader.load_dict(CLOUD_MISSING_FIELDS)
    assert not result.ok
    joined = " ".join(result.errors)
    assert "cloud_profile_missing_cloud_allowed" in joined


def test_cloud_profile_missing_block_secret_context_produces_error():
    data = {
        "profiles": [
            {
                "profile_id": "cloud-partial",
                "provider_id": "openai",
                "model": "gpt-4",
                "cloud_allowed": True,
                # block_secret_context missing
            }
        ]
    }
    loader = ModelProfileLoader()
    result = loader.load_dict(data)
    assert not result.ok
    assert any("block_secret_context" in e for e in result.errors)


def test_duplicate_profile_id_rejected():
    data = {
        "profiles": [
            {"profile_id": "dup", "provider_id": "ollama", "model": "m"},
            {"profile_id": "dup", "provider_id": "ollama", "model": "m2"},
        ]
    }
    result = ModelProfileLoader().load_dict(data)
    assert not result.ok
    assert any("duplicate_id" in e for e in result.errors)


def test_missing_required_fields_rejected():
    data = {"profiles": [{"provider_id": "ollama", "model": "m"}]}
    result = ModelProfileLoader().load_dict(data)
    assert not result.ok
    assert any("missing_profile_id" in e for e in result.errors)


def test_file_not_found():
    result = ModelProfileLoader().load_file("/nonexistent/path.json")
    assert not result.ok
    assert any("file_not_found" in e for e in result.errors)


def test_profile_is_usable_with_secrets_local():
    p = ModelProfile(profile_id="local", provider_id="ollama", model="m", local=True)
    assert p.is_usable_with_secrets()


def test_profile_is_usable_with_secrets_cloud_blocks():
    p = ModelProfile(
        profile_id="cloud",
        provider_id="openai",
        model="gpt-4",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )
    assert not p.is_usable_with_secrets()


def test_legacy_migration_lmstudio():
    loader = ModelProfileLoader()
    legacy = {
        "profiles": [
            {
                "profile_name": "my-lmstudio",
                "provider": "lmstudio",
                "model_name_pattern": "auto",
                "enabled": True,
            }
        ]
    }
    result = loader.migrate_legacy(legacy)
    assert len(result.profiles) == 1
    p = result.profiles[0]
    assert p.profile_id == "my-lmstudio"
    assert p.provider_id == "lmstudio"
    assert not p.is_cloud()
    assert p.extra.get("_legacy") is True


def test_extra_fields_preserved():
    data = {
        "profiles": [
            {
                "profile_id": "p1",
                "provider_id": "ollama",
                "model": "m",
                "custom_field": "hello",
            }
        ]
    }
    result = ModelProfileLoader().load_dict(data)
    assert result.ok
    assert result.profiles[0].extra.get("custom_field") == "hello"


def test_hybrid_profile_fields_are_parsed():
    data = {
        "profiles": [
            {
                "profile_id": "openrouter_qwen",
                "provider_id": "openrouter",
                "model": "qwen/qwen3-30b-a3b-instruct-2507",
                "cloud": True,
                "cloud_allowed": True,
                "block_secret_context": True,
                "supports_tools": True,
                "supports_json": True,
                "tool_calling_mode": "both",
                "json_reliability_class": "strict",
                "price_input_per_million": 0.12,
                "price_output_per_million": 0.30,
                "preferred_for": ["architecture"],
                "fallback_group": "local_first_cheap",
                "fallback_rank": 30,
                "retry_budget": 1,
            }
        ]
    }
    result = ModelProfileLoader().load_dict(data)
    assert result.ok
    profile = result.profiles[0]
    assert profile.tool_calling_mode == "both"
    assert profile.json_reliability_class == "strict"
    assert profile.price_input_per_million == 0.12
    assert profile.fallback_group == "local_first_cheap"
    assert profile.fallback_rank == 30


def test_evidence_aware_profile_fields_are_validated_and_projected():
    data = {
        "profiles": [{
            "profile_id": "local-evidence-aware",
            "provider_id": "ollama",
            "model": "model@sha256:abc",
            "enabled": False,
            "aliases": ["stable-alias"],
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
            "nominal_context_tokens": 262144,
            "verified_context_tokens": 8192,
            "hardware_class": "rtx3080-10gb",
            "artifact_sha256": "a" * 64,
            "release_state": "experimental",
            "capability_claims": [{
                "capability_id": "vision",
                "value": "unknown",
                "evidence": "unknown",
                "source_id": "profile:test",
            }],
        }]
    }

    result = ModelProfileLoader().load_dict(data)

    assert result.ok
    profile = result.profiles[0]
    assert profile.verified_context_tokens == 8192
    assert profile.capability_claims[0]["value"] == "unknown"
    assert "capability_claims" not in profile.extra


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("artifact_sha256", "not-a-digest", "artifact_sha256 invalid"),
        ("release_state", "production", "invalid_release_state"),
        (
            "capability_claims",
            [{"capability_id": "tools", "value": "yes", "evidence": "declared"}],
            "capability_claim_invalid",
        ),
        ("verified_context_tokens", 300000, "exceeds nominal_context_tokens"),
    ],
)
def test_evidence_aware_profile_fields_fail_closed(field, value, error):
    profile = {
        "profile_id": "invalid-evidence-profile",
        "provider_id": "ollama",
        "model": "model",
        "nominal_context_tokens": 262144,
        field: value,
    }

    result = ModelProfileLoader().load_dict({"profiles": [profile]})

    assert not result.ok
    assert error in " ".join(result.errors)
