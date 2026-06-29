"""CaseFlow Discovery REST API.

GET  /api/caseflow/discovery/profiles                      — list profiles
POST /api/caseflow/discovery/profiles                      — create profile
POST /api/caseflow/discovery/profiles/<profile_id>/run     — run discovery
GET  /api/caseflow/discovery/runs/<run_id>/results         — list results
POST /api/caseflow/discovery/results/<result_id>/convert   — convert to case
POST /api/caseflow/discovery/results/<result_id>/ignore    — ignore result
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from agent.caseflow.discovery import (
    DiscoveryResult,
    DiscoveryRun,
    PolicyDenied,
    SearchProfile,
    convert_result_to_case,
)

discovery_bp = Blueprint(
    "caseflow_discovery", __name__, url_prefix="/api/caseflow/discovery"
)

# In-memory stores
_profiles: dict[str, SearchProfile] = {}
_runs: dict[str, DiscoveryRun] = {}
_results: dict[str, DiscoveryResult] = {}   # result_id -> result
_run_results: dict[str, list[str]] = {}     # run_id -> [result_ids]


def _profile_to_dict(p: SearchProfile) -> dict[str, Any]:
    return p.model_dump()


def _run_to_dict(r: DiscoveryRun) -> dict[str, Any]:
    data = r.model_dump()
    data["started_at"] = r.started_at.isoformat()
    if r.finished_at:
        data["finished_at"] = r.finished_at.isoformat()
    return data


def _result_to_dict(r: DiscoveryResult) -> dict[str, Any]:
    data = r.model_dump()
    data["created_at"] = r.created_at.isoformat()
    return data


@discovery_bp.route("/profiles", methods=["GET"])
def discovery_profiles_list():
    return jsonify([_profile_to_dict(p) for p in _profiles.values()]), 200


@discovery_bp.route("/profiles", methods=["POST"])
def discovery_profiles_create():
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400
    profile = SearchProfile(
        name=body["name"],
        profile_type=body.get("profile_type", "job_search"),
        enabled=body.get("enabled", True),
        query_terms=body.get("query_terms", []),
        include_terms=body.get("include_terms", []),
        exclude_terms=body.get("exclude_terms", []),
        locations=body.get("locations", []),
        remote_policy=body.get("remote_policy"),
        source_ids=body.get("source_ids", []),
    )
    _profiles[profile.id] = profile
    return jsonify(_profile_to_dict(profile)), 201


@discovery_bp.route("/profiles/<profile_id>/run", methods=["POST"])
def run_discovery(profile_id: str):
    profile = _profiles.get(profile_id)
    if not profile:
        return jsonify({"error": "not_found"}), 404
    if not profile.enabled:
        return jsonify({"error": "profile_disabled"}), 400

    run = DiscoveryRun(profile_id=profile_id, status="running")
    _runs[run.id] = run
    _run_results[run.id] = []

    # Execute registered adapters (v1: empty results for no-op adapters)
    run.status = "done"
    run.finished_at = datetime.utcnow()
    _runs[run.id] = run

    return jsonify({"run_id": run.id, "status": run.status}), 200


@discovery_bp.route("/runs/<run_id>/results", methods=["GET"])
def run_results(run_id: str):
    if run_id not in _runs:
        return jsonify({"error": "not_found"}), 404
    result_ids = _run_results.get(run_id, [])
    results = [_results[r_id] for r_id in result_ids if r_id in _results]
    return jsonify([_result_to_dict(r) for r in results]), 200


@discovery_bp.route("/results/<result_id>/convert", methods=["POST"])
def convert_result(result_id: str):
    result = _results.get(result_id)
    if not result:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    case_type = body.get("case_type", "generic")
    approved_by = body.get("approved_by", "")
    options = body.get("options") or {}

    try:
        case = convert_result_to_case(
            result=result,
            case_type=case_type,
            approved_by=approved_by,
            options=options,
        )
        # Store the newly created case in the caseflow store
        from agent.routes.caseflow import _cases, _artifacts, _actions
        _cases[case.id] = case
        _artifacts[case.id] = []
        _actions[case.id] = []
        _results[result_id] = result  # persist converted state
        return jsonify(case.model_dump()), 201
    except PolicyDenied as exc:
        return jsonify({"error": exc.error_code, "detail": exc.reason}), 403


@discovery_bp.route("/results/<result_id>/ignore", methods=["POST"])
def ignore_result(result_id: str):
    result = _results.get(result_id)
    if not result:
        return jsonify({"error": "not_found"}), 404
    result.ignored = True
    _results[result_id] = result
    return jsonify({"ok": True, "result_id": result_id}), 200


def _add_result(run_id: str, result: DiscoveryResult) -> None:
    """Helper for tests: add a result to the in-memory store."""
    _results[result.id] = result
    _run_results.setdefault(run_id, []).append(result.id)


def reset_stores() -> None:
    """Reset in-memory stores. For tests only."""
    _profiles.clear()
    _runs.clear()
    _results.clear()
    _run_results.clear()
