from __future__ import annotations

import base64


def test_geomap_registry_requires_authentication(client) -> None:
    response = client.get("/api/geomaps/registry")
    assert response.status_code in {401, 403}


def test_geomap_api_returns_catalog_geometry_and_join(client, admin_auth_header) -> None:
    catalog = client.get("/api/geomaps/registry", headers=admin_auth_header)
    assert catalog.status_code == 200
    assert catalog.get_json()["data"]["schema"] == "ananta.geomap-registry.v1"

    geometry = client.get("/api/geomaps/de-states/geometry", headers=admin_auth_header)
    assert geometry.status_code == 200
    assert len(geometry.get_json()["data"]["features"]) == 16

    projection = client.post(
        "/api/geomaps/project",
        headers=admin_auth_header,
        json={
            "map_id": "de-states",
            "rows": [{"region": "DE-BE", "value": 7}],
            "region_key": "region",
            "value_key": "value",
            "aggregation": "sum",
            "minimum_match_ratio": 1,
            "data_attribution": "API fixture",
        },
    )
    assert projection.status_code == 200
    payload = projection.get_json()["data"]
    assert payload["values"][0]["region_id"] == "DE-BE"
    assert payload["report"]["publication_eligible"] is True


def test_geomap_api_returns_bounded_duplicate_error(client, admin_auth_header) -> None:
    response = client.post(
        "/api/geomaps/project",
        headers=admin_auth_header,
        json={
            "map_id": "de-states",
            "rows": [{"region": "DE-BE", "value": 1}, {"region": "DE-BE", "value": 2}],
            "region_key": "region",
            "value_key": "value",
            "aggregation": "preaggregated",
        },
    )
    assert response.status_code == 422
    assert response.get_json()["data"]["reason_code"] == "geomap_duplicate_aggregation_required"


def test_geomap_export_is_headless_and_contains_attribution(client, admin_auth_header) -> None:
    response = client.post(
        "/api/geomaps/export",
        headers=admin_auth_header,
        json={
            "map_id": "de-states",
            "rows": [{"region": "DE-BE", "value": 1}],
            "region_key": "region",
            "value_key": "value",
            "aggregation": "sum",
            "minimum_match_ratio": 1,
            "data_attribution": "API export fixture",
            "output_format": "svg",
            "title": "Bundesländer",
        },
    )
    assert response.status_code == 200
    artifact = response.get_json()["data"]
    assert artifact["schema"] == "ananta.geomap-export-artifact.v1"
    assert artifact["metadata"]["data_attribution"] == "API export fixture"
    assert artifact["publication_eligible"] is True


def test_geomap_export_enforces_hub_publication_policy(client, admin_auth_header) -> None:
    response = client.post(
        "/api/geomaps/export",
        headers=admin_auth_header,
        json={
            "map_id": "de-states",
            "rows": [{"region": "DE-BE", "value": 1}],
            "region_key": "region",
            "value_key": "value",
            "aggregation": "sum",
            "minimum_match_ratio": 1,
            "output_format": "svg",
        },
    )
    assert response.status_code == 422
    assert response.get_json()["data"]["reason_code"] == "geomap_publication_blocked"


def test_admin_upload_validation_sanitizes_without_persisting(client, admin_auth_header) -> None:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path id="area-a" d="M0 0 L1 0 L1 1 Z"/></svg>'
    response = client.post(
        "/api/geomaps/validate-upload",
        headers=admin_auth_header,
        json={"format": "svg", "max_bytes": 1000, "content_base64": base64.b64encode(svg).decode()},
    )
    assert response.status_code == 200
    report = response.get_json()["data"]
    assert report["feature_ids"] == ["area-a"]
    assert report["feature_count"] == 1


def test_admin_upload_validation_rejects_non_numeric_budget(client, admin_auth_header) -> None:
    response = client.post(
        "/api/geomaps/validate-upload",
        headers=admin_auth_header,
        json={"format": "svg", "max_bytes": "unbounded", "content_base64": ""},
    )
    assert response.status_code == 422
    assert response.get_json()["data"]["reason_code"] == "geomap_payload_invalid"
