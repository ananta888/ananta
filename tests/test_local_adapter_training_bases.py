from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.local_adapter_training_base_verifier import LocalAdapterTrainingBaseVerifier
from ananta_contracts.local_adapter_training_base import LocalAdapterTrainingBaseCatalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/models/local-adapter-training-bases.v1.json"


def _catalog() -> LocalAdapterTrainingBaseCatalog:
    return LocalAdapterTrainingBaseCatalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_training_bases_pin_exact_agentic_and_needle_lineage() -> None:
    catalog = _catalog()
    by_target = {base.release_target: base for base in catalog.bases}

    needle = by_target["needle2"]
    assert needle.upstream_revision == "98fbd955b0347e78059be0c253cc1ffa09b87bc7"
    assert needle.training_runtime is not None
    assert needle.training_runtime.version == "2.0.9"
    assert needle.serving_baseline.compatibility_basis == "same_upstream_revision"

    lfm = by_target["lfm2.5-2.6b-agentic"]
    assert lfm.upstream_model_id == "LiquidAI/LFM2.5-2.6B"
    assert not lfm.upstream_model_id.endswith("-Base")
    assert lfm.upstream_revision == "654f9463ce32b05d0429d76fe1f580b27d4c1ac0"
    assert {artifact.relative_path for artifact in lfm.artifacts} == {
        ".gitattributes",
        "LICENSE",
        "README.md",
        "chat_template.jinja",
        "config.json",
        "generation_config.json",
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
        "model.safetensors.index.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }


def test_training_base_verifier_is_fail_closed(tmp_path: Path) -> None:
    original = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    needle_payload = original["bases"][0]
    content = b"pinned checkpoint"
    tokenizer = b"pinned tokenizer"
    needle_payload["artifacts"] = [
        {
            "relative_path": "checkpoint.bin",
            "role": "checkpoint",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        {
            "relative_path": "tokenizer.model",
            "role": "tokenizer",
            "size_bytes": len(tokenizer),
            "sha256": hashlib.sha256(tokenizer).hexdigest(),
        },
    ]
    canonical = "".join(
        [
            f"checkpoint.bin\0{len(content)}\0{hashlib.sha256(content).hexdigest()}\n",
            f"tokenizer.model\0{len(tokenizer)}\0{hashlib.sha256(tokenizer).hexdigest()}\n",
        ]
    ).encode()
    needle_payload["snapshot_tree_sha256"] = hashlib.sha256(canonical).hexdigest()
    original["bases"][0] = needle_payload
    base = LocalAdapterTrainingBaseCatalog.model_validate(original).bases[0]
    (tmp_path / "checkpoint.bin").write_bytes(content)
    (tmp_path / "tokenizer.model").write_bytes(tokenizer)

    verified = LocalAdapterTrainingBaseVerifier().verify(base, tmp_path)
    assert verified.passed is True
    assert verified.verified_artifacts == 2

    (tmp_path / "checkpoint.bin").write_bytes(b"tampered")
    rejected = LocalAdapterTrainingBaseVerifier().verify(base, tmp_path)
    assert rejected.passed is False
    assert rejected.reason_codes == ("artifact_size_mismatch:checkpoint.bin",)


def test_agentic_profile_does_not_reference_base_repository() -> None:
    profiles = json.loads((ROOT / "config/models/tiny_action_model_profiles.v1.json").read_text(encoding="utf-8"))
    agentic = next(profile for profile in profiles["profiles"] if profile["profile_id"] == "lfm2.5-2.6b-agentic")
    assert agentic["source_url"] == "https://huggingface.co/LiquidAI/LFM2.5-2.6B"
