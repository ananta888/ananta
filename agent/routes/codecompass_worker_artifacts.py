"""Hub endpoint serving admitted CodeCompass artifacts to delegated workers (WCRB-010/013).

``GET /api/codecompass/worker-artifacts/<sha256>`` serves an admitted graph or
visual-metrics artifact; ``GET /api/codecompass/worker-index-artifacts/<index_id>/<role>``
serves a retrieval output (index/details/relations) of a granted index so a worker
can run retrieval locally against the Hub-owned index.

Both require a registered worker (scope ``codecompass.artifacts.read``) and, in
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


def _verified_capability():
    """The signed capability of this request, verified and addressed to the calling worker."""
    from agent.services.codecompass_capability_issuer import (
        CapabilityError,
        capability_key,
        verify_signed_capability,
    )
    from agent.services.codecompass_worker_artifacts import (
        CAPABILITY_HEADER,
        decode_capability,
    )

    worker_url = str(dict(getattr(g, "service_identity", {}) or {}).get("worker_url") or "")
    capability = verify_signed_capability(
        decode_capability(request.headers.get(CAPABILITY_HEADER, "")), key=capability_key()
    )
    if not worker_url or str(capability.get("audience") or "").rstrip("/") != worker_url.rstrip("/"):
        raise CapabilityError("capability_audience_mismatch")
    return capability


@codecompass_worker_artifacts_bp.route("/api/codecompass/worker-artifacts/<sha256>", methods=["GET"])
@check_registered_worker_auth(scope=CODECOMPASS_ARTIFACT_READ_SCOPE)
def get_worker_artifact(sha256: str):
    from agent.services.codecompass_capability_issuer import CapabilityError
    from agent.services.codecompass_graph_artifact_resolver import get_codecompass_graph_artifact_resolver
    from agent.services.codecompass_worker_artifacts import (
        ArtifactAccessError,
        locate_admitted_artifact,
    )
    from agent.services.repository_registry import get_repository_registry

    try:
        capability = _verified_capability()
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


@codecompass_worker_artifacts_bp.route(
    "/api/codecompass/worker-index-artifacts/<knowledge_index_id>/<role>", methods=["GET"]
)
@check_registered_worker_auth(scope=CODECOMPASS_ARTIFACT_READ_SCOPE)
def get_worker_index_artifact(knowledge_index_id: str, role: str):
    from agent.services.codecompass_capability_issuer import CapabilityError
    from agent.services.codecompass_worker_artifacts import (
        ARTIFACT_DIGEST_HEADER,
        ArtifactAccessError,
        locate_admitted_index_output,
    )
    from agent.services.repository_registry import get_repository_registry

    try:
        capability = _verified_capability()
        path, digest = locate_admitted_index_output(
            capability, knowledge_index_id, role,
            get_index=get_repository_registry().knowledge_index_repo.get_by_id,
        )
    except CapabilityError as error:
        return _denied(str(error), 403)
    except ArtifactAccessError as error:
        reason = str(error)
        code = 404 if reason in {"artifact_not_found", "artifact_output_dir_missing"} else 403
        return _denied(reason, code)
    response = send_file(path, mimetype="application/x-ndjson" if path.suffix == ".jsonl" else "application/json",
                         as_attachment=True, download_name=path.name)
    response.headers[ARTIFACT_DIGEST_HEADER] = digest
    return response
