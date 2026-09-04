from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from ananta_contracts.research_training import canonical_digest

from .real_helpers import assignment, dataset_manifest, pipeline_spec, stage


def fixtures(tmp_path: Path) -> dict[str, dict[str, Any]]:
    dataset = dataset_manifest(tmp_path)
    definition = stage("tokenizer", "tokenizer_train", [], "tokenizer_training")
    spec = pipeline_spec(dataset, [definition])
    assigned = assignment(
        spec=spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[],
    )
    artifact = {
        "schema": "ananta.research-training-artifact.v1",
        "tenant_id": "tenant-a",
        "run_id": "research-run-real",
        "stage_id": "tokenizer",
        "attempt_id": "attempt-tokenizer",
        "artifact_kind": "tokenizer",
        "artifact_digest": "3" * 64,
        "size_bytes": 10,
        "parent_artifact_digests": [],
        "recipe_digest": canonical_digest(spec["recipe"]),
        "dataset_digest": canonical_digest(dataset),
        "executable": False,
        "source_refs": sorted(shard["source_id"] for shard in dataset["shards"]),
        "run_refs": ["RUN_tokenizer"],
    }
    runtime = assigned["runtime"]
    checkpoint = {
        "schema": "ananta.research-training-checkpoint-receipt.v1",
        "stage_id": "tokenizer",
        "attempt_id": "attempt-tokenizer",
        "optimizer_step": 1,
        "checkpoint_ref": "tokenizer/checkpoint.bin",
        "checkpoint_digest": "4" * 64,
        "size_bytes": 20,
    }
    provenance = {
        "schema": "ananta.research-training-provenance.v1",
        "run_id": "research-run-real",
        "spec_digest": canonical_digest(spec),
        "repository_revision": spec["source_revision_digest"],
        "dataset_manifest_digest": canonical_digest(dataset),
        "recipe_digest": canonical_digest(spec["recipe"]),
        "pipeline_digest": canonical_digest(spec["pipeline"]),
        "stage_artifact_digests": {"tokenizer": "3" * 64},
        "promoted_artifact_digest": "3" * 64,
        "source_ids": artifact["source_refs"],
        "evidence_run_ids": artifact["run_refs"],
        "evaluation_digest": "5" * 64,
        "quality_decision_digest": "6" * 64,
        "provenance_digest": "7" * 64,
    }
    return {
        "artifact.v1.json": artifact,
        "assignment.v1.json": assigned,
        "checkpoint-receipt.v1.json": checkpoint,
        "dataset-manifest.v1.json": dataset,
        "lineage-entry.v1.json": {
            "schema": "ananta.research-training-lineage-entry.v1",
            "tenant_id": "tenant-a",
            "run_id": "research-run-real",
            "artifact_digest": "3" * 64,
            "artifact_ref": "tenant-a/research-run-real/artifact.bin",
            "manifest": artifact,
            "replayed": False,
        },
        "metric.v1.json": {
            "schema": "ananta.research-training-metric.v1",
            "tenant_id": "tenant-a",
            "run_id": "research-run-real",
            "stage_id": "tokenizer",
            "attempt_id": "attempt-tokenizer",
            "sequence": 0,
            "metric": "train_loss",
            "value": 1.0,
            "unit": "ratio",
        },
        "pipeline.v1.json": spec["pipeline"],
        "provenance.v1.json": provenance,
        "recipe.v1.json": spec["recipe"],
        "rl-config.v1.json": {
            "schema": "ananta.research-training-rl-config.v1",
            "algorithm": "reinforce_v1",
            "samples_per_prompt": 1,
            "maximum_new_tokens": 2,
            "temperature": 1.0,
            "learning_rate": 0.0001,
            "maximum_steps": 1,
            "seed": 7,
            "reward": {
                "schema": "ananta.research-training-reward.v1",
                "reward_id": "exact-match",
                "reward_version": "v1",
                "provider": "exact_match_v1",
                "maximum_absolute_reward": 1.0,
                "redact_rollouts": True,
            },
        },
        "run.v1.json": spec,
        "runtime.v1.json": runtime,
        "tokenizer.v1.json": {
            "schema": "ananta.research-training-tokenizer.v1",
            "tokenizer_id": "tokenizer-tiny",
            "algorithm": "byte_bpe_v1",
            "artifact_digest": "3" * 64,
            "dataset_manifest_digest": canonical_digest(dataset),
            "vocab_size": 264,
            "special_tokens": ["<assistant>", "</assistant>"],
            "normalizer": "none_v1",
        },
    }


def test_every_schema_has_a_valid_positive_and_rejected_negative_fixture(tmp_path: Path) -> None:
    schema_root = Path("schemas/research-training")
    documents = fixtures(tmp_path)
    schema_names = {path.name for path in schema_root.glob("*.json")}
    assert set(documents) == schema_names
    for schema_name in sorted(schema_names):
        schema = json.loads((schema_root / schema_name).read_text())
        validator = Draft202012Validator(schema)
        positive = documents[schema_name]
        validator.validate(positive)
        negative = copy.deepcopy(positive)
        negative["unexpected_fixture_field"] = True
        with pytest.raises(ValidationError):
            validator.validate(negative)
