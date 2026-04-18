from dataclasses import dataclass
from typing import Callable

from flask import Flask


@dataclass(frozen=True)
class RouteAlias:
    path: str
    canonical_path: str
    view_import: str
    methods: tuple[str, ...] = ("GET",)

    @property
    def view_name(self) -> str:
        return self.view_import.rsplit(".", 1)[-1]


SYSTEM_ROUTE_ALIASES: tuple[RouteAlias, ...] = (
    RouteAlias("/health", "/api/system/health", "agent.routes.system.health"),
    RouteAlias("/ready", "/api/system/ready", "agent.routes.system.readiness_check"),
    RouteAlias("/metrics", "/api/system/metrics", "agent.routes.system.metrics"),
    RouteAlias("/stats", "/api/system/stats", "agent.routes.system.system_stats"),
    RouteAlias("/stats/history", "/api/system/stats/history", "agent.routes.system.get_stats_history"),
    RouteAlias("/events", "/api/system/events", "agent.routes.system.stream_system_events"),
    RouteAlias("/agents", "/api/system/agents", "agent.routes.system.list_agents"),
    RouteAlias("/audit-logs", "/api/system/audit-logs", "agent.routes.system.get_audit_logs"),
    RouteAlias(
        "/audit/analyze",
        "/api/system/audit/analyze",
        "agent.routes.system.analyze_audit_logs",
        methods=("POST",),
    ),
    RouteAlias("/register", "/api/system/register", "agent.routes.system.register_agent", methods=("POST",)),
)


def route_alias_metadata() -> dict[str, dict[str, object]]:
    return {
        alias.path: {
            "route_kind": "alias",
            "canonical_path": alias.canonical_path,
            "view": alias.view_import,
            "methods": list(alias.methods),
        }
        for alias in SYSTEM_ROUTE_ALIASES
    }


def register_route_aliases(app: Flask) -> None:
    for alias in SYSTEM_ROUTE_ALIASES:
        app.add_url_rule(alias.path, view_func=_import_view(alias.view_import), methods=list(alias.methods))
    app.extensions["route_inventory_metadata"] = {
        **dict(app.extensions.get("route_inventory_metadata") or {}),
        **route_alias_metadata(),
    }


def _import_view(import_path: str) -> Callable:
    module_name, attr = import_path.rsplit(".", 1)
    module = __import__(module_name, fromlist=[attr])
    return getattr(module, attr)

