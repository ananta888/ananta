from __future__ import annotations

from dataclasses import asdict
from typing import Mapping

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import admin_required, check_auth
from agent.repositories.knowledge_hygiene_repository import KnowledgeHygieneRepositoryError
from agent.services.knowledge_hygiene.context import get_knowledge_hygiene_service
from agent.services.knowledge_hygiene.run_service import KnowledgeHygieneRunError
from agent.services.knowledge_hygiene.service import KnowledgeHygieneServiceError
from agent.services.knowledge_hygiene.writeback import KnowledgeWritebackError
from ananta_contracts.knowledge_hygiene import (
    CoverageState,
    KnowledgeHygieneContractError,
    SourceRevisionBinding,
    WorkerKnowledgeHygieneResult,
)


knowledge_hygiene_bp = Blueprint(
    "knowledge_hygiene",
    __name__,
    url_prefix="/api/knowledge-hygiene",
)


def _service():
    service = get_knowledge_hygiene_service(current_app.config.get("AGENT_CONFIG", {}) or {})
    service.ensure_available()
    return service


def _payload() -> dict[str, object]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise KnowledgeHygieneServiceError("invalid_json_payload")
    return value


def _mapping_items(payload: Mapping[str, object], field: str) -> tuple[Mapping[str, object], ...]:
    raw = payload.get(field)
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise KnowledgeHygieneServiceError(f"invalid_{field}")
    return tuple(raw)


def _actor(*, human: bool) -> str:
    user = getattr(g, "user", None)
    auth = getattr(g, "auth_payload", None)
    source = user if isinstance(user, Mapping) else auth if isinstance(auth, Mapping) else {}
    if human and not isinstance(user, Mapping):
        raise KnowledgeHygieneServiceError("human_actor_required")
    actor_id = str(source.get("sub") or source.get("username") or source.get("agent_id") or "").strip()
    if not actor_id:
        raise KnowledgeHygieneServiceError("authenticated_actor_required")
    return actor_id


def _worker_actor() -> str:
    if isinstance(getattr(g, "user", None), Mapping):
        raise KnowledgeHygieneServiceError("worker_actor_required")
    auth = getattr(g, "auth_payload", None)
    if not isinstance(auth, Mapping):
        raise KnowledgeHygieneServiceError("worker_actor_required")
    worker_id = str(auth.get("agent_id") or auth.get("sub") or "").strip()
    if not worker_id:
        raise KnowledgeHygieneServiceError("worker_actor_required")
    return worker_id


def _project_access(project_id: str) -> None:
    if not project_id or len(project_id) > 200:
        raise KnowledgeHygieneServiceError("invalid_project_id")
    declared = str(request.headers.get("X-Ananta-Project-Id") or "").strip()
    if declared and declared != project_id:
        raise KnowledgeHygieneServiceError("cross_project_request_rejected")
    auth = getattr(g, "auth_payload", None)
    user = getattr(g, "user", None)
    claims = auth if isinstance(auth, Mapping) else user if isinstance(user, Mapping) else {}
    allowed = claims.get("project_ids")
    if isinstance(allowed, list) and allowed and project_id not in {str(item) for item in allowed}:
        raise KnowledgeHygieneServiceError("project_access_denied")


def _command_key(payload: Mapping[str, object], field: str) -> str:
    header = str(request.headers.get("Idempotency-Key") or "").strip()
    body = str(payload.get(field) or "").strip()
    if not header or not body or header != body:
        raise KnowledgeHygieneServiceError("idempotency_key_required")
    return body


def _limit() -> int:
    try:
        return max(1, min(int(request.args.get("limit", "100")), 500))
    except ValueError as exc:
        raise KnowledgeHygieneServiceError("invalid_limit") from exc


def _ok(data: object, code: int = 200):
    return jsonify({"status": "ok", "data": data}), code


@knowledge_hygiene_bp.errorhandler(KnowledgeHygieneServiceError)
@knowledge_hygiene_bp.errorhandler(KnowledgeHygieneRunError)
@knowledge_hygiene_bp.errorhandler(KnowledgeHygieneRepositoryError)
@knowledge_hygiene_bp.errorhandler(KnowledgeHygieneContractError)
@knowledge_hygiene_bp.errorhandler(KnowledgeWritebackError)
def _domain_error(error):
    reason = getattr(error, "reason_code", "knowledge_hygiene_error")
    code = 404 if reason.endswith("not_found") else 409 if any(
        token in reason for token in ("stale", "mismatch", "collision", "race")
    ) else 403 if any(token in reason for token in ("denied", "disabled", "required", "outside", "forbidden")) else 400
    return jsonify({"status": "error", "message": reason, "data": {"reason_code": reason}}), code


@knowledge_hygiene_bp.errorhandler(ValueError)
def _value_error(_error):
    return jsonify({"status": "error", "message": "invalid_request", "data": {"reason_code": "invalid_request"}}), 400


