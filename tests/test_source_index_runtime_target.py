import json

import pytest

from agent.services.source_index_runtime_target import (
    SOURCE_INDEX_RUNTIME_TARGET_ENV,
    load_source_index_runtime_target,
)


def _target(*, model_ids):
    return {
        "runtime_target_id": "source-index-alpha",
        "runtime_id": "codecompass-index",
        "runtime_kind": "docker_container",
        "provider_id": "codex",
        "model_ids": model_ids,
        "provider_location": "local_container",
        "data_residency": "local",
        "model_class": "code",
        "source_access_authorized": True,
        "global_source_access": True,
        "enabled": True,
        "worker_kind": "worker",
    }


def test_runtime_target_accepts_namespaced_model_identifier():
    document = _target(model_ids=["qwen/qwen3.5-9b"])
    document["model_provider_id"] = "lmstudio"
    target = load_source_index_runtime_target(
        {
            SOURCE_INDEX_RUNTIME_TARGET_ENV: json.dumps(
                document
            )
        }
    )

    assert target is not None
    assert target["model_provider_id"] == "lmstudio"
    assert target["model_ids"] == ["qwen/qwen3.5-9b"]


@pytest.mark.parametrize(
    "model_id",
    [
        "qwen model",
        "https://models.example/qwen",
        "qwen/model?revision=latest",
        "/qwen/model",
    ],
)
def test_runtime_target_rejects_unsafe_model_identifier(model_id):
    with pytest.raises(ValueError, match="model_ids must be"):
        load_source_index_runtime_target(
            {
                SOURCE_INDEX_RUNTIME_TARGET_ENV: json.dumps(
                    _target(model_ids=[model_id])
                )
            }
        )
