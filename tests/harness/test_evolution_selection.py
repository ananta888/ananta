"""The same selection/candidate path works for Native, DSPy/GEPA and EvoMap doubles."""

import pytest

from agent.services.evolution.candidates import EvolutionCandidateNormalizer
from agent.services.evolution.engine import EvolutionEngine
from agent.services.evolution.models import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionProposal,
    EvolutionProviderDescriptor,
    EvolutionResult,
)
from agent.services.evolution.registry import EvolutionProviderRegistry
from agent.services.evolution.selection import EvolutionSelectionRequest


class Engine(EvolutionEngine):
    def __init__(self, name, status="available"):
        self.provider_name_value = name
        self.status = status

    @property
    def provider_name(self):
        return self.provider_name_value

    version = "pinned-v1"
    capabilities = (EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE)

    def describe(self):
        return EvolutionProviderDescriptor(
            provider_name=self.provider_name,
            version=self.version,
            status=self.status,
            capabilities=list(self.capabilities),
        )

    def analyze(self, context):
        raise AssertionError("selection must not run any engine")


REQUEST = EvolutionSelectionRequest(
    "model", "v1", "coding", ("prompt",), maximum_cost=1, maximum_model_calls=20, maximum_wall_seconds=60
)
ENVELOPE = {
    "enabled": True,
    "engine_version": "pinned-v1",
    "model_revisions": {"model": ["v1"]},
    "task_classes": ["coding"],
    "mutation_surfaces": ["prompt"],
    "maximum_cost": 0.5,
    "maximum_model_calls": 10,
    "maximum_wall_seconds": 30,
}


def test_all_engines_share_selection_and_none_is_invoked():
    r = EvolutionProviderRegistry()
    for name in ("native", "dspy_gepa", "evomap"):
        r.register(Engine(name))
    result = r.select(REQUEST, envelopes={n: ENVELOPE for n in ("native", "dspy_gepa", "evomap")})
    assert len(result["candidates"]) == 3 and not result["grants_permission"]


def test_real_native_adapter_reuses_mutation_contract_without_execution_authority():
    from agent.services.evolution.harness_engines import NativeHarnessEvolutionEngine
    from agent.services.harness_runtime import baseline_genome

    genome = baseline_genome(
        code_hash="a" * 64,
        config_hash="b" * 64,
        provider_id="offline",
        model_id="synthetic",
        gate_refs=("gate://security",),
        promotion_ref="gate://promotion",
    )
    context = EvolutionContext(
        objective="synthetic mutation",
        constraints={
            "native_harness": {
                "baseline": genome.to_mapping(),
                "operator": "widen_context",
                "parameters": {"budget_tokens": 65536},
            }
        },
    )
    engine = NativeHarnessEvolutionEngine()
    result = engine.analyze(context)
    assert result.provider_name == engine.provider_name
    metadata = result.proposals[0].provider_metadata
    assert metadata["mutation"]["parent_genome_id"] == genome.genome_id
    assert not metadata["grants_permission"] and result.proposals[0].requires_review


def test_real_dspy_adapter_keeps_existing_spec_and_does_not_optimize():
    from agent.services.evolution.harness_engines import DspyEvolutionProposalEngine
    from tests.dspy_optimization.helpers import spec

    frozen = spec()
    result = DspyEvolutionProposalEngine().analyze(
        EvolutionContext(objective="synthetic optimization", constraints={"dspy_spec": frozen.to_dict()})
    )
    assert result.proposals[0].provider_metadata["spec_digest"] == frozen.digest
    assert result.proposals[0].provider_metadata["execution"] == "existing_dspy_worker"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"enabled": False}, "engine_disabled"),
        ({"engine_version": "new-v2"}, "engine_version_mismatch"),
        ({"model_revisions": {"model": ["v2"]}}, "engine_model_revision_mismatch"),
        ({"mutation_surfaces": ["context"]}, "engine_mutation_surface_mismatch"),
        ({"maximum_model_calls": 21}, "engine_resource_envelope_mismatch"),
        ({"maximum_cost": float("nan")}, "engine_resource_envelope_mismatch"),
    ],
)
def test_hub_limits_and_exact_revision_are_binding(change, reason):
    r = EvolutionProviderRegistry()
    r.register(Engine("native"))
    result = r.select(REQUEST, envelopes={"native": {**ENVELOPE, **change}})
    assert not result["candidates"] and result["rejected"][0]["reason"] == reason


