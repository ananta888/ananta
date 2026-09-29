"""Visual process catalog endpoints: presets, skill profiles, task kinds, node definitions."""

from __future__ import annotations

import hashlib
import json

from flask import (
    Response,
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.config import settings
from agent.routes.visual_process_blueprint import vp_bp
from agent.services.alias_catalog import default_alias_registry
from agent.services.alias_registry import ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET
from agent.visual_process.node_definitions import (
    NODE_REGISTRY_VERSION,
    get_node_definition,
    list_node_definitions,
)
from agent.visual_process.presets import (
    get_preset,
    list_presets,
)
from agent.visual_process.skill_profiles import get_skill_profile_registry
from agent.visual_process.task_kind_registry import list_task_kinds

# ── Presets ───────────────────────────────────────────────────────────────────


@vp_bp.get("/presets")
def get_presets():
    """List presets, each carrying the names a person can read and search by.

    The technical id stays the identity; display_name and aliases are added
    beside it so a client never has to invent a label of its own.
    """

    registry = default_alias_registry()
    return (
        jsonify(
            [
                {
                    **preset,
                    **registry.describe(
                        namespace=ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
                        canonical_id=str(preset.get("id") or ""),
                    ),
                    "id": preset.get("id"),
                }
                for preset in list_presets()
            ]
        ),
        200,
    )


@vp_bp.get("/presets/<preset_id>")
def get_preset_by_id(preset_id: str):
    preset = get_preset(preset_id)
    if not preset:
        return jsonify({"error": "not_found"}), 404
    return jsonify(preset.model_dump()), 200


# ── Skill profiles (VPAD-005 agent library) ───────────────────────────────────


@vp_bp.get("/skill-profiles")
@check_user_auth
def skill_profiles():
    reg = get_skill_profile_registry()
    return jsonify(reg.as_library()), 200


@vp_bp.get("/skill-profiles/<profile_id>")
@check_user_auth
def skill_profile_detail(profile_id: str):
    reg = get_skill_profile_registry()
    p = reg.get(profile_id)
    if not p:
        return jsonify({"error": "not_found"}), 404
    return jsonify(p.as_dict()), 200


# ── Task kinds (VPWRK-001) ────────────────────────────────────────────────────


@vp_bp.get("/task-kinds")
def task_kinds():
    return jsonify(list_task_kinds()), 200


def _node_definition_registry_response():
    if not settings.visual_process_registry_inspector_enabled:
        return jsonify(
            {
                "error": "visual_process_registry_inspector_disabled",
                "error_code": "visual_process_registry_inspector_disabled",
            }
        ), 404
    definitions = list_node_definitions()
    canonical = json.dumps(
        {"registry_version": NODE_REGISTRY_VERSION, "definitions": definitions},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    registry_hash = hashlib.sha256(canonical).hexdigest()
    etag = f'"{registry_hash}"'
    if str(request.headers.get("If-None-Match") or "").strip() == etag:
        response = Response(status=304)
    else:
        response = jsonify(
            {
                "schema": "ananta.visual_process.node_definition_registry.v1",
                "registry_version": NODE_REGISTRY_VERSION,
                "registry_hash": registry_hash,
                "definitions": definitions,
            }
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=300, must-revalidate"
    return response


@vp_bp.get("/node-definitions")
@vp_bp.get("/v1/node-definitions")
@check_user_auth
def node_definitions():
    return _node_definition_registry_response()


@vp_bp.get("/node-definitions/<kind>")
@vp_bp.get("/v1/node-definitions/<kind>")
@check_user_auth
def node_definition(kind: str):
    if not settings.visual_process_registry_inspector_enabled:
        return jsonify(
            {
                "error": "visual_process_registry_inspector_disabled",
                "error_code": "visual_process_registry_inspector_disabled",
            }
        ), 404
    definition = get_node_definition(kind)
    if definition is None:
        return jsonify({"error": "node_kind_not_found", "error_code": "node_kind_not_found"}), 404
    return jsonify(definition), 200
