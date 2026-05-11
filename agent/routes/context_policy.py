from flask import Blueprint, request, jsonify
from agent.services.context_access_policy_service import ContextAccessPolicyService
from agent.repositories.context_access_policy_repo import get_context_access_policy_repo
from worker.core.context_access_policy import ContextAccessPolicy, ContextAccessRule
from agent.db_models import ContextAccessPolicyDB
import time

context_policy_bp = Blueprint("context_policy", __name__, url_prefix="/api/context-policy")
policy_service = ContextAccessPolicyService()
policy_repo = get_context_access_policy_repo()

@context_policy_bp.route("/policies", methods=["GET"])
def list_policies():
    project_id = request.args.get("project_id")
    if project_id:
        policies = policy_repo.find_by_project(project_id)
    else:
        policies = policy_repo.find_by_scope("system_default")
    
    return jsonify({
        "status": "success",
        "data": [p.dict() for p in policies]
    })

@context_policy_bp.route("/policies", methods=["POST"])
def create_policy():
    data = request.json
    try:
        # Validate rules
        rules = [ContextAccessRule(**r) for r in data.get("rules", [])]
        policy = ContextAccessPolicy(
            policy_id=data["policy_id"],
            version=data.get("version", 1),
            scope=data.get("scope", "project"),
            rules=rules,
            defaults=data.get("defaults", {}),
            precedence=data.get("precedence", [])
        )
        
        errors = policy_service.validate_policy(policy)
        if errors:
            return jsonify({"status": "error", "message": "Validation failed", "errors": errors}), 400
            
        policy_db = ContextAccessPolicyDB(
            policy_id=policy.policy_id,
            version=policy.version,
            project_id=data.get("project_id"),
            scope=policy.scope,
            policy_json=data,
            created_at=time.time(),
            updated_at=time.time()
        )
        policy_repo.save(policy_db)
        return jsonify({"status": "success", "data": policy_db.dict()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@context_policy_bp.route("/policies/<policy_id>/latest", methods=["GET"])
def get_latest_policy(policy_id):
    policy_db = policy_repo.get_latest_version(policy_id)
    if not policy_db:
        return jsonify({"status": "error", "message": "Policy not found"}), 404
    return jsonify({"status": "success", "data": policy_db.dict()})

@context_policy_bp.route("/validate", methods=["POST"])
def validate_policy_payload():
    data = request.json
    try:
        rules = [ContextAccessRule(**r) for r in data.get("rules", [])]
        policy = ContextAccessPolicy(
            policy_id=data.get("policy_id", "temp"),
            version=data.get("version", 1),
            scope=data.get("scope", "project"),
            rules=rules,
            defaults=data.get("defaults", {}),
            precedence=data.get("precedence", [])
        )
        errors = policy_service.validate_policy(policy)
        return jsonify({
            "status": "success" if not errors else "error",
            "valid": len(errors) == 0,
            "errors": errors
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400
