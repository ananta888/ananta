"""Plotly adapter behind a small optional renderer port."""

from __future__ import annotations

import html
import importlib
import io
import json
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Protocol

from .contracts import GeoMapError, GeoMapExportArtifact, GeoMapProjection


class GeoMapExporter(Protocol):
    def render(
        self,
        *,
        projection: GeoMapProjection,
        geometry: dict[str, Any],
        output_format: str,
        title: str,
    ) -> GeoMapExportArtifact: ...


class PortableGeoMapExporter:
    """Dependency-light offline exporter for SVG, PNG, PDF, and HTML."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def render(
        self,
        *,
        projection: GeoMapProjection,
        geometry: dict[str, Any],
        output_format: str,
        title: str,
    ) -> GeoMapExportArtifact:
        if output_format not in {"html", "svg", "png", "pdf"}:
            raise GeoMapError("geomap_export_format_unsupported")
        metadata = self._metadata(projection)
        svg = self._svg(projection, geometry, title, metadata)
        if output_format == "svg":
            content, media_type = svg, "image/svg+xml"
        elif output_format == "html":
            content = (
                '<!doctype html><html lang="de"><head><meta charset="utf-8">'
                f"<title>{html.escape(title)}</title><style>body{{font-family:sans-serif}}"
                "path:hover{stroke:#111;stroke-width:1.5}</style></head><body>" + svg.decode() + "</body></html>"
            ).encode()
            media_type = "text/html; charset=utf-8"
        else:
            content = self._raster(projection, geometry, title, output_format)
            media_type = "image/png" if output_format == "png" else "application/pdf"
        return GeoMapExportArtifact(
            filename=f"{projection.map_id}.{output_format}",
            media_type=media_type,
            content=content,
            metadata=metadata,
        )

    def _metadata(self, projection: GeoMapProjection) -> dict[str, Any]:
        return {
            "schema": "ananta.geomap-export-metadata.v1",
            "created_at": self._clock().astimezone(timezone.utc).isoformat(),
            "map_id": projection.map_id,
            "registry_version": projection.registry_version,
            "aggregation": projection.aggregation,
            "map_attribution": projection.map_attribution,
            "data_attribution": projection.data_attribution,
            "join_report": projection.to_wire()["report"],
        }

    @staticmethod
    def _rings(feature: dict[str, Any]) -> Iterable[list[list[float]]]:
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            yield from geometry["coordinates"][:1]
        else:
            for polygon in geometry["coordinates"]:
                yield from polygon[:1]

    def _projector(
        self,
        geometry: dict[str, Any],
        *,
        width: int,
        height: int,
        padding: int = 20,
    ) -> Callable[[list[float]], tuple[float, float]]:
        coordinates = [point for feature in geometry["features"] for ring in self._rings(feature) for point in ring]
        if not coordinates:
            raise GeoMapError("geomap_geometry_empty")
        min_x = min(float(point[0]) for point in coordinates)
        max_x = max(float(point[0]) for point in coordinates)
        min_y = min(float(point[1]) for point in coordinates)
        max_y = max(float(point[1]) for point in coordinates)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        available_width = max(width - (2 * padding), 1)
        available_height = max(height - (2 * padding), 1)
        scale = min(available_width / span_x, available_height / span_y)
        offset_x = (width - (span_x * scale)) / 2
        offset_y = (height - (span_y * scale)) / 2

        def project(point: list[float]) -> tuple[float, float]:
            return (
                offset_x + ((float(point[0]) - min_x) * scale),
                offset_y + ((max_y - float(point[1])) * scale),
            )

        return project

    @staticmethod
    def _color(value: float | None, minimum: float, maximum: float) -> str:
        if value is None:
            return "#d9dee8"
        ratio = 0.5 if maximum == minimum else max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
        red = round(235 - 190 * ratio)
        green = round(244 - 80 * ratio)
        blue = round(255 - 40 * ratio)
        return f"#{red:02x}{green:02x}{blue:02x}"

    def _svg(
        self,
        projection: GeoMapProjection,
        geometry: dict[str, Any],
        title: str,
        metadata: dict[str, Any],
    ) -> bytes:
        width, map_height, footer = 1200, 620, 60
        lookup = {item.region_id: item for item in projection.values}
        numeric = [item.value for item in projection.values]
        minimum, maximum = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
        project = self._projector(geometry, width=width, height=map_height)
        paths: list[str] = []
        for feature in geometry["features"]:
            region_id = str(feature["properties"]["id"])
            item = lookup.get(region_id)
            segments: list[str] = []
            for ring in self._rings(feature):
                points = [project(point) for point in ring]
                encoded = " ".join(
                    f"{'M' if index == 0 else 'L'}{x:.2f},{y:.2f}" for index, (x, y) in enumerate(points)
                )
                if encoded:
                    segments.append(encoded + " Z")
            name = str(feature["properties"].get("name") or region_id)
            status = f"{item.value:g}" if item else "Keine Daten"
            paths.append(
                f'<path d="{" ".join(segments)}" fill="{self._color(item.value if item else None, minimum, maximum)}" '
                f'stroke="#667085" stroke-width="0.45" data-region="{html.escape(region_id)}">'
                f"<title>{html.escape(name)}: {html.escape(status)}</title></path>"
            )
        attribution = " · ".join(filter(None, [projection.data_attribution, projection.map_attribution]))
        metadata_json = html.escape(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")))
        document = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {map_height + footer}" '
            f'role="img" aria-label="{html.escape(title)}"><title>{html.escape(title)}</title>'
            f'<metadata>{metadata_json}</metadata><rect width="100%" height="100%" fill="white"/>'
            + "".join(paths)
            + f'<text x="12" y="{map_height + 35}" font-family="sans-serif" font-size="16">'
            + f"{html.escape(attribution)}</text></svg>"
        )
        return document.encode()

    def _raster(
        self,
        projection: GeoMapProjection,
        geometry: dict[str, Any],
        title: str,
        output_format: str,
    ) -> bytes:
        try:
            image_module = importlib.import_module("PIL.Image")
            image_color = importlib.import_module("PIL.ImageColor")
            image_draw = importlib.import_module("PIL.ImageDraw")
        except ImportError as exc:
            raise GeoMapError("geomap_pillow_unavailable") from exc
        width, map_height, footer = 1200, 620, 80
        image = image_module.new("RGB", (width, map_height + footer), "white")
        draw = image_draw.Draw(image)
        lookup = {item.region_id: item for item in projection.values}
        numeric = [item.value for item in projection.values]
        minimum, maximum = (min(numeric), max(numeric)) if numeric else (0.0, 1.0)
        project = self._projector(geometry, width=width, height=map_height)
        for feature in geometry["features"]:
            item = lookup.get(str(feature["properties"]["id"]))
            color = image_color.getrgb(self._color(item.value if item else None, minimum, maximum))
            for ring in self._rings(feature):
                points = [project(point) for point in ring]
                if len(points) >= 3:
                    draw.polygon(points, fill=color, outline="#667085", width=1)
        attribution = " · ".join(filter(None, [projection.data_attribution, projection.map_attribution]))
        draw.text((12, map_height + 10), title, fill="black")
        draw.text((12, map_height + 35), attribution[:180], fill="#344054")
        output = io.BytesIO()
        image.save(output, format="PNG" if output_format == "png" else "PDF")
        return output.getvalue()


class PlotlyGeoMapExporter:
    """Lazy optional Plotly adapter; importing the Hub never imports Plotly."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def render(
        self,
        *,
        projection: GeoMapProjection,
        geometry: dict[str, Any],
        output_format: str,
        title: str,
    ) -> GeoMapExportArtifact:
        if output_format not in {"html", "svg", "png", "pdf"}:
            raise GeoMapError("geomap_export_format_unsupported")
        try:
            go = importlib.import_module("plotly.graph_objects")
        except ImportError as exc:
            raise GeoMapError("geomap_plotly_unavailable") from exc
        custom = [[item.name, item.source_rows] for item in projection.values]
        figure = go.Figure(
            go.Choropleth(
                geojson=geometry,
                locations=[item.region_id for item in projection.values],
                z=[item.value for item in projection.values],
                featureidkey="properties.id",
                customdata=custom,
                hovertemplate="%{customdata[0]}<br>%{z}<br>Zeilen: %{customdata[1]}<extra></extra>",
                marker_line_width=0.4,
            )
        )
        figure.update_geos(fitbounds="locations", visible=False)
        attribution = " · ".join(filter(None, [projection.data_attribution, projection.map_attribution]))
        figure.update_layout(
            title=title,
            annotations=[
                {
                    "text": attribution,
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0,
                    "y": -0.08,
                    "showarrow": False,
                    "font": {"size": 10},
                }
            ],
        )
        metadata = {
            "schema": "ananta.geomap-export-metadata.v1",
            "created_at": self._clock().astimezone(timezone.utc).isoformat(),
            "map_id": projection.map_id,
            "registry_version": projection.registry_version,
            "aggregation": projection.aggregation,
            "map_attribution": projection.map_attribution,
            "data_attribution": projection.data_attribution,
            "join_report": projection.to_wire()["report"],
        }
        if output_format == "html":
            meta = json.dumps(metadata, ensure_ascii=False).replace("</", "<\\/")
            html = figure.to_html(full_html=True, include_plotlyjs=True)
            html = html.replace("</head>", f'<meta name="ananta-geomap" content={json.dumps(meta)}></head>', 1)
            content = html.encode()
            media_type = "text/html; charset=utf-8"
        else:
            try:
                content = figure.to_image(format=output_format)
            except Exception as exc:
                raise GeoMapError("geomap_static_export_unavailable", type(exc).__name__) from exc
            media_type = {
                "svg": "image/svg+xml",
                "png": "image/png",
                "pdf": "application/pdf",
            }[output_format]
        return GeoMapExportArtifact(
            filename=f"{projection.map_id}.{output_format}",
            media_type=media_type,
            content=content,
            metadata=metadata,
        )
