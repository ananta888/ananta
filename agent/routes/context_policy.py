from flask import Blueprint, request, jsonify
from agent.services.context_access_policy_service import ContextAccessPolicyService
from worker.core.context_access_policy import ContextAccessPolicy, ContextAccessRule
import time

context_policy_bp = Blueprint("context_policy", __name__, url_prefix="/api/context-policy")
policy_service = ContextAccessPolicyService()

@context_policy_bp.route("/policies", methods=["GET"])
def list_policies():
    project_id = request.args.get("project_id")
    policies = policy_service.list_policies(project_id=project_id)
    
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
            
        policy_db = policy_service.create_policy_record(payload=data, now_ts=time.time())
        return jsonify({"status": "success", "data": policy_db.dict()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@context_policy_bp.route("/policies/<policy_id>/latest", methods=["GET"])
def get_latest_policy(policy_id):
    policy_db = policy_service.get_latest_policy(policy_id)
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
