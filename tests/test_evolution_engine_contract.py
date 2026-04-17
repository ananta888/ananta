import pytest

from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionResult,
    UnsupportedEvolutionOperation,
    ValidationResult,
)


class AnalysisOnlyEngine(EvolutionEngine):
    provider_name = "analysis-only"
    capabilities = [EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(
            provider_name=self.provider_name,
            summary=f"Analyzed {context.objective}",
            proposals=[
                EvolutionProposal(
                    title="Improve queue visibility",
                    description="Expose queue state in a provider-neutral proposal.",
                    provider_metadata={"vendor_score": 0.8},
                    raw_payload={"vendor_field": "kept_additively"},
                )
            ],
        )


class ValidationCapableEngine(EvolutionEngine):
    provider_name = "validation-capable"
    capabilities = ["analyze", "validate"]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(provider_name=self.provider_name, summary=context.objective)

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        return ValidationResult(proposal_id=proposal.proposal_id, status="passed", valid=True)


def test_multiple_providers_share_core_evolution_models():
    context = EvolutionContext(objective="Improve hub task flow", task_id="T-1")
    engines = [AnalysisOnlyEngine(), ValidationCapableEngine()]

    results = [engine.analyze(context) for engine in engines]

    assert [result.provider_name for result in results] == ["analysis-only", "validation-capable"]
    assert all(isinstance(result, EvolutionResult) for result in results)
    assert results[0].proposals[0].provider_metadata == {"vendor_score": 0.8}
    assert results[0].proposals[0].raw_payload == {"vendor_field": "kept_additively"}


def test_provider_descriptor_exposes_machine_readable_capabilities():
    descriptor = ValidationCapableEngine().describe()

    assert descriptor.provider_name == "validation-capable"
    assert descriptor.status == "available"
    assert descriptor.capabilities == [EvolutionCapability.ANALYZE, EvolutionCapability.VALIDATE]


def test_unsupported_operations_fail_closed_with_reproducible_error():
    engine = AnalysisOnlyEngine()
    context = EvolutionContext(objective="Validate proposal")
    proposal = EvolutionProposal(title="Proposal", description="Needs validation")

    with pytest.raises(UnsupportedEvolutionOperation) as exc:
        engine.validate(context, proposal)

    assert exc.value.provider_name == "analysis-only"
    assert exc.value.operation == "validate"
    assert str(exc.value) == "evolution_operation_not_supported:analysis-only:validate"


def test_core_models_do_not_embed_provider_specific_domain_terms():
    forbidden = {"gene", "genes", "capsule", "capsules", "gep"}
    model_names = [
        EvolutionContext,
        EvolutionProposal,
        EvolutionResult,
        ValidationResult,
    ]

    field_names = {field_name.lower() for model in model_names for field_name in model.model_fields}

    assert forbidden.isdisjoint(field_names)
