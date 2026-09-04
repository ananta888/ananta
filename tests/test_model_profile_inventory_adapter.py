from __future__ import annotations

from agent.services.model_profile_inventory_adapter import (
    ConfiguredProfileModelInventoryAdapter,
)
from agent.services.model_profile_loader import ModelProfile
from ananta_contracts.model_catalog import ModelAvailability, ModelMetadataEvidence


def test_descriptor_projects_evidence_aware_profile_without_promoting_it() -> None:
    profile = ModelProfile(
        profile_id="ornith-evaluation",
        provider_id="ollama",
        model="ornith-1.5:9b",
        enabled=False,
        aliases=["ornith-1.5-9b"],
        input_modalities=["text", "image"],
        output_modalities=["text"],
        capability_claims=[{
            "capability_id": "vision",
            "value": "supported",
            "evidence": "declared",
            "source_id": "hf:ornith-9b",
        }],
        nominal_context_tokens=262144,
        verified_context_tokens=None,
        hardware_class="rtx3080-10gb",
        artifact_sha256="a" * 64,
        release_state="experimental",
    )

    descriptor = ConfiguredProfileModelInventoryAdapter(lambda: "")._descriptor(
        profile, {}
    )

    assert descriptor.availability is ModelAvailability.UNAVAILABLE
    assert descriptor.aliases == ("ornith-1.5-9b",)
    assert descriptor.input_modalities == ("image", "text")
    vision = next(item for item in descriptor.capabilities if item.capability_id == "vision")
    assert vision.value == "supported"
    assert vision.evidence is ModelMetadataEvidence.DECLARED
    facts = {item.fact_id: item.value for item in descriptor.metadata_facts}
    assert facts["release_state"] == "experimental"
    assert facts["context.nominal_tokens"] == "262144"
    assert "context.verified_tokens" not in facts
