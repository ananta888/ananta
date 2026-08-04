from __future__ import annotations

from copy import deepcopy

from flask import Blueprint, request

from agent.artifacts.artifact_candidate_service import ArtifactCandidateService
from agent.artifacts.citation_bundle_service import GoalCitationBundleService
from agent.artifacts.goal_artifact_service import GoalArtifactService, GoalArtifactServiceError
from agent.auth import check_auth
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
    repository_artifact_reference_candidates,
)
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


def _artifact_ref_is_visible(artifact_ref: object) -> bool:
    artifact_repo = get_repository_registry().artifact_repo
    for artifact_id in repository_artifact_reference_candidates(artifact_ref):
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is not None:
            return is_artifact_visible_on_generic_surfaces(artifact)
    return True


def _public_goal_graph(graph: dict) -> dict:
    """Project the persisted graph onto the generic client-visible surface."""

    public_graph = deepcopy(dict(graph or {}))
    grants = [
        dict(row)
        for row in list(public_graph.get("source_grants") or [])
        if isinstance(row, dict) and _artifact_ref_is_visible(row.get("artifact_ref"))
    ]
    visible_grant_ids = {
        str(row.get("grant_id") or "") for row in grants if row.get("grant_id")
    }
    all_grant_ids = {
        str(row.get("grant_id") or "")
        for row in list(public_graph.get("source_grants") or [])
        if isinstance(row, dict) and row.get("grant_id")
    }
    hidden_grant_ids = all_grant_ids - visible_grant_ids

    usages = [
        dict(row)
        for row in list(public_graph.get("source_usages") or [])
        if isinstance(row, dict)
        and str(row.get("grant_id") or "") not in hidden_grant_ids
        and _artifact_ref_is_visible(row.get("artifact_ref"))
    ]
    visible_usage_ids = {
        str(row.get("usage_id") or "") for row in usages if row.get("usage_id")
    }
    all_usage_ids = {
        str(row.get("usage_id") or "")
        for row in list(public_graph.get("source_usages") or [])
        if isinstance(row, dict) and row.get("usage_id")
    }
    hidden_usage_ids = all_usage_ids - visible_usage_ids

    outputs: list[dict] = []
    for row in list(public_graph.get("output_artifacts") or []):
        if not isinstance(row, dict) or not _artifact_ref_is_visible(
            row.get("artifact_ref")
        ):
            continue
        output = dict(row)
        output["input_usage_refs"] = [
            ref
            for ref in list(output.get("input_usage_refs") or [])
            if str(ref) not in hidden_usage_ids
        ]
        outputs.append(output)
    visible_output_ids = {
        str(row.get("output_artifact_id") or "")
        for row in outputs
        if row.get("output_artifact_id")
    }
    all_output_ids = {
        str(row.get("output_artifact_id") or "")
        for row in list(public_graph.get("output_artifacts") or [])
        if isinstance(row, dict) and row.get("output_artifact_id")
    }
    hidden_output_ids = all_output_ids - visible_output_ids

    hidden_node_refs = {
        *(f"grant:{item}" for item in hidden_grant_ids),
        *(f"usage:{item}" for item in hidden_usage_ids),
        *(f"output:{item}" for item in hidden_output_ids),
    }
    public_graph["source_grants"] = grants
    public_graph["source_usages"] = usages
    public_graph["output_artifacts"] = outputs
    public_graph["edges"] = [
        dict(edge)
        for edge in list(public_graph.get("edges") or [])
        if isinstance(edge, dict)
        and str(edge.get("from_ref") or "") not in hidden_node_refs
        and str(edge.get("to_ref") or "") not in hidden_node_refs
    ]

    extensions = dict(public_graph.get("extensions") or {})
    provenances: list[dict] = []
    for row in list(extensions.get("execution_provenance") or []):
        if not isinstance(row, dict):
            continue
        provenance = dict(row)
        original_output_refs = list(provenance.get("output_artifact_refs") or [])
        provenance["input_usage_refs"] = [
            ref
            for ref in list(provenance.get("input_usage_refs") or [])
            if str(ref) not in hidden_usage_ids
        ]
        provenance["output_artifact_refs"] = [
            ref
            for ref in original_output_refs
            if str(ref) not in hidden_output_ids
        ]
        if original_output_refs and not provenance["output_artifact_refs"]:
            continue
        provenances.append(provenance)
    if "execution_provenance" in extensions:
        extensions["execution_provenance"] = provenances
    public_graph["extensions"] = extensions
    return public_graph


