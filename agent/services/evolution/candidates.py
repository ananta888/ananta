"""Hub normalization of existing EvolutionProposal; no parallel learning lifecycle."""

from __future__ import annotations

from agent.services.evolution.models import EvolutionContext, EvolutionResult
from ananta_contracts.harness_genome import content_digest


class EvolutionCandidateNormalizer:
    def normalize(self, context: EvolutionContext, result: EvolutionResult, *, engine_version: str) -> EvolutionResult:
        binding = context.constraints.get("candidate_binding") or {}
        if not isinstance(binding, dict):
            raise ValueError("evolution_candidate_binding_invalid")
        known = {
            "tenant_id",
            "project_id",
            "model_id",
            "model_revision",
            "dataset_digest",
            "benchmark_suite_digest",
            "mutation_surfaces",
            "parents",
        }
        if set(binding) - known:
            raise ValueError("evolution_candidate_binding_unknown_field")
        for key in ("parents", "mutation_surfaces"):
            values = binding.get(key, [])
            if (
                not isinstance(values, list)
                or len(values) > 64
                or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in values)
            ):
                raise ValueError("evolution_candidate_binding_invalid")
        scope = {
            key: str(binding.get(key) or "")
            for key in (
                "tenant_id",
                "project_id",
                "model_id",
                "model_revision",
                "dataset_digest",
                "benchmark_suite_digest",
            )
        }
        proposals = []
        for proposal in result.proposals:
            normalized = {
                "schema": "ananta.evolution-candidate.v1",
                "provider_name": result.provider_name,
                "engine_version": engine_version,
                **scope,
                "context_id": context.context_id,
                "parents": list(binding.get("parents") or []),
                "mutation_surfaces": list(binding.get("mutation_surfaces") or []),
                "classification": "proposal",
                "evidence_status": "unverified",
                "grants_permission": False,
            }
            normalized["candidate_digest"] = content_digest(
                {
                    "binding": normalized,
                    "proposal": {
                        "title": proposal.title,
                        "description": proposal.description,
                        "proposal_type": proposal.proposal_type,
                        "target_refs": proposal.target_refs,
                        "rationale": proposal.rationale,
                        "provider_metadata": proposal.provider_metadata,
                        "raw_payload": proposal.raw_payload,
                        "risk_level": proposal.risk_level,
                        "requires_review": proposal.requires_review,
                    },
                }
            )
            proposals.append(proposal.model_copy(deep=True, update={"candidate_binding": normalized}))
        return result.model_copy(deep=True, update={"proposals": proposals})
