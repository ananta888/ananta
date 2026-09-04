from __future__ import annotations

import pytest

from agent.services.geomaps.contracts import GeoMapError
from agent.services.geomaps.join import RegionJoinService, rows_from_csv, rows_from_dataframe
from agent.services.geomaps.registry import GeoMapRegistry
from agent.services.geomaps.service import GeoMapService


def test_iso_join_reports_unknown_null_and_missing_geometry() -> None:
    service = GeoMapService()
    projection = service.project(
        map_id="europe-countries",
        rows=[
            {"iso": "DEU", "value": 4},
            {"iso": "FRA", "value": 2},
            {"iso": "unknown", "value": 9},
            {"iso": "ESP", "value": None},
        ],
        region_key="iso",
        value_key="value",
        aggregation="preaggregated",
        data_attribution="Deterministic fixture",
    )

    assert [(item.region_id, item.value) for item in projection.values] == [("DEU", 4.0), ("FRA", 2.0)]
    assert projection.report.unmatched == ("unknown",)
    assert projection.report.invalid_values == ("ESP",)
    assert "DEU" not in projection.report.missing_geometry
    assert projection.report.publication_eligible is False
    assert projection.data_attribution == "Deterministic fixture"


def test_duplicate_requires_explicit_aggregation() -> None:
    service = GeoMapService()
    rows = [{"state": "DE-BW", "value": 2}, {"state": "DE-BW", "value": 4}]
    with pytest.raises(GeoMapError, match="geomap_duplicate_aggregation_required"):
        service.project(
            map_id="de-states",
            rows=rows,
            region_key="state",
            value_key="value",
            aggregation="preaggregated",
        )

    projection = service.project(
        map_id="de-states",
        rows=rows,
        region_key="state",
        value_key="value",
        aggregation="mean",
        minimum_match_ratio=1,
        data_attribution="Deterministic fixture",
    )
    assert projection.values[0].value == 3
    assert projection.report.duplicates == ("DE-BW",)
    assert projection.report.publication_eligible is True


def test_declared_german_aliases_are_normalized_but_guesses_are_not() -> None:
    service = GeoMapService()
    projection = service.project(
        map_id="de-states",
        rows=[{"state": "Thüringen", "value": 1}, {"state": "Freistaat Bayern", "value": 2}],
        region_key="state",
        value_key="value",
        aggregation="preaggregated",
    )
    assert projection.report.matched == ("DE-TH",)
    assert projection.report.unmatched == ("Freistaat Bayern",)


def test_csv_dataframe_and_sql_style_rows_share_one_contract() -> None:
    csv_rows = rows_from_csv("region,value\nDE-BE,2\nDE-BB,4\n")

    class Frame:
        def to_dict(self, *, orient):
            assert orient == "records"
            return csv_rows

    assert rows_from_dataframe(Frame()) == csv_rows
    registry = GeoMapRegistry()
    projection = RegionJoinService().join(
        registry_version=registry.version,
        map_definition=registry.get("de-states"),
        geometry=registry.geometry("de-states"),
        rows=csv_rows,
        region_key="region",
        value_key="value",
        aggregation="sum",
        data_attribution="",
        minimum_match_ratio=1,
    )
    assert {item.region_id: item.value for item in projection.values} == {"DE-BB": 4, "DE-BE": 2}


def test_registry_threshold_cannot_be_lowered_by_caller() -> None:
    service = GeoMapService()
    projection = service.project(
        map_id="de-states",
        rows=[{"state": "DE-BE", "value": 1}, {"state": "XX", "value": 2}],
        region_key="state",
        value_key="value",
        aggregation="sum",
        minimum_match_ratio=0,
    )
    assert projection.report.minimum_match_ratio == 0.9
    assert projection.report.publication_eligible is False


def test_publication_requires_data_attribution() -> None:
    service = GeoMapService()
    projection = service.project(
        map_id="de-states",
        rows=[{"state": "DE-BE", "value": 1}],
        region_key="state",
        value_key="value",
        aggregation="sum",
        minimum_match_ratio=1,
    )
    assert projection.report.publication_eligible is False
    assert "geomap_data_attribution_required" in projection.report.reason_codes
