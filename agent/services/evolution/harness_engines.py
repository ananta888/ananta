"""Proposal-only Native/DSPy adapters, sharing the existing EvolutionEngine SPI."""

from __future__ import annotations

from agent.services.evolution.engine import EvolutionEngine
from agent.services.evolution.models import (
    EvolutionCapability,
    EvolutionProposal,
    EvolutionResult,
)
from agent.services.harness_mutation import MutationEngine
from ananta_contracts.dspy_optimization import OptimizationSpecV1
from ananta_contracts.harness_genome import HarnessGenome


class NativeHarnessEvolutionEngine(EvolutionEngine):
    provider_name = "native_harness"
    version = "ananta-native-harness.v1"
    capabilities = (EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE)

    def analyze(self, context):
        binding = context.constraints.get("native_harness") or {}
        genome = HarnessGenome.from_mapping(binding["baseline"])
        operator = str(binding["operator"])
        parameters = dict(binding.get("parameters") or {})
        mutation = MutationEngine().mutate(
            genome, operator_id=operator, params=parameters, rationale="Hub-scoped native hypothesis"
        )
        proposal = EvolutionProposal(
            title=f"Native Harness: {operator}",
            description=mutation.rationale,
            proposal_type="policy",
            requires_review=True,
            provider_metadata={
                "mutation": mutation.to_mapping(),
                "candidate_genome": mutation.child_genome.to_mapping(),
                "grants_permission": False,
            },
        )
        return EvolutionResult(provider_name=self.provider_name, proposals=[proposal], summary="Native policy proposal")


class DspyEvolutionProposalEngine(EvolutionEngine):
    provider_name = "dspy_optimization"
    version = "ananta-dspy-proposal.v1"
    capabilities = (EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE)

    def analyze(self, context):
        spec = OptimizationSpecV1.from_mapping(context.constraints["dspy_spec"])
        proposal = EvolutionProposal(
            title=f"DSPy: {spec.optimizer_id}",
            description="Evaluate existing DSPy program through the Hub task lifecycle.",
            proposal_type="prompt",
            requires_review=True,
            provider_metadata={
                "optimization_spec": spec.to_dict(),
                "spec_digest": spec.digest,
                "execution": "existing_dspy_worker",
                "grants_permission": False,
            },
        )
        return EvolutionResult(
            provider_name=self.provider_name, proposals=[proposal], summary="DSPy optimization proposal"
        )
