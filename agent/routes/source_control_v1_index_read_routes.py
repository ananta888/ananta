"""Graph, query, artifact and CodeHug mutation routes of the Source Control v1 API.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, Response, request

from agent.routes.source_control_v1_authorization import SourceControlV1RequestGuard
from agent.routes.source_control_v1_http import (
    GRAPH_DOMAIN_SCOPE,
    SOURCE_GRAPH_MAX_EDGES,
    SourceControlApiError,
    boundary,
    bounded_int,
    json_object,
    query_boolean,
    require_exact_fields,
    require_idempotency_key,
    required_string,
    success_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)


def register_index_read_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Graph, query and artifact status reads of the active index."""

    @blueprint.get("/connections/<connection_id>/graph")
    @check_auth
    @boundary
    def graph(connection_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.GRAPH,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        if set(request.args) - {
            "project_id",
            "cursor",
            "limit",
            "view",
            "max_edges",
            "domain_scope",
            "include_subdomains",
            "stage",
        }:
            raise SourceControlApiError("graph_parameters_invalid")
        raw_max_edges = request.args.get("max_edges")
        requested_view = request.args.get("view", "default")
        normalized_view = str(requested_view).strip().lower()
        if (
            normalized_view == "topology"
            and request.args.get("cursor") is not None
        ):
            raise SourceControlApiError("graph_topology_cursor_unsupported")
        domain_scope = request.args.get("domain_scope")
        if domain_scope is not None and not GRAPH_DOMAIN_SCOPE.fullmatch(
            domain_scope
        ):
            raise SourceControlApiError("domain_scope_invalid")
        stage = request.args.get("stage")
        if stage is not None and (
            normalized_view != "staged" or stage not in {"nodes", "edges"}
        ):
            raise SourceControlApiError("graph_stage_invalid")
        parameters: dict[str, object] = {
            "cursor": request.args.get("cursor"),
            "limit": bounded_int(
                request.args.get("limit"),
                field="limit",
                default=100,
                minimum=1,
                maximum=500,
            ),
            "view": requested_view,
            "max_edges": (
                bounded_int(
                    raw_max_edges,
                    field="max_edges",
                    default=SOURCE_GRAPH_MAX_EDGES,
                    minimum=1,
                    maximum=SOURCE_GRAPH_MAX_EDGES,
                )
                if raw_max_edges is not None
                else None
            ),
        }
        if domain_scope is not None:
            parameters["domain_scope"] = domain_scope
        if request.args.get("include_subdomains") is not None:
            parameters["include_subdomains"] = query_boolean(
                request.args.get("include_subdomains"),
                field="include_subdomains",
                default=True,
            )
        if stage is not None:
            parameters["stage"] = stage
        if (
            domain_scope is not None
            or request.args.get("include_subdomains") is not None
        ) and normalized_view not in {"topology", "staged"}:
            raise SourceControlApiError("graph_domain_parameters_invalid")
        return success_response(
            api.graph(
                principal=request_guard.principal(),
                connection_id=connection_id,
                parameters=parameters,
            )
        )

    @blueprint.post("/connections/<connection_id>/query")
    @check_auth
    @boundary
    def query(connection_id: str):
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset({"query", "limit"}),
            required=frozenset({"query"}),
        )
        required_string(payload, "query", maximum=4000)
        denied = request_guard.authorize(api,
            action=SourceControlAction.QUERY,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.query(
                principal=request_guard.principal(),
                connection_id=connection_id,
                payload={
                    "query": payload["query"],
                    "limit": bounded_int(
                        payload.get("limit"),
                        field="limit",
                        default=20,
                        minimum=1,
                        maximum=100,
                    ),
                },
            )
        )

    @blueprint.get(
        "/connections/<connection_id>/artifacts/<artifact_id>/status"
    )
    @check_auth
    @boundary
    def artifact_status(connection_id: str, artifact_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.ARTIFACT,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.artifact_status(
                principal=request_guard.principal(),
                connection_id=connection_id,
                artifact_id=artifact_id,
            )
        )


def register_artifact_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Artifact download and CodeHug mutation routes."""

    @blueprint.get(
        "/connections/<connection_id>/artifacts/<artifact_id>/download"
    )
    @check_auth
    @boundary
    def artifact_download(connection_id: str, artifact_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.DOWNLOAD,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        stream = api.artifact_download(
            principal=request_guard.principal(),
            connection_id=connection_id,
            artifact_id=artifact_id,
            range_header=request.headers.get("Range"),
        )
        if not isinstance(stream, SourceControlArtifactStream):
            raise SourceControlApiError(
                "artifact_download_result_invalid", status_code=502
            )
        response = Response(
            stream.body,
            status=stream.status_code,
            content_type=stream.media_type,
        )
        response.headers["Content-Length"] = str(stream.content_length)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{stream.filename}"'
        )
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["ETag"] = stream.etag
        response.headers["X-Content-SHA256"] = stream.sha256
        response.headers["X-Content-Type-Options"] = "nosniff"
        if stream.content_range is not None:
            response.headers["Content-Range"] = stream.content_range
        response.call_on_close(stream.close)
        return response

    @blueprint.post("/codehug/mutations")
    @check_auth
    @boundary
    def codehug_mutation():
        denied = request_guard.authorize(api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset({"mutation_intent_id", "dry_run"}),
            required=frozenset({"mutation_intent_id", "dry_run"}),
        )
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return success_response(
            api.codehug_mutation(
                principal=request_guard.principal(),
                mutation_intent_id=required_string(
                    payload, "mutation_intent_id"
                ),
                idempotency_key=require_idempotency_key(),
            ),
            status=202,
        )
