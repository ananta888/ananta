"""Authenticated Hub API for GeoMap catalog, geometry, and joins."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any, cast

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_user_auth
from agent.common.errors import api_response
from agent.services.geomaps.contracts import GeoMapError
from agent.services.geomaps.service import GeoMapService

geomaps_bp = Blueprint("geomaps", __name__, url_prefix="/api/geomaps")


def _service() -> GeoMapService:
    service = cast(GeoMapService | None, current_app.extensions.get("geomap_service"))
    if service is None:
        raise RuntimeError("geomap_service_unavailable")
    return service


def _body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise GeoMapError("geomap_payload_invalid")
    return payload


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise GeoMapError("geomap_payload_invalid") from exc
    return parsed


def _invoke(operation: Callable[[], Any]):
    try:
        return api_response(data=operation())
    except GeoMapError as exc:
        code = 404 if exc.reason_code == "geomap_not_found" else 422
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code, "detail": exc.detail},
            code=code,
        )
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@geomaps_bp.get("/registry")
@check_user_auth
def registry():
    return _invoke(_service().catalog)


@geomaps_bp.get("/<map_id>/geometry")
@check_user_auth
def geometry(map_id: str):
    return _invoke(lambda: _service().geometry(map_id))


@geomaps_bp.post("/project")
@check_user_auth
def project():
    def operation():
        payload = _body()
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise GeoMapError("geomap_rows_invalid")
        projection = _service().project(
            map_id=str(payload.get("map_id") or ""),
            rows=rows,
            region_key=str(payload.get("region_key") or ""),
            value_key=str(payload.get("value_key") or ""),
            aggregation=str(payload.get("aggregation") or "preaggregated"),
            data_attribution=str(payload.get("data_attribution") or ""),
            minimum_match_ratio=payload.get("minimum_match_ratio"),
        )
        return projection.to_wire()

    return _invoke(operation)


@geomaps_bp.post("/export")
@check_user_auth
def export():
    def operation():
        payload = _body()
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise GeoMapError("geomap_rows_invalid")
        projection = _service().project(
            map_id=str(payload.get("map_id") or ""),
            rows=rows,
            region_key=str(payload.get("region_key") or ""),
            value_key=str(payload.get("value_key") or ""),
            aggregation=str(payload.get("aggregation") or "preaggregated"),
            data_attribution=str(payload.get("data_attribution") or ""),
            minimum_match_ratio=payload.get("minimum_match_ratio"),
        )
        artifact = _service().export(
            projection=projection,
            output_format=str(payload.get("output_format") or "svg"),
            title=str(payload.get("title") or "GeoMap")[:200],
        )
        return {
            "schema": "ananta.geomap-export-artifact.v1",
            "filename": artifact.filename,
            "media_type": artifact.media_type,
            "content_base64": base64.b64encode(artifact.content).decode("ascii"),
            "metadata": artifact.metadata,
            "publication_eligible": projection.report.publication_eligible,
        }

    return _invoke(operation)


@geomaps_bp.post("/validate-upload")
@admin_required
def validate_upload():
    def operation():
        payload = _body()
        return _service().validate_upload(
            content_base64=str(payload.get("content_base64") or ""),
            format=str(payload.get("format") or ""),
            max_bytes=_positive_int(payload.get("max_bytes"), default=2_000_000),
        )

    return _invoke(operation)


__all__ = ["geomaps_bp"]