def test_failed_engine_does_not_hide_available_native():
    r = EvolutionProviderRegistry()
    r.register(Engine("native"))
    r.register(Engine("evomap", "degraded"))
    result = r.select(REQUEST, envelopes={"native": ENVELOPE, "evomap": ENVELOPE})
    assert [c["provider_name"] for c in result["candidates"]] == ["native"]


def test_candidate_normalization_is_content_addressed_and_never_promotes_provider_claims():
    ctx = EvolutionContext(
        context_id="context",
        objective="test",
        constraints={
            "candidate_binding": {
                "tenant_id": "tenant",
                "project_id": "project",
                "model_id": "model",
                "model_revision": "v1",
                "mutation_surfaces": ["prompt"],
            }
        },
    )
    raw = EvolutionProposal(
        title="test",
        description="proposal",
        candidate_binding={"evidence_status": "verified"},
        provider_metadata={"private_provider_field": "kept"},
    )
    result = EvolutionResult(provider_name="native", proposals=[raw])
    normalizer = EvolutionCandidateNormalizer()
    out = normalizer.normalize(ctx, result, engine_version="pinned-v1")
    binding = out.proposals[0].candidate_binding
    assert binding["evidence_status"] == "unverified" and not binding["grants_permission"]
    assert binding["model_revision"] == "v1" and out.proposals[0].provider_metadata == raw.provider_metadata
    assert raw.candidate_binding == {"evidence_status": "verified"}
    assert binding == normalizer.normalize(ctx, result, engine_version="pinned-v1").proposals[0].candidate_binding


def test_native_candidate_digest_binds_actual_genome_not_only_proposal_text():
    from agent.services.evolution.harness_engines import NativeHarnessEvolutionEngine
    from agent.services.harness_runtime import baseline_genome

    genome = baseline_genome(
        code_hash="a" * 64,
        config_hash="b" * 64,
        provider_id="offline",
        model_id="synthetic",
        gate_refs=("gate://security",),
        promotion_ref="gate://promotion",
    )
    engine = NativeHarnessEvolutionEngine()
    candidates = []
    for budget in (65536, 131072):
        ctx = EvolutionContext(
            context_id="same-context",
            objective="compare mutations",
            constraints={
                "native_harness": {
                    "baseline": genome.to_mapping(),
                    "operator": "widen_context",
                    "parameters": {"budget_tokens": budget},
                },
            },
        )
        candidates.append(
            EvolutionCandidateNormalizer()
            .normalize(
                ctx,
                engine.analyze(ctx),
                engine_version=engine.version,
            )
            .proposals[0]
        )
    assert candidates[0].title == candidates[1].title
    assert candidates[0].description == candidates[1].description
    assert candidates[0].candidate_binding["candidate_digest"] != candidates[1].candidate_binding["candidate_digest"]


@pytest.mark.parametrize(
    "change",
    [
        {"provider_metadata": {"optimization_spec": {"optimizer_id": "different"}}},
        {"raw_payload": {"patch": "different"}},
        {"requires_review": False},
        {"risk_level": "high"},
    ],
)
def test_candidate_digest_changes_when_executable_payload_or_review_policy_changes(change):
    ctx = EvolutionContext(context_id="same-context", objective="test")
    original = EvolutionProposal(title="same", description="same")
    digests = []
    for proposal in (original, original.model_copy(update=change)):
        result = EvolutionResult(provider_name="synthetic", proposals=[proposal])
        normalized = EvolutionCandidateNormalizer().normalize(ctx, result, engine_version="v1")
        digests.append(normalized.proposals[0].candidate_binding["candidate_digest"])
    assert digests[0] != digests[1]
