"""Bounded GeoJSON and SVG validation for built-in and custom maps."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any, cast

from .contracts import GeoMapError

ALLOWED_GEOMETRIES = {"Polygon", "MultiPolygon"}
FORBIDDEN_SVG_ELEMENTS = {"script", "foreignObject", "iframe", "object", "embed", "audio", "video"}
URL_PATTERN = re.compile(r"url\s*\(\s*(['\"]?)(?!#)[^)]+\1\s*\)", re.IGNORECASE)


def _coordinates(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        raise GeoMapError("geomap_coordinate_invalid")
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if not isinstance(value, list):
        raise GeoMapError("geomap_coordinate_invalid")
    for item in value:
        yield from _coordinates(item)


def validate_geojson(payload: dict[str, Any], *, max_features: int = 10_000) -> tuple[str, ...]:
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise GeoMapError("geomap_geojson_feature_collection_required")
    features = payload["features"]
    if not features or len(features) > max_features:
        raise GeoMapError("geomap_geojson_feature_count_invalid")
    identifiers: list[str] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise GeoMapError("geomap_geojson_feature_invalid")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ALLOWED_GEOMETRIES:
            raise GeoMapError("geomap_geojson_geometry_unsupported")
        coordinates = list(_coordinates(geometry.get("coordinates")))
        if not coordinates or any(value != value or abs(value) > 1_000_000 for value in coordinates):
            raise GeoMapError("geomap_geojson_coordinate_invalid")
        properties = feature.get("properties") or {}
        identifier = str(properties.get("id") or "").strip()
        if not identifier:
            raise GeoMapError("geomap_geojson_feature_id_missing")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise GeoMapError("geomap_geojson_feature_id_duplicate")
    return tuple(identifiers)


def parse_and_validate_geojson(content: bytes, *, max_bytes: int, max_features: int = 10_000) -> dict[str, Any]:
    if not content or len(content) > max_bytes:
        raise GeoMapError("geomap_file_size_invalid")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GeoMapError("geomap_geojson_invalid") from exc
    if not isinstance(payload, dict):
        raise GeoMapError("geomap_geojson_invalid")
    validate_geojson(payload, max_features=max_features)
    return payload


def sanitize_svg(content: bytes, *, max_bytes: int = 2_000_000) -> bytes:
    if not content or len(content) > max_bytes:
        raise GeoMapError("geomap_file_size_invalid")
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise GeoMapError("geomap_svg_declaration_forbidden")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise GeoMapError("geomap_svg_invalid") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise GeoMapError("geomap_svg_root_required")
    named_regions = 0
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in FORBIDDEN_SVG_ELEMENTS:
            raise GeoMapError("geomap_svg_active_content_forbidden", tag)
        if tag in {"path", "polygon", "rect", "circle", "ellipse", "g"} and (element.get("name") or element.get("id")):
            named_regions += 1
            if element.get("id") and not element.get("name"):
                element.set("name", str(element.get("id")))
        if tag == "style" and element.text:
            style = element.text.strip().lower()
            if "@import" in style or "javascript:" in style or URL_PATTERN.search(style):
                raise GeoMapError("geomap_svg_external_reference_forbidden", "style")
        for attribute, value in element.attrib.items():
            local_attribute = attribute.rsplit("}", 1)[-1].lower()
            normalized_value = value.strip().lower()
            if local_attribute.startswith("on"):
                raise GeoMapError("geomap_svg_event_handler_forbidden", attribute)
            if local_attribute in {"href", "src"} and normalized_value and not normalized_value.startswith("#"):
                raise GeoMapError("geomap_svg_external_reference_forbidden", attribute)
            if "javascript:" in normalized_value or URL_PATTERN.search(value):
                raise GeoMapError("geomap_svg_external_reference_forbidden", attribute)
    if not named_regions:
        raise GeoMapError("geomap_svg_named_region_required")
    return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def svg_region_ids(content: bytes, *, max_bytes: int = 2_000_000) -> tuple[str, ...]:
    sanitized = sanitize_svg(content, max_bytes=max_bytes)
    root = ET.fromstring(sanitized)
    identifiers = [
        str(element.get("name") or element.get("id"))
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"path", "polygon", "rect", "circle", "ellipse", "g"}
        and (element.get("name") or element.get("id"))
    ]
    if len(set(identifiers)) != len(identifiers):
        raise GeoMapError("geomap_svg_region_id_duplicate")
    return tuple(identifiers)
