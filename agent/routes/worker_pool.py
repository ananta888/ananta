from __future__ import annotations

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.repository import worker_slot_lease_repo
from agent.services.worker_pool_scheduler_service import get_worker_pool_scheduler_service


worker_pool_bp = Blueprint("worker_pool", __name__)


@worker_pool_bp.route("/worker-pool/status", methods=["GET"])
@check_auth
def worker_pool_status():
    scheduler = get_worker_pool_scheduler_service()
    return api_response(data=scheduler.get_scheduler_status())


@worker_pool_bp.route("/worker-pool/leases", methods=["GET"])
@check_auth
def worker_pool_leases():
    leases = worker_slot_lease_repo.list_all()
    return api_response(data={"items": [lease.model_dump(mode="json") for lease in leases]})


@worker_pool_bp.route("/worker-pool/queues", methods=["GET"])
@check_auth
def worker_pool_queues():
    queued = worker_slot_lease_repo.list_queued()
    return api_response(data={"items": [lease.model_dump(mode="json") for lease in queued]})


@worker_pool_bp.route("/worker-pool/ollama-models", methods=["GET"])
@check_auth
def worker_pool_ollama_models():
    scheduler = get_worker_pool_scheduler_service()
    status = scheduler.get_scheduler_status().get("capacity_by_model", {})
    return api_response(data={"items": status})


@worker_pool_bp.route("/worker-pool/cleanup-stale-leases", methods=["POST"])
@admin_required
def worker_pool_cleanup_stale_leases():
    scheduler = get_worker_pool_scheduler_service()
    cleaned = scheduler.cleanup_stale_leases()
    log_audit("worker_pool_cleanup_stale_leases", {"cleaned": cleaned})
    return api_response(data={"released": cleaned})


@worker_pool_bp.route("/worker-pool/revalidate-queued", methods=["POST"])
@admin_required
def worker_pool_revalidate_queued():
    payload = request.get_json(silent=True) or {}
    lease_id = str(payload.get("slot_lease_id") or "").strip()
    if not lease_id:
        return api_response(status="error", message="slot_lease_id_required", code=400)
    decision = get_worker_pool_scheduler_service().revalidate_queued_job(
        slot_lease_id=lease_id,
        policy_decision_ref=str(payload.get("policy_decision_ref") or "").strip() or None,
        policy_decision_hash=str(payload.get("policy_decision_hash") or "").strip() or None,
        worker_online=bool(payload.get("worker_online", True)),
        policy_allowed=bool(payload.get("policy_allowed", True)),
        capacity_available=bool(payload.get("capacity_available", True)),
    )
    return api_response(data=decision.__dict__)