def _public_execution_provenance(graph: dict, provenance_id: str) -> dict | None:
    extensions = dict(_public_goal_graph(graph).get("extensions") or {})
    for item in list(extensions.get("execution_provenance") or []):
        if isinstance(item, dict) and str(item.get("provenance_id") or "") == str(
            provenance_id
        ):
            return dict(item)
    return None


def _redact_provenance(payload: dict, *, include_raw_prompt: bool) -> dict:
    data = dict(payload or {})
    prompt_refs = dict(data.get("prompt_refs") or {})
    config_refs = dict(data.get("config_refs") or {})
    data["config_refs"] = {
        "worker_config_ref": str(config_refs.get("worker_config_ref") or ""),
        "runtime_config_ref": str(config_refs.get("runtime_config_ref") or ""),
        "model_config_ref": str(config_refs.get("model_config_ref") or ""),
        "policy_config_ref": str(config_refs.get("policy_config_ref") or ""),
    }
    if include_raw_prompt:
        data["prompt_access"] = {
            "raw_prompt": "policy_blocked",
            "reason_code": "raw_prompt_access_blocked",
        }
    prompt_refs.pop("raw_prompt_stored", None)
    data["prompt_refs"] = prompt_refs
    return data


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/graph", methods=["GET"])
@check_auth
def get_goal_artifact_graph(goal_id: str):
    _require_goal(goal_id)
    return api_response(data=_public_goal_graph(_service().get_goal_graph(goal_id)))


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/sources", methods=["GET"])
@check_auth
def get_goal_artifact_sources(goal_id: str):
    _require_goal(goal_id)
    graph = _public_goal_graph(_service().get_goal_graph(goal_id))
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
    graph = _public_goal_graph(_service().get_goal_graph(goal_id))
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
    graph = _public_goal_graph(_service().get_goal_graph(goal_id))
    bundle = _citation_bundle_service().build_bundle(goal_id=goal_id, graph=graph)
    return api_response(data=bundle)


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/outputs/<output_id>/provenance", methods=["GET"])
@check_auth
def get_goal_output_provenance(goal_id: str, output_id: str):
    _require_goal(goal_id)
    graph = _public_goal_graph(_service().get_goal_graph(goal_id))
    output = next((row for row in list(graph.get("output_artifacts") or []) if str(row.get("output_artifact_id") or "") == str(output_id)), None)
    if output is None:
        raise NotFoundError("output_artifact_not_found")
    provenance_id = str(output.get("provenance_id") or "").strip()
    if not provenance_id:
        raise NotFoundError("provenance_not_found")
    provenance = _public_execution_provenance(graph, provenance_id)
    if provenance is None:
        raise NotFoundError("provenance_not_found")
    include_raw_prompt = str(request.args.get("include_raw_prompt", "")).strip().lower() in {"1", "true", "yes", "on"}
    return api_response(data=_redact_provenance(provenance, include_raw_prompt=include_raw_prompt))


@goal_artifacts_bp.route("/goals/<goal_id>/artifacts/executions/<provenance_id>", methods=["GET"])
@check_auth
def get_goal_execution_provenance(goal_id: str, provenance_id: str):
    _require_goal(goal_id)
    graph = _service().get_goal_graph(goal_id)
    provenance = _public_execution_provenance(graph, provenance_id)
    if provenance is None:
        raise NotFoundError("provenance_not_found")
    include_raw_prompt = str(request.args.get("include_raw_prompt", "")).strip().lower() in {"1", "true", "yes", "on"}
    return api_response(data=_redact_provenance(provenance, include_raw_prompt=include_raw_prompt))
