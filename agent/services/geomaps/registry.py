"""Versioned, immutable GeoMap registry loader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

from .contracts import GeoMapError
from .geometry import parse_and_validate_geojson, sanitize_svg, svg_region_ids

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = ROOT / "config" / "geomaps" / "registry.v1.json"
DEFAULT_SCHEMA = ROOT / "schemas" / "geomaps" / "registry.v1.json"


class GeoMapRegistry:
    def __init__(self, *, registry_path: Path = DEFAULT_REGISTRY, root: Path = ROOT) -> None:
        self._root = root.resolve()
        self._registry_path = registry_path.resolve()
        self._payload = self._load()
        self._maps = {item["id"]: item for item in self._payload["maps"]}

    def _load(self) -> dict[str, Any]:
        payload = cast(dict[str, Any], json.loads(self._registry_path.read_text(encoding="utf-8")))
        schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        if errors:
            raise GeoMapError("geomap_registry_schema_invalid", errors[0].message)
        identifiers = [item["id"] for item in payload["maps"]]
        if len(set(identifiers)) != len(identifiers):
            raise GeoMapError("geomap_registry_id_duplicate")
        for item in payload["maps"]:
            self._validate_asset(item)
        return cast(dict[str, Any], payload)

    def _asset_path(self, item: dict[str, Any]) -> Path:
        path = cast(Path, (self._root / item["source"]).resolve())
        try:
            path.relative_to((self._root / "assets" / "geomaps").resolve())
        except ValueError as exc:
            raise GeoMapError("geomap_asset_path_invalid") from exc
        return path

    def _validate_asset(self, item: dict[str, Any]) -> None:
        min_x, min_y, max_x, max_y = (float(value) for value in item["bounds"])
        if min_x >= max_x or min_y >= max_y:
            raise GeoMapError("geomap_registry_bounds_invalid", item["id"])
        path = self._asset_path(item)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise GeoMapError("geomap_asset_unavailable", item["id"]) from exc
        if len(content) > item["maxBytes"]:
            raise GeoMapError("geomap_asset_budget_exceeded", item["id"])
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise GeoMapError("geomap_asset_digest_mismatch", item["id"])
        if item["format"] == "geojson":
            payload = parse_and_validate_geojson(content, max_bytes=item["maxBytes"])
            if len(payload["features"]) != item["featureCount"]:
                raise GeoMapError("geomap_asset_feature_count_mismatch", item["id"])
            identifiers = tuple(str(feature["properties"]["id"]) for feature in payload["features"])
        else:
            identifiers = svg_region_ids(content, max_bytes=item["maxBytes"])
            if len(identifiers) != item["featureCount"]:
                raise GeoMapError("geomap_asset_feature_count_mismatch", item["id"])
        unknown_aliases = sorted(set((item.get("aliases") or {}).values()) - set(identifiers))
        if unknown_aliases:
            raise GeoMapError("geomap_registry_alias_target_invalid", ",".join(unknown_aliases))

    @property
    def version(self) -> int:
        return int(self._payload["version"])

    def catalog(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(json.dumps(self._payload)))

    def get(self, map_id: str) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], json.loads(json.dumps(self._maps[map_id])))
        except KeyError as exc:
            raise GeoMapError("geomap_not_found", map_id) from exc

    def geometry(self, map_id: str) -> dict[str, Any]:
        item = self.get(map_id)
        content = self._asset_path(item).read_bytes()
        if item["format"] == "geojson":
            return parse_and_validate_geojson(content, max_bytes=int(item["maxBytes"]))
        sanitized = sanitize_svg(content, max_bytes=int(item["maxBytes"]))
        return {
            "type": "AnantaSvgMap",
            "svg": sanitized.decode("utf-8"),
            "features": [
                {"properties": {"id": identifier, "name": identifier}}
                for identifier in svg_region_ids(sanitized, max_bytes=int(item["maxBytes"]))
            ],
        }
