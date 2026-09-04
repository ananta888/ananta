from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananta_contracts.model_identity import AbliteratedModelIdentity

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_identities_are_unique_closed_and_safety_modified() -> None:
    catalog = json.loads((ROOT / "config/models/qwen3.8-27b-abliterated.v1.json").read_text())
    identities = [AbliteratedModelIdentity.model_validate(item) for item in catalog["identities"]]

    assert len({item.identity_id for item in identities}) == len(identities)
    assert len({item.binding_digest() for item in identities}) == len(identities)
    assert all(item.trust_class == "unsafe_research" and item.safety_modified for item in identities)
    assert catalog["normal_routing_allowed"] is False


def test_unknown_line_cannot_claim_layers_and_known_line_requires_layers() -> None:
    base = {
        "schema": "ananta.abliterated-model-identity.v1",
        "identity_id": "test",
        "base_repository": "Qwen/Qwen3.8-27B",
        "base_revision": "a" * 40,
        "derivative_repository": "test/model",
        "derivative_revision": "b" * 40,
        "artifact_sha256": "c" * 64,
        "quantization": "q2_k",
        "runtime_family": "gguf",
        "trust_class": "unsafe_research",
        "safety_modified": True,
    }
    with pytest.raises(ValueError, match="unknown_ablation_layers_must_be_empty"):
        AbliteratedModelIdentity.model_validate({**base, "ablation_line": "unknown", "ablated_layer_start": 1})
    with pytest.raises(ValueError, match="known_ablation_layers_required"):
        AbliteratedModelIdentity.model_validate({**base, "ablation_line": "ud"})
