from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.services.geomaps.contracts import GeoMapError
from agent.services.geomaps.geometry import parse_and_validate_geojson, sanitize_svg
from agent.services.geomaps.registry import GeoMapRegistry


def _registry_fixture(tmp_path: Path, **overrides):
    asset_root = tmp_path / "assets" / "geomaps"
    asset_root.mkdir(parents=True)
    content = (
        b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"x"},'
        b'"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}}]}\n'
    )
    (asset_root / "custom.geojson").write_bytes(content)
    definition = {
        "id": "custom-map",
        "label": "Custom",
        "level": "custom",
        "format": "geojson",
        "source": "assets/geomaps/custom.geojson",
        "featureIdPath": "properties.id",
        "dataJoinKey": "custom",
        "supportedRenderers": ["echarts"],
        "bounds": [0, 0, 1, 1],
        "license": "Internal",
        "licenseUrl": "https://example.test/license",
        "attribution": "Fixture",
        "sha256": hashlib.sha256(content).hexdigest(),
        "featureCount": 1,
        "maxBytes": 1000,
        **overrides,
    }
    return content, definition


def test_builtin_registry_and_assets_are_offline_complete(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: pytest.fail("network access"))
    registry = GeoMapRegistry()

    catalog = registry.catalog()

    assert [item["id"] for item in catalog["maps"]] == [
        "world-countries",
        "world-continents",
        "europe-countries",
        "de-states",
    ]
    assert len(registry.geometry("world-countries")["features"]) == 177
    assert len(registry.geometry("world-continents")["features"]) == 6
    assert len(registry.geometry("europe-countries")["features"]) == 39
    states = registry.geometry("de-states")["features"]
    assert len(states) == 16
    assert len({feature["properties"]["id"] for feature in states}) == 16


def test_attribution_manifest_matches_packaged_assets() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "assets/geomaps/attribution.v1.json").read_text())
    assert all(source["license"] and source["attribution"] for source in manifest["sources"])
    for item in manifest["files"]:
        content = (root / "assets/geomaps" / item["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == item["sha256"]
        assert len(content) == item["size_bytes"]


@pytest.mark.parametrize(
    "mutation,reason_code",
    [
        (lambda definition: definition.pop("licenseUrl"), "geomap_registry_schema_invalid"),
        (lambda definition: definition.update(supportedRenderers=["leaflet"]), "geomap_registry_schema_invalid"),
        (lambda definition: definition.update(bounds=[1, 0, 0, 1]), "geomap_registry_bounds_invalid"),
        (lambda definition: definition.update(aliases={"wrong": "missing"}), "geomap_registry_alias_target_invalid"),
    ],
)
def test_registry_rejects_invalid_contracts(tmp_path, mutation, reason_code) -> None:
    _, definition = _registry_fixture(tmp_path)
    mutation(definition)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"schema": "ananta.geomap-registry.v1", "version": 1, "maps": [definition]}))
    with pytest.raises(GeoMapError, match=reason_code):
        GeoMapRegistry(registry_path=registry_path, root=tmp_path)


def test_registry_rejects_duplicate_map_ids(tmp_path) -> None:
    _, definition = _registry_fixture(tmp_path)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"schema": "ananta.geomap-registry.v1", "version": 1, "maps": [definition, definition]})
    )
    with pytest.raises(GeoMapError, match="geomap_registry_id_duplicate"):
        GeoMapRegistry(registry_path=registry_path, root=tmp_path)


@pytest.mark.parametrize(
    "content,reason_code",
    [
        (b'{"type":"FeatureCollection","features":[]}', "geomap_geojson_feature_count_invalid"),
        (
            b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"x"},"geometry":{"type":"Point","coordinates":[0,0]}}]}',
            "geomap_geojson_geometry_unsupported",
        ),
    ],
)
def test_geojson_rejects_invalid_shapes(content: bytes, reason_code: str) -> None:
    with pytest.raises(GeoMapError, match=reason_code):
        parse_and_validate_geojson(content, max_bytes=10_000)


@pytest.mark.parametrize(
    "svg,reason_code",
    [
        (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><path id="x"/></svg>',
            "geomap_svg_active_content_forbidden",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg"><path id="x" onclick="alert(1)"/></svg>',
            "geomap_svg_event_handler_forbidden",
        ),
        (
            b'<svg xmlns="http://www.w3.org/2000/svg"><image id="x" href="https://evil.test/x"/></svg>',
            "geomap_svg_external_reference_forbidden",
        ),
        (
            b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg><path id="x"/></svg>',
            "geomap_svg_declaration_forbidden",
        ),
    ],
)
def test_svg_sanitizer_rejects_active_or_external_content(svg: bytes, reason_code: str) -> None:
    with pytest.raises(GeoMapError, match=reason_code):
        sanitize_svg(svg)


def test_svg_sanitizer_accepts_named_local_geometry() -> None:
    sanitized = sanitize_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path id="DE-BW" d="M0 0 L1 0 L1 1 Z"/></svg>')
    assert b'name="DE-BW"' in sanitized


def test_svg_sanitizer_rejects_external_css_import() -> None:
    with pytest.raises(GeoMapError, match="geomap_svg_external_reference_forbidden"):
        sanitize_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b"<style>@import url(https://evil.test/x.css)</style>"
            b'<path id="x"/></svg>'
        )


def test_custom_svg_registry_entry_is_sanitized_before_projection(tmp_path) -> None:
    asset_root = tmp_path / "assets" / "geomaps"
    asset_root.mkdir(parents=True)
    content = b'<svg xmlns="http://www.w3.org/2000/svg"><path name="north" d="M0 0 L1 0 L1 1 Z"/></svg>'
    (asset_root / "custom.svg").write_bytes(content)
    registry_payload = {
        "schema": "ananta.geomap-registry.v1",
        "version": 1,
        "maps": [
            {
                "id": "custom-map",
                "label": "Custom",
                "level": "custom",
                "format": "svg",
                "source": "assets/geomaps/custom.svg",
                "featureIdPath": "properties.id",
                "dataJoinKey": "custom",
                "supportedRenderers": ["echarts"],
                "bounds": [0, 0, 1, 1],
                "license": "Internal",
                "licenseUrl": "https://example.test/license",
                "attribution": "Fixture",
                "sha256": hashlib.sha256(content).hexdigest(),
                "featureCount": 1,
                "maxBytes": 1000,
            }
        ],
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry_payload))
    registry = GeoMapRegistry(registry_path=registry_path, root=tmp_path)
    geometry = registry.geometry("custom-map")
    assert geometry["type"] == "AnantaSvgMap"
    assert geometry["features"] == [{"properties": {"id": "north", "name": "north"}}]
