"""Read-only authenticated Hub operator API; never a Worker memory capability."""

from flask import Blueprint, current_app, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.common.errors import api_response
from agent.services.memory_audit_read_service import MemoryAuditQuery, MemoryAuditReadService
from agent.services.project_access_authority import ProjectAccessError
from ananta_contracts.memory_access import MemoryScope

memory_audit_bp = Blueprint("memory_audit", __name__)


def _service():
    injected = current_app.extensions.get("memory_audit_read_service")
    if injected is not None:
        return injected
    access = current_app.extensions.get("project_access_authority")
    if access is None:
        raise RuntimeError("memory_audit_read_unavailable")
    from agent.database import engine
    from agent.repositories.memory_audit import SQLMemoryAuditRepository
    from agent.repositories.memory_snapshot import SQLMemorySnapshotRepository

    return MemoryAuditReadService(access=access, repository=SQLMemoryAuditRepository(engine),
                                  snapshots=SQLMemorySnapshotRepository(engine))


@memory_audit_bp.get("/api/projects/<project_id>/memory-audit/events")
@memory_audit_bp.get("/api/projects/<project_id>/memory-audit/diffs", defaults={"diffs": True})
@check_auth
def memory_audit_events(project_id, diffs=False):
    if current_app.config.get("ROLE") != "hub":
        return api_response(status="error", message="memory_audit_hub_required", code=403)
    try:
        allowed = {"page_size", "after_sequence", "task_id", "run_id", "trace_id", "channel_id", "scope"}
        if set(request.args) - allowed or any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise ValueError("memory_audit_query_invalid")
        values = {key: request.args[key] for key in request.args}
        for key in ("page_size", "after_sequence"):
            if key in values:
                if not values[key].isascii() or not values[key].isdigit() or len(values[key]) > 19:
                    raise ValueError("memory_audit_query_invalid")
                values[key] = int(values[key])
        if "scope" in values:
            values["scope"] = MemoryScope(values["scope"])
        service = _service()
        method = service.diffs if diffs else service.page
        result = method(principal=get_authenticated_source_control_principal(),
                        project_id=project_id, query=MemoryAuditQuery(**values))
        return api_response(data=result)
    except ProjectAccessError as exc:
        return api_response(status="error", message=exc.reason_code, code=exc.public_status)
    except PermissionError:
        return api_response(status="error", message="memory_audit_project_denied", code=403)
    except (ValueError, TypeError):
        return api_response(status="error", message="memory_audit_query_or_data_invalid", code=422)
    except Exception:
        return api_response(status="error", message="memory_audit_read_unavailable", code=503)
