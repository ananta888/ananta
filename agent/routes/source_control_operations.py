"""Authenticated operational surface for the canonical Source Control Center."""

from __future__ import annotations

from flask import Blueprint, jsonify

from agent.auth import admin_required, check_auth
from agent.services.source_control_observability import (
    SourceControlHealthMonitor,
)


def create_source_control_operations_blueprint(
    health: SourceControlHealthMonitor,
) -> Blueprint:
    blueprint = Blueprint(
        "source_control_operations",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.get("/health")
    @check_auth
    @admin_required
    def source_control_health():
        report = health.snapshot()
        return jsonify(
            {
                "status": "success",
                "data": report.to_dict(),
            }
        )

    return blueprint


__all__ = ["create_source_control_operations_blueprint"]
