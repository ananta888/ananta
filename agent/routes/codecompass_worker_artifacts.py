"""Hub endpoint serving admitted CodeCompass graph artifacts to delegated workers (WCRB-010).

GET /api/codecompass/worker-artifacts/<sha256>

Requires a registered worker (scope ``codecompass.artifacts.read``) and, in
``X-Ananta-CodeCompass-Capability``, a Hub-signed capability addressed to that
very worker. Only an artifact bound by one of the capability's indices is served,
after the resolver verified its hash.
"""

from __future__ import annotations

from flask import Blueprint, g, request, send_file

from agent.auth import check_registered_worker_auth
from agent.common.errors import api_response
from agent.services.workflow_worker_service_auth import CODECOMPASS_ARTIFACT_READ_SCOPE

codecompass_worker_artifacts_bp = Blueprint("codecompass_worker_artifacts", __name__)


def _denied(reason: str, code: int):
    return api_response(status="error", message="forbidden", data={"reason_code": reason}, code=code)


@codecompass_worker_artifacts_bp.route("/api/codecompass/worker-artifacts/<sha256>", methods=["GET"])
@check_registered_worker_auth(scope=CODECOMPASS_ARTIFACT_READ_SCOPE)
def get_worker_artifact(sha256: str):
    from agent.services.codecompass_capability_issuer import (
        CapabilityError,
        capability_key,
        verify_signed_capability,
    )
    from agent.services.codecompass_graph_artifact_resolver import get_codecompass_graph_artifact_resolver
    from agent.services.codecompass_worker_artifacts import (
        CAPABILITY_HEADER,
        ArtifactAccessError,
        decode_capability,
        locate_admitted_artifact,
    )
    from agent.services.repository_registry import get_repository_registry

    worker_url = str(dict(getattr(g, "service_identity", {}) or {}).get("worker_url") or "")
    try:
        capability = verify_signed_capability(decode_capability(request.headers.get(CAPABILITY_HEADER, "")),
                                              key=capability_key())
        if not worker_url or str(capability.get("audience") or "").rstrip("/") != worker_url.rstrip("/"):
            raise CapabilityError("capability_audience_mismatch")
        path = locate_admitted_artifact(capability, sha256,
                                        get_index=get_repository_registry().knowledge_index_repo.get_by_id,
                                        resolver=get_codecompass_graph_artifact_resolver())
    except (CapabilityError, ArtifactAccessError) as error:
        reason = str(error)
        return _denied(reason, 404 if reason == "artifact_not_granted" else 403)
    except ValueError as error:  # resolver: artifact missing or its hash no longer matches
        return _denied(str(error) or "artifact_unavailable", 404)
    response = send_file(path, mimetype="application/json", as_attachment=True, download_name=path.name)
    response.headers["X-Artifact-SHA256"] = sha256
    return response
