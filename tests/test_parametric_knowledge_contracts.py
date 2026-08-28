from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.knowledge_expert_runtime import KnowledgeExpertRuntimeCapability
from ananta_contracts.knowledge_expert_task import KnowledgeExpertTask
from ananta_contracts.parametric_knowledge import (
    KnowledgeExpertBank,
    KnowledgeExpertManifest,
    ParametricKnowledgeContractError,
    ParametricKnowledgeUnit,
)

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "a" * 64


def _unit(**overrides):
    payload = {
        "schema": "ananta.parametric-knowledge-unit.v1",
        "unit_id": "unit-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "source_id": "SRC_0001",
        "source_revision": "rev-1",
        "content_hash": DIGEST,
        "provenance_digest": "b" * 64,
        "domain": "payments",
        "parent_id": "",
        "relations": ["calls:retry"],
        "sensitivity": "public",
        "retention_until": "2099-01-01T00:00:00Z",
        "license_spdx": "MIT",
        "citation_ref": "citation-1",
        "citation_required": False,
        "stable": True,
        "approval_state": "approved",
        "revoked": False,
    }
    payload.update(overrides)
    return payload


def _manifest(**overrides):
    payload = {
        "schema": "ananta.knowledge-expert-manifest.v1",
        "expert_id": "expert-1",
        "generation_id": "generation-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "knowledge_unit_ids": ["unit-1"],
        "knowledge_unit_digest": DIGEST,
        "adapter_format": "safetensors",
        "adapter_digest": "b" * 64,
        "adapter_size_bytes": 1024,
        "compatibility": {
            "base_model_digest": "c" * 64,
            "tokenizer_digest": "d" * 64,
            "architecture": "llama",
            "target_layer": "final_ffn",
            "target_modules": ["down_proj", "up_proj"],
            "runtime_provider": "transformers_prototype",
            "runtime_version": "1.0",
            "kv_cache_safe": True,
        },
        "peft_configuration_digest": "e" * 64,
        "training_dataset_digest": "f" * 64,
        "evaluation_status": "passed",
        "evaluation_digest": "1" * 64,
        "policy_decision_digest": "2" * 64,
        "signing_key_id": "key-1",
        "signature": "signed-value",
    }
    payload.update(overrides)
    return payload


def test_contracts_parse_closed_bound_payloads():
    unit = ParametricKnowledgeUnit.from_mapping(_unit())
    expert = KnowledgeExpertManifest.from_mapping(_manifest())
    bank = KnowledgeExpertBank.from_mapping(
        {
            "schema": "ananta.knowledge-expert-bank.v1",
            "bank_id": "bank-1",
            "generation_id": "generation-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "repository_id": "repo-1",
            "previous_generation_id": "",
            "expert_manifest_digests": [expert.manifest_digest],
            "status": "candidate",
            "policy_digest": "3" * 64,
            "created_at": "2026-08-27T00:00:00Z",
            "signing_key_id": "key-1",
            "signature": "signed-bank",
        }
    )
    assert unit.binding_digest
    assert expert.compatibility.target_layer == "final_ffn"
    assert bank.expert_manifest_digests == (expert.manifest_digest,)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({**_unit(), "unexpected": True}, "parametric_knowledge_unit_invalid_unknown_fields"),
        (_unit(source_id="invented-source"), "parametric_knowledge_source_invalid"),
        (_manifest(adapter_format="pickle"), "knowledge_expert_adapter_format_denied"),
        (
            _manifest(compatibility={**_manifest()["compatibility"], "base_model_digest": "bad"}),
            "knowledge_expert_model_digest_invalid",
        ),
    ],
)
def test_contracts_fail_closed(payload, reason):
    parser = (
        ParametricKnowledgeUnit.from_mapping
        if payload.get("schema", "").endswith("unit.v1")
        else KnowledgeExpertManifest.from_mapping
    )
    with pytest.raises(ParametricKnowledgeContractError, match=reason):
        parser(payload)


def test_json_schemas_are_valid_and_accept_golden_payloads():
    fixtures = {
        "parametric_knowledge_unit.v1.json": _unit(),
        "knowledge_expert_manifest.v1.json": _manifest(),
    }
    for filename, payload in fixtures.items():
        schema = json.loads((ROOT / "schemas/knowledge" / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert list(Draft202012Validator(schema).iter_errors(payload)) == []


def test_runtime_and_task_contracts_are_closed_and_schema_validated():
    runtime_payload = {
        "schema": "ananta.knowledge-expert-runtime-capability.v1",
        "provider_id": "runtime-1",
        "provider_version": "1",
        "base_model_digest": "a" * 64,
        "tokenizer_digest": "b" * 64,
        "architecture": "llama",
        "final_layer_name": "model.layers.15.mlp",
        "supported_target_modules": ["down_proj"],
        "dynamic_adapter_composition": True,
        "token_entropy": True,
        "kv_cache_safe_final_ffn": True,
        "atomic_expert_switch": True,
        "max_active_experts": 2,
    }
    task_payload = {
        "schema": "ananta.knowledge-expert-task.v1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "task_type": "train_expert",
        "worker_audience": "worker-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "input_digest": "c" * 64,
        "policy_digest": "d" * 64,
        "deadline": "2026-08-28T00:00:00Z",
    }
    fixtures = {
        "knowledge_expert_runtime_capability.v1.json": runtime_payload,
        "knowledge_expert_task.v1.json": task_payload,
    }
    for filename, payload in fixtures.items():
        schema = json.loads((ROOT / "schemas/knowledge" / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    assert KnowledgeExpertRuntimeCapability.from_mapping(runtime_payload).token_entropy is True
    assert KnowledgeExpertTask.from_mapping(task_payload).task_type == "train_expert"
    with pytest.raises(ValueError, match="shape_invalid"):
        KnowledgeExpertTask.from_mapping({**task_payload, "activate": True})


def test_manifest_golden_and_negative_fixtures_are_deterministic():
    fixtures = ROOT / "tests/fixtures/scenarios/knowledge-experts"
    golden = json.loads((fixtures / "manifest.golden.json").read_text(encoding="utf-8"))
    invalid = json.loads((fixtures / "manifest.invalid-unsafe-format.json").read_text(encoding="utf-8"))
    assert KnowledgeExpertManifest.from_mapping(golden).expert_id == "expert-fixture"
    with pytest.raises(ParametricKnowledgeContractError, match="adapter_format_denied"):
        KnowledgeExpertManifest.from_mapping(invalid)
