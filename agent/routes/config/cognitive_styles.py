"""Versioned Hub admin API for cognitive-style routing profiles."""

from __future__ import annotations

import os

from flask import Blueprint, current_app, g, request
from pydantic import ValidationError

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.benchmark_job_service import get_benchmark_job_service
from agent.services.cognitive_style_benchmark_service import (
    CognitiveStyleBenchmarkSuite,
)
from agent.services.cognitive_style_drift_service import CognitiveStyleDriftService
from agent.services.cognitive_style_evidence_service import (
    CognitiveStyleRetrospectiveService,
    ComplementaryStyleExperimentService,
)
from agent.services.cognitive_style_overlay_comparison_service import (
    CognitiveStyleOverlayComparisonService,
)
from agent.services.cognitive_style_service import (
    CognitiveStyleConflict,
    get_cognitive_style_service,
)
from agent.services.model_catalog_service import ModelCatalogCapabilityPolicy
from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from ananta_contracts.cognitive_style import (
    ComplementaryStyleExperimentCommand,
    CognitiveStyleMutationCommand,
    StyleBenchmarkRunCommand,
    StyleEvolutionFromEvidenceCommand,
    StyleEvolutionProposalCommand,
    StyleEvolutionTransitionMutationCommand,
    StyleMismatchRecordCommand,
    StyleOverlayComparisonCommand,
    StyleProfileDriftCommand,
    StyleRetrospectiveAnalysisCommand,
    TeamStyleDiversityCommand,
)

cognitive_styles_bp = Blueprint("config_cognitive_styles", __name__)

STYLE_READ_CAPABILITY = "cognitive_style.read"
STYLE_MUTATE_CAPABILITY = "cognitive_style.mutate"
STYLE_BENCHMARK_CAPABILITY = "cognitive_style.benchmark"


def _allowed(capability: str) -> bool:
    claims = {
        **dict(getattr(g, "auth_payload", {}) or {}),
        **dict(getattr(g, "user", {}) or {}),
    }
    return ModelCatalogCapabilityPolicy().allows(
        capability,
        is_admin=bool(getattr(g, "is_admin", False)),
        claims=claims,
    )


def _denied(capability: str):
    return api_response(
        status="error",
        message="cognitive_style_capability_required",
        data={"reason_code": "cognitive_style_capability_required", "capability": capability},
        code=403,
    )


def _profiles() -> tuple[ModelProfile, ...]:
    path = str(
        current_app.config.get("MODEL_PROFILES_PATH")
        or os.environ.get("MODEL_PROFILES_PATH")
        or ""
    ).strip()
    if not path:
        return ()
    loaded = ModelProfileLoader().load_file(path)
    return tuple(item for item in loaded.profiles if item.enabled)


def _actor() -> str:
    claims = dict(getattr(g, "user", {}) or {})
    return str(claims.get("sub") or claims.get("username") or "admin").strip()


