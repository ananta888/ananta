from __future__ import annotations

from flask import Blueprint, request

from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.citation_bundle_service import GoalCitationBundleService
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.auth import check_auth
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.services.repository_registry import get_repository_registry

goal_artifacts_bp = Blueprint("goal_artifacts", __name__)


def _service() -> GoalArtifactService:
    return GoalArtifactService()


def _candidate_service() -> ArtifactCandidateService:
    return ArtifactCandidateService(goal_artifact_service=_service())


def _citation_bundle_service() -> GoalCitationBundleService:
    return GoalCitationBundleService(goal_artifact_service=_service())


def _goal_exists(goal_id: str) -> bool:
    goal = get_repository_registry().goal_repo.get_by_id(goal_id)
    return goal is not None


def _require_goal(goal_id: str) -> None:
    normalized = str(goal_id or "").strip()
    if not normalized:
        raise BadRequestError("goal_id_required")
    if not _goal_exists(normalized):
        raise NotFoundError("goal_not_found")


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/graph", methods=["GET"])
@check_auth
def get_goal_artifact_graph(goal_id: str):
    _require_goal(goal_id)
    return api_response(data=_service().get_goal_graph(goal_id))


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/sources", methods=["GET"])
@check_auth
def get_goal_artifact_sources(goal_id: str):
    _require_goal(goal_id)
    graph = _service().get_goal_graph(goal_id)
    return api_response(
        data={
            "goal_id": goal_id,
            "source_grants": list(graph.get("source_grants") or []),
            "source_usages": list(graph.get("source_usages") or []),
        }
    )


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/outputs", methods=["GET"])
@check_auth
def get_goal_artifact_outputs(goal_id: str):
    _require_goal(goal_id)
    graph = _service().get_goal_graph(goal_id)
    return api_response(data={"goal_id": goal_id, "output_artifacts": list(graph.get("output_artifacts") or [])})


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/sources/grant", methods=["POST"])
@check_auth
def create_goal_source_grant(goal_id: str):
    _require_goal(goal_id)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    try:
        created = _service().create_grant(goal_id=goal_id, grant=payload)
    except GoalArtifactServiceError as exc:
        if exc.reason_code == "grant_conflict":
            raise ConflictError("grant_conflict") from exc
        raise BadRequestError(exc.reason_code) from exc
    return api_response(data=created, code=201)


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/sources/<grant_id>/revoke", methods=["POST"])
@check_auth
def revoke_goal_source_grant(goal_id: str, grant_id: str):
    _require_goal(goal_id)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        revoked = _service().revoke_grant(
            goal_id=goal_id,
            grant_id=grant_id,
            revoked_at=str(payload.get("revoked_at") or "").strip() or None,
            revoke_reason=str(payload.get("revoke_reason") or "").strip(),
        )
    except GoalArtifactServiceError as exc:
        if exc.reason_code == "grant_not_found":
            raise NotFoundError("grant_not_found") from exc
        raise BadRequestError(exc.reason_code) from exc
    return api_response(data=revoked)


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/source-candidates", methods=["GET"])
@check_auth
def list_goal_source_candidates(goal_id: str):
    _require_goal(goal_id)
    data = _candidate_service().list_candidates(
        goal_id=goal_id,
        artifact_type=request.args.get("artifact_type", type=str),
        sensitivity=request.args.get("sensitivity", type=str),
        source_id=request.args.get("source_id", type=str),
    )
    return api_response(data={"goal_id": goal_id, "candidates": data})


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/citations", methods=["GET"])
@check_auth
def get_goal_citation_bundle(goal_id: str):
    _require_goal(goal_id)
    bundle = _citation_bundle_service().build_bundle(goal_id=goal_id)
    return api_response(data=bundle)