@knowledge_hygiene_bp.get("/projects/<project_id>/health")
@check_auth
def get_health(project_id: str):
    _project_access(project_id)
    service = _service()
    snapshot = service.repository.latest_health(project_id)
    if snapshot is None:
        snapshot = service.refresh_health(project_id, coverage=CoverageState.UNKNOWN)
    return _ok(snapshot.to_dict())


@knowledge_hygiene_bp.get("/projects/<project_id>/claims")
@check_auth
def list_claims(project_id: str):
    _project_access(project_id)
    page = _service().list_claims(
        project_id,
        cursor=request.args.get("cursor"),
        limit=_limit(),
    )
    return _ok({"items": [item.to_dict() for item in page.items], "next_cursor": page.next_cursor})


@knowledge_hygiene_bp.get("/projects/<project_id>/conflicts")
@check_auth
def list_conflicts(project_id: str):
    _project_access(project_id)
    page = _service().list_conflicts(
        project_id,
        state=request.args.get("state"),
        cursor=request.args.get("cursor"),
        limit=_limit(),
    )
    return _ok({"items": [item.to_dict() for item in page.items], "next_cursor": page.next_cursor})


@knowledge_hygiene_bp.get("/projects/<project_id>/conflicts/<conflict_id>")
@check_auth
def get_conflict(project_id: str, conflict_id: str):
    _project_access(project_id)
    return _ok(_service().conflict_detail(project_id, conflict_id))


@knowledge_hygiene_bp.get("/projects/<project_id>/wiki")
@check_auth
def list_wiki(project_id: str):
    _project_access(project_id)
    page = _service().list_pages(project_id, cursor=request.args.get("cursor"), limit=_limit())
    return _ok({"items": [item.to_dict() for item in page.items], "next_cursor": page.next_cursor})


@knowledge_hygiene_bp.get("/projects/<project_id>/wiki/<slug>")
@check_auth
def get_wiki(project_id: str, slug: str):
    _project_access(project_id)
    revision = request.args.get("revision")
    page = _service().repository.get_page(project_id, slug, int(revision) if revision else None)
    if page is None:
        raise KnowledgeHygieneServiceError("wiki_page_not_found")
    return _ok(page.to_dict())


@knowledge_hygiene_bp.get("/projects/<project_id>/graph-supplement")
@check_auth
def get_graph_supplement(project_id: str):
    _project_access(project_id)
    return _ok(asdict(_service().graph_supplement(project_id)))


@knowledge_hygiene_bp.get("/projects/<project_id>/metrics")
@check_auth
def get_metrics(project_id: str):
    _project_access(project_id)
    return _ok(_service().metrics_report(project_id))


@knowledge_hygiene_bp.get("/projects/<project_id>/runs/<run_id>")
@check_auth
def get_run(project_id: str, run_id: str):
    _project_access(project_id)
    run = _service().repository.get_run(project_id, run_id)
    if run is None:
        raise KnowledgeHygieneServiceError("run_not_found")
    return _ok(run.to_dict())


@knowledge_hygiene_bp.post("/projects/<project_id>/runs")
@admin_required
def create_run(project_id: str):
    _project_access(project_id)
    payload = _payload()
    run_id = _command_key(payload, "run_id")
    bindings = tuple(
        SourceRevisionBinding.from_mapping(item)
        for item in _mapping_items(payload, "source_bindings")
    )
    raw_budgets = payload.get("budgets") or {}
    if not isinstance(raw_budgets, Mapping):
        raise KnowledgeHygieneServiceError("invalid_budgets")
    run = _service().start_run(
        run_id=run_id,
        project_id=project_id,
        source_bindings=bindings,
        actor_id=_actor(human=True),
        profile_name=str(payload.get("profile_name") or "deterministic-v1"),
        policy_version=str(payload.get("policy_version") or "knowledge_claim_precedence.v1"),
        budgets=dict(raw_budgets),
        automatic=bool(payload.get("automatic", False)),
    )
    return _ok(run.to_dict(), 201)


@knowledge_hygiene_bp.post("/projects/<project_id>/runs/<run_id>/dispatch")
@admin_required
def dispatch_run(project_id: str, run_id: str):
    _project_access(project_id)
    payload = _payload()
    _command_key(payload, "command_id")
    run = _service().runs.dispatch(
        project_id=project_id,
        run_id=run_id,
        worker_id=str(payload.get("worker_id") or ""),
        lease_seconds=int(payload.get("lease_seconds") or 300),
    )
    return _ok(run.to_dict())


@knowledge_hygiene_bp.post("/projects/<project_id>/runs/<run_id>/results")
@check_auth
def admit_result(project_id: str, run_id: str):
    _project_access(project_id)
    payload = _payload()
    result = WorkerKnowledgeHygieneResult(
        run_id=run_id,
        assignment_digest=str(payload.get("assignment_digest") or ""),
        claims=_mapping_items(payload, "claims"),
        wiki_pages=_mapping_items(payload, "wiki_pages"),
        conflict_candidates=_mapping_items(payload, "conflict_candidates"),
        correction_proposals=_mapping_items(payload, "correction_proposals"),
        coverage=CoverageState(str(payload.get("coverage") or "unknown")),
        processed_records=int(payload.get("processed_records") or 0),
        rejected_records=int(payload.get("rejected_records") or 0),
    )
    supplied_digest = str(payload.get("result_digest") or result.result_digest)
    if supplied_digest != result.result_digest:
        raise KnowledgeHygieneServiceError("result_digest_mismatch")
    run, claims = _service().admit_worker_result(
        project_id=project_id,
        worker_id=_worker_actor(),
        result=result,
    )
    return _ok({"run": run.to_dict(), "claims": [item.to_dict() for item in claims]})


