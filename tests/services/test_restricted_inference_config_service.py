from __future__ import annotations

from agent.services.restricted_inference_config_service import (
    ENGINE_MOCK,
    ENGINE_SENTENCE_TRANSFORMERS,
    RestrictedInferenceConfigService,
    TASK_CANDIDATE_RERANK,
)


def test_from_config_empty_returns_safe_mock_defaults() -> None:
    cfg = RestrictedInferenceConfigService.from_config({})

    assert cfg.enabled is True
    assert cfg.default_engine == ENGINE_MOCK
    assert cfg.allow_mock_fallback is True
    assert cfg.models[0].engine == ENGINE_MOCK
    assert TASK_CANDIDATE_RERANK in cfg.tasks


def test_valid_config_models_tasks_and_redacts_secrets() -> None:
    cfg = RestrictedInferenceConfigService.from_config({
        "restricted_inference": {
            "default_engine": "pytorch",
            "allowed_engines": ["pytorch", "mock"],
            "models": [
                {
                    "id": "local",
                    "engine": "pytorch",
                    "model": "local",
                    "local_path": "/does/not/exist",
                    "tasks": ["candidate_rerank"],
                    "api_token": "secret",
                }
            ],
            "tasks": {
                "candidate_rerank": {
                    "enabled": True,
                    "preferred_engine": "pytorch",
                    "max_candidates": 8,
                }
            },
        }
    })

    payload = cfg.as_dict(redact_secrets=True)
    assert payload["models"][0]["api_token"] == "<redacted>"
    assert payload["tasks"]["candidate_rerank"]["max_candidates"] == 8


def test_explicit_sentence_transformers_config_bridges_embedding_options() -> None:
    cfg = RestrictedInferenceConfigService.from_config({
        "embedding_model_id": "intfloat/multilingual-e5-small",
        "embedding_lang_detect": True,
        "embedding_lang_model_de": "deepset/gbert-base",
        "embedding_lang_model_en": "all-MiniLM-L6-v2",
        "restricted_inference": {
            "default_engine": ENGINE_SENTENCE_TRANSFORMERS,
        },
    })

    assert cfg.default_engine == ENGINE_SENTENCE_TRANSFORMERS
    assert cfg.default_model_id == "intfloat/multilingual-e5-small"
    assert cfg.models[0].engine == ENGINE_SENTENCE_TRANSFORMERS
    assert cfg.models[0].options["lang_detect"] is True
    assert cfg.models[0].options["lang_model_map"] == {
        "de": "deepset/gbert-base",
        "en": "all-MiniLM-L6-v2",
        "*": "intfloat/multilingual-e5-small",
    }


def test_diagnostics_report_unknown_engine_disabled_model_and_missing_dependency() -> None:
    svc = RestrictedInferenceConfigService(global_config={
        "restricted_inference": {
            "allowed_engines": ["unknown"],
            "models": [
                {"id": "m1", "engine": "unknown", "enabled": False},
                {"id": "m2", "engine": "pytorch", "local_path": "/missing"},
            ],
        }
    })

    codes = {diag.reason_code for diag in svc.diagnostics(dependency_status={"pytorch": "degraded"})}
    assert "unknown_engine" in codes
    assert "disabled_model" in codes
    assert "invalid_local_path" in codes
    assert "missing_dependency" in codes
