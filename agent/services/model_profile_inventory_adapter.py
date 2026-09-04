"""Configured technical-profile source adapter for the model inventory."""

from __future__ import annotations

from collections.abc import Callable

from agent.services.model_inventory_service import ModelInventorySnapshot
from agent.services.model_profile_loader import ModelProfileLoader
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelMetadataFact,
    ModelRuntime,
    ModelSourceKind,
)
from ananta_contracts.model_selection import ModelRoutingConfiguration


class ConfiguredProfileModelInventoryAdapter:
    source_id = "profiles.configured"
    source_kind = ModelSourceKind.CONFIGURED
    cache_ttl_seconds = 30.0
    stale_after_seconds = 300.0

    def __init__(
        self,
        path_loader: Callable[[], str],
        routing_loader: Callable[[], ModelRoutingConfiguration] | None = None,
    ) -> None:
        self._path_loader = path_loader
        self._routing_loader = routing_loader

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        path = str(self._path_loader() or "").strip()
        if not path:
            return ModelInventorySnapshot(models=())
        loaded = ModelProfileLoader().load_file(path)
        if not loaded.ok:
            raise ValueError("configured_model_profiles_invalid")
        consumers_by_profile: dict[str, set[str]] = {}
        if self._routing_loader is not None:
            routing = self._routing_loader()
            for assignment in routing.assignments:
                if assignment.profile_id:
                    consumers_by_profile.setdefault(
                        assignment.profile_id, set()
                    ).add(assignment.consumer_id)
        return ModelInventorySnapshot(models=tuple(
            self._descriptor(profile, consumers_by_profile)
            for profile in loaded.profiles
        ))

    def _descriptor(self, profile, consumers_by_profile) -> ModelInventoryDescriptor:
        claims_by_id = {item.capability_id: item for item in (
            ModelCapabilityClaim(
                capability_id="tools",
                value="supported" if (
                    profile.supports_tools or profile.supports_prompt_json_tools()
                ) else "unsupported",
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelCapabilityClaim(
                capability_id="json",
                value="supported" if profile.supports_json else "unsupported",
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelCapabilityClaim(
                capability_id="streaming",
                value="supported" if profile.supports_streaming else "unsupported",
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
        )}
        for raw in profile.capability_claims:
            claim = ModelCapabilityClaim.model_validate(raw)
            claims_by_id[claim.capability_id] = claim
        claims = tuple(claims_by_id[key] for key in sorted(claims_by_id))
        runtime = ModelRuntime.CLOUD if profile.is_cloud() else ModelRuntime.LOCAL
        unavailable = not profile.enabled or profile.release_state in {"unavailable", "disabled"}
        facts = [
            ModelMetadataFact(
                fact_id="model_role", value=profile.model_role,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelMetadataFact(
                fact_id="quality_class", value=profile.quality_class,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelMetadataFact(
                fact_id="cost_class", value=profile.cost_class,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelMetadataFact(
                fact_id="release_state", value=profile.release_state,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelMetadataFact(
                fact_id="trust_class", value=profile.trust_class,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
            ModelMetadataFact(
                fact_id="safety_modified", value=str(profile.safety_modified).lower(),
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ),
        ]
        for fact_id, value in (
            ("hardware_class", profile.hardware_class),
            ("artifact.sha256", profile.artifact_sha256),
            ("context.nominal_tokens", profile.nominal_context_tokens),
            ("context.verified_tokens", profile.verified_context_tokens),
        ):
            if value is not None:
                facts.append(ModelMetadataFact(
                    fact_id=fact_id,
                    value=str(value),
                    evidence=(
                        ModelMetadataEvidence.BENCHMARK
                        if fact_id == "context.verified_tokens"
                        else ModelMetadataEvidence.DECLARED
                    ),
                    source_id=self.source_id,
                ))
        return ModelInventoryDescriptor(
            provider_id=profile.provider_id,
            model_id=profile.model,
            executor_id=f"api:{profile.provider_id}",
            display_name=profile.model,
            runtime=runtime,
            source_ids=(self.source_id,),
            source_kinds=(self.source_kind,),
            profile_ids=(profile.profile_id,),
            aliases=tuple(profile.aliases),
            availability=(ModelAvailability.UNAVAILABLE if unavailable else ModelAvailability.UNKNOWN),
            health=(ModelHealth.UNAVAILABLE if unavailable else ModelHealth.UNKNOWN),
            configured=True,
            listing_supported=False,
            context_window=profile.context_tokens,
            quantization=(
                str(profile.extra.get("quantization") or "").strip() or None
            ),
            input_modalities=tuple(profile.input_modalities),
            output_modalities=tuple(profile.output_modalities),
            price_input_per_million=profile.price_input_per_million,
            price_output_per_million=profile.price_output_per_million,
            capabilities=claims,
            metadata_facts=tuple(facts),
            used_by_consumers=tuple(sorted(
                consumers_by_profile.get(profile.profile_id, set())
            )),
        )


__all__ = ["ConfiguredProfileModelInventoryAdapter"]