@knowledge_hygiene_bp.post("/projects/<project_id>/analysis")
@admin_required
def analyze(project_id: str):
    _project_access(project_id)
    payload = _payload()
    _command_key(payload, "command_id")
    result = _service().analyze_project(project_id, actor_id=_actor(human=True))
    return _ok(
        {
            "conflicts": [item.to_dict() for item in result.conflicts],
            "exact_duplicates": [asdict(item) for item in result.exact_duplicates],
            "semantic_duplicates": [asdict(item) for item in result.semantic_duplicates],
            "coverage": result.coverage.value,
            "evaluated_pairs": result.evaluated_pairs,
            "skipped_pairs": result.skipped_pairs,
            "reason_codes": result.reason_codes,
        }
    )


@knowledge_hygiene_bp.post("/projects/<project_id>/wiki")
@admin_required
def publish_wiki(project_id: str):
    _project_access(project_id)
    payload = _payload()
    _command_key(payload, "command_id")
    return _ok(_service().publish_page(project_id=project_id, proposal=payload, actor_id=_actor(human=True)).to_dict(), 201)


@knowledge_hygiene_bp.post("/projects/<project_id>/conflicts/<conflict_id>/decisions")
@admin_required
def decide_conflict(project_id: str, conflict_id: str):
    _project_access(project_id)
    payload = _payload()
    decision_id = _command_key(payload, "decision_id")
    conflict = _service().decide_conflict(
        project_id=project_id,
        conflict_id=conflict_id,
        decision_id=decision_id,
        expected_version=int(payload.get("expected_version") or 0),
        basis_digest=str(payload.get("basis_digest") or ""),
        actor_id=_actor(human=True),
        actor_kind="human",
        decision=str(payload.get("decision") or ""),
        rationale=str(payload.get("rationale") or ""),
        qualifiers=tuple(str(item) for item in payload.get("qualifiers") or ()),
        writeback_requested=bool(payload.get("writeback_requested", False)),
        second_approver_id=str(payload["second_approver_id"]) if payload.get("second_approver_id") else None,
    )
    return _ok(conflict.to_dict())


@knowledge_hygiene_bp.post("/projects/<project_id>/conflicts/<conflict_id>/recheck")
@admin_required
def recheck_conflict(project_id: str, conflict_id: str):
    _project_access(project_id)
    payload = _payload()
    _command_key(payload, "command_id")
    conflict = _service().recheck_conflict(
        project_id=project_id,
        conflict_id=conflict_id,
        run_id=str(payload.get("run_id") or ""),
        left_claim_id=str(payload.get("left_claim_id") or ""),
        right_claim_id=str(payload.get("right_claim_id") or ""),
        actor_id=_actor(human=True),
    )
    return _ok(conflict.to_dict())


@knowledge_hygiene_bp.post("/projects/<project_id>/conflicts/<conflict_id>/corrections")
@check_auth
def propose_correction(project_id: str, conflict_id: str):
    _project_access(project_id)
    payload = _payload()
    correction_id = _command_key(payload, "correction_id")
    proposal = _service().propose_correction(
        project_id=project_id,
        conflict_id=conflict_id,
        run_id=str(payload.get("run_id") or ""),
        correction_id=correction_id,
        source_id=str(payload.get("source_id") or ""),
        source_revision=str(payload.get("source_revision") or ""),
        source_locator=str(payload.get("source_locator") or ""),
        base_content_sha256=str(payload.get("base_content_sha256") or ""),
        proposed_content=str(payload.get("proposed_content") or ""),
        worker_id=_worker_actor(),
    )
    return _ok(proposal.to_dict(), 201)


@knowledge_hygiene_bp.get("/projects/<project_id>/corrections/<correction_id>")
@check_auth
def get_correction(project_id: str, correction_id: str):
    _project_access(project_id)
    return _ok(_service().correction_detail(project_id, correction_id))


@knowledge_hygiene_bp.post("/projects/<project_id>/corrections/<correction_id>/writeback")
@admin_required
def approve_writeback(project_id: str, correction_id: str):
    _project_access(project_id)
    payload = _payload()
    _command_key(payload, "approval_id")
    receipt = _service().approve_writeback(
        project_id=project_id,
        correction_id=correction_id,
        proposal_digest=str(payload.get("proposal_digest") or ""),
        actor_id=_actor(human=True),
        actor_kind="human",
    )
    return _ok(asdict(receipt))
