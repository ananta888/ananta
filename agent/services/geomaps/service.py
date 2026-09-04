"""Hub-owned GeoMap facade coordinating registry, joins, and exports."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import GeoMapError, GeoMapExportArtifact, GeoMapProjection
from .export import GeoMapExporter, PortableGeoMapExporter
from .geometry import parse_and_validate_geojson, sanitize_svg, svg_region_ids
from .join import RegionJoinService
from .registry import GeoMapRegistry


class GeoMapService:
    def __init__(
        self,
        *,
        registry: GeoMapRegistry | None = None,
        join_service: RegionJoinService | None = None,
        exporter: GeoMapExporter | None = None,
    ) -> None:
        self._registry = registry or GeoMapRegistry()
        self._join_service = join_service or RegionJoinService()
        self._exporter = exporter or PortableGeoMapExporter()

    def catalog(self) -> dict[str, Any]:
        return self._registry.catalog()

    def geometry(self, map_id: str) -> dict[str, Any]:
        return self._registry.geometry(map_id)

    def project(
        self,
        *,
        map_id: str,
        rows: Sequence[Mapping[str, Any]],
        region_key: str,
        value_key: str,
        aggregation: str,
        data_attribution: str = "",
        minimum_match_ratio: float | None = None,
    ) -> GeoMapProjection:
        definition = self._registry.get(map_id)
        return self._join_service.join(
            registry_version=self._registry.version,
            map_definition=definition,
            geometry=self._registry.geometry(map_id),
            rows=rows,
            region_key=region_key,
            value_key=value_key,
            aggregation=aggregation,
            data_attribution=data_attribution,
            minimum_match_ratio=minimum_match_ratio,
        )

    def export(
        self,
        *,
        projection: GeoMapProjection,
        output_format: str,
        title: str,
    ) -> GeoMapExportArtifact:
        if not projection.report.publication_eligible:
            raise GeoMapError("geomap_publication_blocked", ",".join(projection.report.reason_codes))
        if self._registry.get(projection.map_id)["format"] != "geojson":
            raise GeoMapError("geomap_export_renderer_unsupported")
        return self._exporter.render(
            projection=projection,
            geometry=self._registry.geometry(projection.map_id),
            output_format=output_format,
            title=title,
        )

    def validate_upload(self, *, content_base64: str, format: str, max_bytes: int) -> dict[str, Any]:
        if max_bytes < 1 or max_bytes > 10_000_000:
            raise GeoMapError("geomap_upload_budget_invalid")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise GeoMapError("geomap_upload_base64_invalid") from exc
        if format == "geojson":
            payload = parse_and_validate_geojson(content, max_bytes=max_bytes)
            identifiers = tuple(str(item["properties"]["id"]) for item in payload["features"])
            sanitized = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        elif format == "svg":
            sanitized = sanitize_svg(content, max_bytes=max_bytes)
            identifiers = svg_region_ids(sanitized, max_bytes=max_bytes)
        else:
            raise GeoMapError("geomap_upload_format_unsupported")
        return {
            "schema": "ananta.geomap-upload-validation.v1",
            "format": format,
            "sha256": hashlib.sha256(sanitized).hexdigest(),
            "size_bytes": len(sanitized),
            "feature_count": len(identifiers),
            "feature_ids": list(identifiers),
            "sanitized_content_base64": base64.b64encode(sanitized).decode("ascii"),
        }
