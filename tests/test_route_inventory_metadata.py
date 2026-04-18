from flask import Flask

from agent.bootstrap.route_aliases import route_alias_metadata
from devtools.export_route_inventory import build_route_inventory


def test_route_inventory_marks_alias_routes_with_canonical_path():
    app = Flask(__name__)

    def health():
        return "ok"

    def canonical_health():
        return "ok"

    app.add_url_rule("/health", endpoint="health", view_func=health)
    app.add_url_rule("/api/system/health", endpoint="system.health", view_func=canonical_health)
    app.extensions["route_inventory_metadata"] = route_alias_metadata()

    routes = build_route_inventory(app, include_methods=True, include_route_kind=True)
    by_path = {route["path"]: route for route in routes}

    assert by_path["/health"]["route_kind"] == "alias"
    assert by_path["/health"]["canonical_path"] == "/api/system/health"
    assert by_path["/health"]["methods"] == ["GET"]
    assert by_path["/api/system/health"]["route_kind"] == "canonical"

