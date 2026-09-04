#!/usr/bin/env python3
"""Build deterministic, offline GeoMap assets from pinned upstream revisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "geomaps"
NATURAL_EARTH_REVISION = "f1890d9f152c896d250a77557a5751a93d494776"
GERMANY_REVISION = "4090d4e1f89c1184b436b3d9ccaf332b4c5b43d2"
NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    f"{NATURAL_EARTH_REVISION}/geojson/ne_110m_admin_0_countries.geojson"
)
GERMANY_URL = (
    "https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/"
    f"{GERMANY_REVISION}/2_bundeslaender/4_niedrig.geo.json"
)
GERMANY_LICENSE_URL = f"https://raw.githubusercontent.com/isellsoap/deutschlandGeoJSON/{GERMANY_REVISION}/LICENSE.md"


def _download_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - pinned HTTPS source
        return cast(dict[str, Any], json.load(response))


def _country_features(source: dict[str, Any]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in source.get("features", []):
        properties = raw.get("properties") or {}
        region_id = str(properties.get("ADM0_A3") or "").upper()
        if len(region_id) != 3 or region_id == "-99" or region_id in seen:
            continue
        seen.add(region_id)
        features.append(
            {
                "type": "Feature",
                "id": region_id,
                "properties": {
                    "id": region_id,
                    "name": str(properties.get("NAME_LONG") or properties.get("NAME") or region_id),
                    "continent": str(properties.get("CONTINENT") or "Unknown"),
                },
                "geometry": raw["geometry"],
            }
        )
    return sorted(features, key=lambda item: item["id"])


def _continent_features(countries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    polygons: dict[str, list[Any]] = defaultdict(list)
    for feature in countries:
        continent = feature["properties"]["continent"]
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            polygons[continent].append(geometry["coordinates"])
        elif geometry["type"] == "MultiPolygon":
            polygons[continent].extend(geometry["coordinates"])
    return [
        {
            "type": "Feature",
            "id": continent.lower().replace(" ", "-"),
            "properties": {
                "id": continent.lower().replace(" ", "-"),
                "name": continent,
            },
            "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
        }
        for continent, coordinates in sorted(polygons.items())
        if continent not in {"Antarctica", "Seven seas (open ocean)", "Unknown"}
    ]


def _germany_features(source: dict[str, Any]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for raw in source.get("features", []):
        properties = raw.get("properties") or {}
        region_id = str(properties.get("id") or "").upper()
        features.append(
            {
                "type": "Feature",
                "id": region_id,
                "properties": {"id": region_id, "name": str(properties.get("name") or region_id)},
                "geometry": raw["geometry"],
            }
        )
    return sorted(features, key=lambda item: item["id"])


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.write_bytes(encoded)
    return {
        "path": path.name,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "feature_count": len(payload.get("features") or []),
    }


def build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    countries = _country_features(_download_json(NATURAL_EARTH_URL))
    europe = [feature for feature in countries if feature["properties"]["continent"] == "Europe"]
    states = _germany_features(_download_json(GERMANY_URL))
    expected_states = {
        "DE-BB",
        "DE-BE",
        "DE-BW",
        "DE-BY",
        "DE-HB",
        "DE-HE",
        "DE-HH",
        "DE-MV",
        "DE-NI",
        "DE-NW",
        "DE-RP",
        "DE-SH",
        "DE-SL",
        "DE-SN",
        "DE-ST",
        "DE-TH",
    }
    actual_states = {feature["id"] for feature in states}
    if actual_states != expected_states:
        raise RuntimeError(f"unexpected German state identifiers: {sorted(actual_states)}")

    files = [
        _write_json(OUTPUT / "world-countries.geojson", _collection(countries)),
        _write_json(OUTPUT / "world-continents.geojson", _collection(_continent_features(countries))),
        _write_json(OUTPUT / "europe-countries.geojson", _collection(europe)),
        _write_json(OUTPUT / "de-states.geojson", _collection(states)),
    ]
    attribution = {
        "schema": "ananta.geomap-attribution.v1",
        "generated_by": "scripts/build_geomap_assets.py",
        "sources": [
            {
                "id": "natural-earth-admin-0-110m",
                "revision": NATURAL_EARTH_REVISION,
                "origin": NATURAL_EARTH_URL,
                "license": "Public Domain",
                "license_url": "https://www.naturalearthdata.com/about/terms-of-use/",
                "attribution": "Made with Natural Earth.",
            },
            {
                "id": "deutschland-geojson-states-low",
                "revision": GERMANY_REVISION,
                "origin": GERMANY_URL,
                "license": "The Unlicense",
                "license_url": GERMANY_LICENSE_URL,
                "attribution": "Administrative boundaries derived from deutschlandGeoJSON / GIS-DATA.",
            },
        ],
        "files": files,
    }
    _write_json(OUTPUT / "attribution.v1.json", attribution)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="confirm the pinned network refresh")
    args = parser.parse_args()
    if not args.refresh:
        parser.error("--refresh is required; product runtime never downloads map data")
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
