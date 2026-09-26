"""Internal Worker endpoints of Hub-delegated CodeCompass layer jobs."""

from __future__ import annotations

import gzip
import json

from flask import Blueprint, Response, current_app, request

from agent.auth import check_registered_worker_auth
from agent.common.errors import api_response
from ananta_contracts.codecompass_layer_job import (
    CONTENT_PATH,
    JOB_PATH,
    LAYER_MEDIA_TYPE,
    LAYER_PATH,
    LAYER_UPLOAD_MAX_BYTES,
    WORKER_SCOPE,
)

codecompass_layer_jobs_bp = Blueprint("codecompass_layer_jobs", __name__)


def _gateway():
    return current_app.extensions.get("codecompass_layer_job_gateway")


def _error(code: str, status: int):
    return api_response(status="error", message=code, data={"error": code}, code=status)


def _flask_path(template: str) -> str:
    return template.replace("{task_id}", "<task_id>")


@codecompass_layer_jobs_bp.route(_flask_path(JOB_PATH), methods=["GET"])
@check_registered_worker_auth(scope=WORKER_SCOPE)
def layer_job_spec(task_id: str):
    from agent.services.codecompass_layer_job_gateway import LayerJobNotFound

    gateway = _gateway()
    if gateway is None:
        return _error("codecompass_layers_disabled", 404)
    try:
        return api_response(gateway.spec(task_id))
    except (LayerJobNotFound, ValueError) as error:
        return _error(str(error), 404)


@codecompass_layer_jobs_bp.route(_flask_path(CONTENT_PATH), methods=["GET"])
@check_registered_worker_auth(scope=WORKER_SCOPE)
def layer_job_content(task_id: str):
    from agent.services.codecompass_layer_job_gateway import LayerJobNotFound

    gateway = _gateway()
    if gateway is None:
        return _error("codecompass_layers_disabled", 404)
    try:
        part = int(request.args.get("part", "0"))
        payload = gateway.content_part(task_id, part)
    except (LayerJobNotFound, ValueError) as error:
        return _error(str(error), 404)
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return Response(body, mimetype="application/json", headers={"Content-Encoding": "gzip"})


@codecompass_layer_jobs_bp.route(_flask_path(LAYER_PATH), methods=["PUT"])
@check_registered_worker_auth(scope=WORKER_SCOPE)
def layer_job_upload(task_id: str):
    from agent.services.codecompass_layer_job_gateway import LayerJobNotFound

    gateway = _gateway()
    if gateway is None:
        return _error("codecompass_layers_disabled", 404)
    if request.mimetype != LAYER_MEDIA_TYPE:
        return _error("codecompass_layer_media_type_invalid", 415)
    length = request.content_length
    if length is None or not 0 < length <= LAYER_UPLOAD_MAX_BYTES:
        return _error("codecompass_layer_upload_size_invalid", 413)
    try:
        return api_response(gateway.receive_layer(task_id, request.get_data(cache=False)))
    except LayerJobNotFound as error:
        return _error(str(error), 404)
    except (ValueError, OSError) as error:
        return _error(str(error)[:160] or "codecompass_layer_upload_invalid", 422)