@cognitive_styles_bp.route("/models/styles/v1", methods=["GET"])
@check_auth
def get_cognitive_styles():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    read = get_cognitive_style_service().read()
    return api_response(data=read.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1", methods=["PUT"])
@check_auth
def put_cognitive_styles():
    if not _allowed(STYLE_MUTATE_CAPABILITY):
        return _denied(STYLE_MUTATE_CAPABILITY)
    try:
        command = CognitiveStyleMutationCommand.model_validate(
            request.get_json(silent=True)
        )
        updated = get_cognitive_style_service().mutate(command)
    except ValidationError:
        return api_response(
            status="error", message="cognitive_style_mutation_invalid", code=400
        )
    except CognitiveStyleConflict as exc:
        return api_response(
            status="error", message=str(exc),
            data={"current_revision": exc.current_revision}, code=409,
        )
    log_audit("cognitive_style_configuration_updated", {
        "previous_revision": command.expected_revision,
        "revision": updated.revision,
        "profile_count": len(updated.profiles),
        "target_count": len(updated.role_targets),
        "overlay_count": len(updated.overlays),
    })
    return api_response(data=updated.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/benchmarks/plan", methods=["POST"])
@check_auth
def preview_cognitive_style_benchmark():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = StyleBenchmarkRunCommand.model_validate(request.get_json(silent=True))
        plan = CognitiveStyleBenchmarkSuite.plan(
            command.context,
            repeats=command.repeats,
            seeds=command.seeds,
            temperatures=command.temperatures,
        )
    except ValidationError:
        return api_response(
            status="error", message="cognitive_style_benchmark_command_invalid", code=400
        )
    return api_response(data=plan.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/benchmarks/run", methods=["POST"])
@check_auth
def run_cognitive_style_benchmark():
    if not _allowed(STYLE_BENCHMARK_CAPABILITY):
        return _denied(STYLE_BENCHMARK_CAPABILITY)
    try:
        command = StyleBenchmarkRunCommand.model_validate(request.get_json(silent=True))
    except ValidationError:
        return api_response(
            status="error", message="cognitive_style_benchmark_command_invalid", code=400
        )
    profile = next(
        (item for item in _profiles() if item.profile_id == command.context.model_profile_id),
        None,
    )
    if profile is None:
        return api_response(
            status="error", message="cognitive_style_model_profile_not_found", code=404
        )
    current_revision = get_cognitive_style_service().read().configuration.revision
    if command.expected_revision != current_revision:
        return api_response(
            status="error", message="cognitive_style_revision_conflict",
            data={"current_revision": current_revision}, code=409,
        )
    plan = CognitiveStyleBenchmarkSuite.plan(
        command.context,
        repeats=command.repeats,
        seeds=command.seeds,
        temperatures=command.temperatures,
    )
    job = get_benchmark_job_service().submit_cognitive_style_benchmark_job(
        profile=profile,
        plan=plan,
        expected_revision=command.expected_revision,
        created_by=_actor(),
    )
    from agent import metrics

    metrics.AGENT_STYLE_BENCHMARKS_TOTAL.labels(outcome="queued").inc()
    log_audit("cognitive_style_benchmark_queued", {
        "job_id": job["job_id"],
        "model_profile_id": profile.profile_id,
        "observation_count": len(plan.variants) * plan.repeats * len(plan.seeds) * len(plan.temperatures),
    })
    return api_response(data=job, code=202)


@cognitive_styles_bp.route("/models/styles/v1/benchmarks/jobs/<job_id>", methods=["GET"])
@check_auth
def get_cognitive_style_benchmark_job(job_id: str):
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    job = get_benchmark_job_service().get_job(job_id)
    if not job or job.get("job_type") != "cognitive_style_benchmark":
        return api_response(status="error", message="benchmark_job_not_found", code=404)
    return api_response(data=job)


@cognitive_styles_bp.route("/models/styles/v1/diversity", methods=["POST"])
@check_auth
def evaluate_cognitive_style_diversity():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = TeamStyleDiversityCommand.model_validate(request.get_json(silent=True))
    except ValidationError:
        return api_response(status="error", message="team_style_diversity_invalid", code=400)
    report = get_cognitive_style_service().diversity(command.members)
    return api_response(data=report.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/drift", methods=["POST"])
@check_auth
def evaluate_cognitive_style_drift():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = StyleProfileDriftCommand.model_validate(request.get_json(silent=True))
    except ValidationError:
        return api_response(status="error", message="style_profile_drift_invalid", code=400)
    profiles = get_cognitive_style_service().read().configuration.profiles
    report = CognitiveStyleDriftService().evaluate(
        profiles=profiles,
        contexts=command.contexts,
        stale_after_days=command.stale_after_days,
    )
    return api_response(data=report.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/overlays/compare", methods=["POST"])
@check_auth
def compare_cognitive_style_overlay():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = StyleOverlayComparisonCommand.model_validate(request.get_json(silent=True))
    except ValidationError:
        return api_response(status="error", message="style_overlay_comparison_invalid", code=400)
    configuration = get_cognitive_style_service().read().configuration
    report = CognitiveStyleOverlayComparisonService().compare(
        command=command,
        profiles=configuration.profiles,
        overlays=configuration.overlays,
    )
    return api_response(data=report.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/retrospectives/analyze", methods=["POST"])
@check_auth
def analyze_cognitive_style_retrospective():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = StyleRetrospectiveAnalysisCommand.model_validate(
            request.get_json(silent=True)
        )
    except ValidationError:
        return api_response(status="error", message="style_retrospective_invalid", code=400)
    report = CognitiveStyleRetrospectiveService().analyze(command.signals)
    from agent import metrics

    for hypothesis in report.hypotheses:
        metrics.AGENT_STYLE_MISMATCH_EVENTS_TOTAL.labels(signal=hypothesis.signal).inc()
    return api_response(data=report.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/experiments/evaluate", methods=["POST"])
@check_auth
def evaluate_complementary_style_experiment():
    if not _allowed(STYLE_READ_CAPABILITY):
        return _denied(STYLE_READ_CAPABILITY)
    try:
        command = ComplementaryStyleExperimentCommand.model_validate(
            request.get_json(silent=True)
        )
    except ValidationError:
        return api_response(status="error", message="style_experiment_invalid", code=400)
    report = ComplementaryStyleExperimentService().evaluate(command)
    from agent import metrics

    metrics.AGENT_STYLE_EXPERIMENTS_TOTAL.labels(outcome=report.outcome).inc()
    return api_response(data=report.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/mismatches", methods=["POST"])
@check_auth
def record_cognitive_style_mismatch():
    if not _allowed(STYLE_MUTATE_CAPABILITY):
        return _denied(STYLE_MUTATE_CAPABILITY)
    try:
        command = StyleMismatchRecordCommand.model_validate(request.get_json(silent=True))
        read = get_cognitive_style_service().record_mismatch(
            command.evidence, expected_revision=command.expected_revision
        )
    except ValidationError:
        return api_response(status="error", message="style_mismatch_evidence_invalid", code=400)
    except CognitiveStyleConflict as exc:
        return api_response(status="error", message=str(exc), data={"current_revision": exc.current_revision}, code=409)
    log_audit("cognitive_style_mismatch_recorded", {
        "evidence_id": command.evidence.evidence_id,
        "signal": command.evidence.signal,
        "causes_reclassification": False,
    })
    from agent import metrics

    metrics.AGENT_STYLE_MISMATCH_EVENTS_TOTAL.labels(
        signal=command.evidence.signal
    ).inc()
    return api_response(data=read.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/proposals", methods=["POST"])
@check_auth
def propose_cognitive_style_evolution():
    if not _allowed(STYLE_MUTATE_CAPABILITY):
        return _denied(STYLE_MUTATE_CAPABILITY)
    try:
        command = StyleEvolutionProposalCommand.model_validate(request.get_json(silent=True))
        read = get_cognitive_style_service().add_proposal(
            command.proposal, expected_revision=command.expected_revision
        )
    except ValidationError:
        return api_response(status="error", message="style_evolution_proposal_invalid", code=400)
    except (CognitiveStyleConflict, ValueError) as exc:
        code = 409 if isinstance(exc, CognitiveStyleConflict) else 400
        return api_response(status="error", message=str(exc), code=code)
    log_audit("cognitive_style_evolution_proposed", {
        "proposal_id": command.proposal.proposal_id,
        "proposal_type": command.proposal.proposal_type,
        "review_required": True,
    })
    from agent import metrics

    metrics.AGENT_STYLE_EVOLUTION_PROPOSALS_TOTAL.labels(
        proposal_type=command.proposal.proposal_type, status="proposed"
    ).inc()
    return api_response(data=read.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/proposals/from-evidence", methods=["POST"])
@check_auth
def propose_cognitive_style_evolution_from_evidence():
    if not _allowed(STYLE_MUTATE_CAPABILITY):
        return _denied(STYLE_MUTATE_CAPABILITY)
    try:
        command = StyleEvolutionFromEvidenceCommand.model_validate(
            request.get_json(silent=True)
        )
        service = get_cognitive_style_service()
        current = service.read()
        evidence = next(
            (item for item in current.mismatch_evidence if item.evidence_id == command.evidence_id),
            None,
        )
        if evidence is None:
            return api_response(status="error", message="style_mismatch_evidence_not_found", code=404)
        proposal = CognitiveStyleRetrospectiveService.proposal_from_evidence(
            evidence,
            proposal_id=command.proposal_id,
            proposal_type=command.proposal_type,
            experiment_id=command.experiment_id,
            sprint_id=command.sprint_id,
        )
        read = service.add_proposal(proposal, expected_revision=command.expected_revision)
    except ValidationError:
        return api_response(status="error", message="style_evolution_from_evidence_invalid", code=400)
    except (CognitiveStyleConflict, ValueError) as exc:
        code = 409 if isinstance(exc, CognitiveStyleConflict) else 400
        return api_response(status="error", message=str(exc), code=code)
    log_audit("cognitive_style_evolution_proposed", {
        "proposal_id": proposal.proposal_id,
        "proposal_type": proposal.proposal_type,
        "evidence_id": command.evidence_id,
        "review_required": True,
    })
    from agent import metrics

    metrics.AGENT_STYLE_EVOLUTION_PROPOSALS_TOTAL.labels(
        proposal_type=proposal.proposal_type, status="proposed"
    ).inc()
    return api_response(data=read.model_dump(mode="json", by_alias=True))


@cognitive_styles_bp.route("/models/styles/v1/proposals/<proposal_id>/transition", methods=["POST"])
@check_auth
def transition_cognitive_style_evolution(proposal_id: str):
    if not _allowed(STYLE_MUTATE_CAPABILITY):
        return _denied(STYLE_MUTATE_CAPABILITY)
    try:
        command = StyleEvolutionTransitionMutationCommand.model_validate(
            request.get_json(silent=True)
        )
        read = get_cognitive_style_service().transition_proposal(
            proposal_id,
            command.transition,
            expected_revision=command.expected_revision,
        )
    except ValidationError:
        return api_response(status="error", message="style_evolution_transition_invalid", code=400)
    except (CognitiveStyleConflict, ValueError) as exc:
        code = 409 if isinstance(exc, CognitiveStyleConflict) else 400
        return api_response(status="error", message=str(exc), code=code)
    log_audit("cognitive_style_evolution_transitioned", {
        "proposal_id": proposal_id,
        "target_status": command.transition.target_status,
        "review_reference_present": bool(command.transition.review_reference),
    })
    from agent import metrics

    proposal = next(
        item for item in read.evolution_proposals if item.proposal_id == proposal_id
    )
    metrics.AGENT_STYLE_EVOLUTION_PROPOSALS_TOTAL.labels(
        proposal_type=proposal.proposal_type, status=command.transition.target_status
    ).inc()
    return api_response(data=read.model_dump(mode="json", by_alias=True))


__all__ = ["cognitive_styles_bp"]
