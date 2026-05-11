from flask import Blueprint, g, request
from sqlmodel import Session, select
from agent.auth import check_auth
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import ContextAccessPolicyDB
from agent.services.context_access_policy_service import ContextAccessPolicyService
from worker.core.context_access_policy import ContextAccessPolicy, ContextAccessRule, SourceType, Sensitivity, ModelScope

context_policy_bp = Blueprint("context_policy", __name__)
policy_service = ContextAccessPolicyService()

def _parse_rule(r: dict) -> ContextAccessRule:
    """Helper to parse JSON rule into ContextAccessRule with proper Enum conversion."""
    if "source_types" in r:
        r["source_types"] = [SourceType(t) for t in r["source_types"]]
    if "sensitivity" in r and r["sensitivity"]:
        r["sensitivity"] = Sensitivity(r["sensitivity"])
    if "allowed_model_scopes" in r:
        r["allowed_model_scopes"] = [ModelScope(s) for s in r["allowed_model_scopes"]]
    return ContextAccessRule(**r)

@context_policy_bp.route("/api/context-policy", methods=["GET"])
@check_auth
def list_policies():
    project_id = request.args.get("project_id")
    with Session(engine) as session:
        statement = select(ContextAccessPolicyDB)
        if project_id:
            statement = statement.where(ContextAccessPolicyDB.project_id == project_id)
        policies = session.exec(statement).all()
        return api_response(data=[p.dict() for p in policies])

@context_policy_bp.route("/api/context-policy", methods=["POST"])
@check_auth
def create_policy():
    data = request.json
    if not data.get("policy_id") or "rules" not in data:
         return api_response(status="error", message="Missing policy_id or rules", code=400)

    try:
        rules = [_parse_rule(r) for r in data.get("rules", [])]
        cap = ContextAccessPolicy(
            policy_id=data["policy_id"],
            version=data.get("version", 1),
            scope=data.get("scope", "project"),
            rules=rules,
            defaults=data.get("defaults", {})
        )
        errors = policy_service.validate_policy(cap)
        if errors:
            return api_response(status="error", message="Policy validation failed", code=400, data={"errors": errors})

        with Session(engine) as session:
            db_policy = ContextAccessPolicyDB(
                policy_id=cap.policy_id,
                version=cap.version,
                project_id=data.get("project_id"),
                scope=cap.scope,
                policy_json=data,
                created_by=g.user.username if hasattr(g, 'user') and g.user else None
            )
            session.add(db_policy)
            session.commit()
            session.refresh(db_policy)
            return api_response(data=db_policy.dict())
    except Exception as e:
        return api_response(status="error", message=str(e), code=500)

@context_policy_bp.route("/api/context-policy/lint", methods=["POST"])
@check_auth
def lint_policy():
    data = request.json
    try:
        rules = [_parse_rule(r) for r in data.get("rules", [])]
        cap = ContextAccessPolicy(
            policy_id=data.get("policy_id", "draft"),
            version=data.get("version", 1),
            scope=data.get("scope", "project"),
            rules=rules
        )
        errors = policy_service.validate_policy(cap)
        return api_response(data={"errors": errors, "valid": len(errors) == 0})
    except Exception as e:
        return api_response(status="error", message=str(e), code=500)
