from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from ananta_contracts.research_training import canonical_digest
from ananta_contracts.research_training_data import ResearchDatasetManifestV1


def dataset_manifest(root: Path) -> dict[str, Any]:
    records = [
        {
            "messages": [
                {"role": "system", "content": "Answer briefly."},
                {"role": "user", "content": "Say hello."},
                {"role": "assistant", "content": "hello"},
            ],
            "prompt": "Say hello.",
            "expected": "hello",
        },
        {
            "messages": [
                {"role": "user", "content": "What is two plus two?"},
                {"role": "assistant", "content": "4"},
            ],
            "prompt": "What is two plus two?",
            "expected": "4",
        },
    ]
    shards: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        relative = f"datasets/{split}.jsonl"
        content = ("\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n").encode()
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        shards.append(
            {
                "source_id": f"SRC_{split}",
                "relative_ref": relative,
                "content_digest": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "split": split,
                "media_type": "application_jsonl",
                "license_id": "synthetic-test",
                "consent_class": "synthetic",
                "pii_scan_digest": canonical_digest({"matches": []}),
                "secret_scan_digest": canonical_digest({"matches": []}),
                "dedup_digest": canonical_digest([hashlib.sha256(content).hexdigest()]),
            }
        )
    return ResearchDatasetManifestV1.from_mapping(
        {
            "schema": ResearchDatasetManifestV1.SCHEMA,
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "policy_digest": "1" * 64,
            "contamination_check_digest": "2" * 64,
            "shards": shards,
        }
    ).to_dict()


def pipeline_spec(dataset: dict[str, Any], stage_definitions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "ananta.research-training-run.v1",
        "spec_id": "tiny-real-run",
        "tenant_id": "tenant-a",
        "mode": "live",
        "dataset_manifest_digest": canonical_digest(dataset),
        "source_revision_digest": "a" * 64,
        "recipe": {
            "schema": "ananta.research-training-recipe.v1",
            "recipe_id": "tiny-real",
            "recipe_version": "explicit-v1",
            "model_family": "tiny-local",
            "architecture": "decoder-transformer",
            "depth": 1,
            "context_length": 128,
            "vocab_size": 264,
            "max_steps": 2,
            "seed": 7,
            "precision": "float32",
            "world_size": 1,
            "allow_rl": any(stage["kind"] == "rl" for stage in stage_definitions),
            "resolved_hyperparameters": {
                "num_layers": 1,
                "hidden_size": 32,
                "attention_heads": 4,
                "learning_rate": 0.001,
                "weight_decay": 0.0,
            },
        },
        "pipeline": {
            "schema": "ananta.research-training-pipeline.v1",
            "pipeline_id": "tiny-real-pipeline",
            "pipeline_version": "v1",
            "stages": stage_definitions,
            "automatic_release": False,
        },
        "budget": {
            "gpu_hours": 1.0,
            "storage_bytes": 100_000_000,
            "estimated_cost_microunits": 0,
        },
    }


def stage(
    stage_id: str,
    kind: str,
    dependencies: list[str],
    capability: str,
) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "kind": kind,
        "dependencies": dependencies,
        "required_capability": capability,
        "max_attempts": 2,
        "timeout_seconds": 120,
    }


def assignment(
    *,
    spec: dict[str, Any],
    dataset: dict[str, Any],
    stage_definition: dict[str, Any],
    inputs: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    suffix = stage_definition["stage_id"]
    evidence = build_hub_evidence_assignment(
        run_id=f"RUN_{suffix}",
        task_id=f"task-{suffix}",
        assignment_id=f"assignment-{suffix}",
        dispatch_lease_id=f"lease-{suffix}",
        source_ids=sorted(shard["source_id"] for shard in dataset["shards"]),
        evidence_scope="test",
        binding_digest="b" * 64,
    )
    return {
        "schema": "ananta.research-training-stage-assignment.v1",
        "task_id": f"task-{suffix}",
        "assignment_id": f"assignment-{suffix}",
        "dispatch_lease_id": f"lease-{suffix}",
        "attempt_id": f"attempt-{suffix}",
        "worker_id": "worker-real-cpu",
        "quota_reservation_id": f"assignment-{suffix}",
        "run_id": "research-run-real",
        "run_spec": spec,
        "stage": stage_definition,
        "dataset_manifest": dataset,
        "runtime": {
            "schema": "ananta.research-training-runtime.v1",
            "repository_revision": "a" * 64,
            "image_digest": "c" * 64,
            "python_version": "3.12.14",
            "torch_version": "2.6.0+cpu",
            "cuda_version": "none",
            "backend_name": "ananta-local-torch",
            "backend_version": "v1",
            "hardware_profile_digest": "d" * 64,
            "deterministic_algorithms": True,
        },
        "inputs": inputs,
        "parameters": parameters or {},
        "workspace_subdir": "workspace",
        "hub_evidence": evidence,
    }


def persist_artifact(root: Path, kind: str, content: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(content).hexdigest()
    relative = f"artifacts/{kind}-{digest}.bin"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        "artifact_kind": kind,
        "artifact_digest": digest,
        "size_bytes": len(content),
        "relative_ref": relative,
    }
