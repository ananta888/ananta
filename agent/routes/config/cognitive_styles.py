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
from agent.services.cognitive_style_service import (
    CognitiveStyleConflict,
    get_cognitive_style_service,
)
from agent.services.model_catalog_service import ModelCatalogCapabilityPolicy
from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from ananta_contracts.cognitive_style import (
    CognitiveStyleMutationCommand,
    StyleBenchmarkRunCommand,
    StyleEvolutionProposalCommand,
    StyleEvolutionTransitionMutationCommand,
    StyleMismatchRecordCommand,
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
    return api_response(data=read.model_dump(mode="json", by_alias=True))


__all__ = ["cognitive_styles_bp"]
