from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from agent.services.geomaps.export import PlotlyGeoMapExporter, PortableGeoMapExporter
from agent.services.geomaps.service import GeoMapService


@pytest.fixture
def projection_and_geometry():
    service = GeoMapService()
    projection = service.project(
        map_id="de-states",
        rows=[{"region": "DE-BE", "value": 7}, {"region": "DE-BB", "value": 3}],
        region_key="region",
        value_key="value",
        aggregation="sum",
        data_attribution="Deterministic export fixture",
        minimum_match_ratio=1,
    )
    return projection, service.geometry("de-states")


@pytest.mark.parametrize(
    "output_format,signature",
    [("svg", b"<svg"), ("html", b"<!doctype html>"), ("png", b"\x89PNG"), ("pdf", b"%PDF")],
)
def test_portable_export_is_fully_headless_with_metadata(projection_and_geometry, output_format, signature) -> None:
    projection, geometry = projection_and_geometry
    exporter = PortableGeoMapExporter(clock=lambda: datetime(2026, 9, 4, tzinfo=timezone.utc))
    artifact = exporter.render(
        projection=projection,
        geometry=geometry,
        output_format=output_format,
        title="Test map",
    )
    assert artifact.content.startswith(signature)
    assert artifact.metadata["created_at"] == "2026-09-04T00:00:00+00:00"
    assert artifact.metadata["aggregation"] == "sum"
    assert artifact.metadata["map_attribution"]
    assert artifact.metadata["data_attribution"] == "Deterministic export fixture"


def test_portable_export_fits_regional_geometry_to_the_canvas(projection_and_geometry) -> None:
    projection, geometry = projection_and_geometry
    artifact = PortableGeoMapExporter().render(
        projection=projection,
        geometry=geometry,
        output_format="svg",
        title="Regional map",
    )
    coordinates = [float(value) for value in re.findall(rb"[ML]([0-9.]+),", artifact.content)]
    assert max(coordinates) - min(coordinates) >= 600


def test_plotly_adapter_generates_self_contained_html(monkeypatch, projection_and_geometry) -> None:
    class FakeGraphObjects:
        @staticmethod
        def Choropleth(**values):
            return values

        class Figure:
            def __init__(self, trace):
                self.trace = trace
                self.layout = {}

            def update_geos(self, **values):
                self.layout["geo"] = values

            def update_layout(self, **values):
                self.layout.update(values)

            def to_html(self, **_values):
                return f"<html><head></head><body>plotly.js {self.layout}</body></html>"

    monkeypatch.setattr("agent.services.geomaps.export.importlib.import_module", lambda _name: FakeGraphObjects)
    projection, geometry = projection_and_geometry
    artifact = PlotlyGeoMapExporter().render(
        projection=projection,
        geometry=geometry,
        output_format="html",
        title="Plotly map",
    )
    assert artifact.media_type.startswith("text/html")
    assert b"plotly.js" in artifact.content
    assert b"Deterministic export fixture" in artifact.content
