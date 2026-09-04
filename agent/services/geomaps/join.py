"""Renderer-independent table-to-region join policy."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from statistics import fmean
from typing import Any

from .contracts import GeoMapError, GeoMapProjection, JoinReport, RegionValue

AGGREGATIONS = {"preaggregated", "sum", "mean", "min", "max", "count"}
ISO3 = re.compile(r"^[A-Z]{3}$")
ISO_SUBDIVISION = re.compile(r"^[A-Z]{2}-[A-Z0-9]{1,3}$")
NUTS = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,3}$")


def rows_from_csv(content: str, *, max_rows: int = 100_000) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise GeoMapError("geomap_csv_header_required")
    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) >= max_rows:
            raise GeoMapError("geomap_row_limit_exceeded")
        rows.append(dict(row))
    return rows


def rows_from_dataframe(frame: Any, *, max_rows: int = 100_000) -> list[dict[str, Any]]:
    converter = getattr(frame, "to_dict", None)
    if not callable(converter):
        raise GeoMapError("geomap_dataframe_invalid")
    rows = converter(orient="records")
    return rows_from_result(rows, max_rows=max_rows)


def rows_from_result(result: Iterable[Mapping[str, Any]], *, max_rows: int = 100_000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in result:
        if len(rows) >= max_rows:
            raise GeoMapError("geomap_row_limit_exceeded")
        if not isinstance(row, Mapping):
            raise GeoMapError("geomap_row_invalid")
        rows.append(dict(row))
    return rows


class RegionJoinService:
    def join(
        self,
        *,
        registry_version: int,
        map_definition: dict[str, Any],
        geometry: dict[str, Any],
        rows: Iterable[Mapping[str, Any]],
        region_key: str,
        value_key: str,
        aggregation: str,
        data_attribution: str,
        minimum_match_ratio: float | None = None,
    ) -> GeoMapProjection:
        if aggregation not in AGGREGATIONS:
            raise GeoMapError("geomap_aggregation_unsupported")
        if not region_key or not value_key:
            raise GeoMapError("geomap_join_columns_required")
        bounded_rows = rows_from_result(rows)
        feature_names = {
            str(feature["properties"]["id"]): str(feature["properties"].get("name") or feature["properties"]["id"])
            for feature in geometry["features"]
        }
        aliases = {self._alias_key(key): str(value) for key, value in (map_definition.get("aliases") or {}).items()}
        grouped: dict[str, list[float]] = defaultdict(list)
        unmatched: set[str] = set()
        invalid_values: set[str] = set()
        for index, row in enumerate(bounded_rows):
            if region_key not in row or value_key not in row:
                raise GeoMapError("geomap_join_column_missing", str(index))
            raw_region = row.get(region_key)
            label = "" if raw_region is None else str(raw_region).strip()
            if not label:
                unmatched.add("<empty>")
                continue
            region_id = self._normalize(label, map_definition["dataJoinKey"], aliases)
            if region_id not in feature_names:
                unmatched.add(label)
                continue
            value = row.get(value_key)
            if aggregation == "count":
                numeric = 1.0
            else:
                try:
                    if value is None or isinstance(value, bool) or str(value).strip() == "":
                        raise ValueError
                    numeric = float(value)
                    if numeric != numeric or numeric in {float("inf"), float("-inf")}:
                        raise ValueError
                except (TypeError, ValueError):
                    invalid_values.add(label)
                    continue
            grouped[region_id].append(numeric)

        duplicates = tuple(sorted(region for region, values in grouped.items() if len(values) > 1))
        if duplicates and aggregation == "preaggregated":
            raise GeoMapError("geomap_duplicate_aggregation_required", ",".join(duplicates))
        values = tuple(
            RegionValue(
                region_id=region_id,
                name=feature_names[region_id],
                value=self._aggregate(entries, aggregation),
                source_rows=len(entries),
            )
            for region_id, entries in sorted(grouped.items())
        )
        attempted = len(grouped) + len(unmatched)
        match_ratio = round(len(grouped) / attempted, 6) if attempted else 0.0
        configured_threshold = float(map_definition.get("minimumMatchRatio", 0.0))
        try:
            requested_threshold = configured_threshold if minimum_match_ratio is None else float(minimum_match_ratio)
        except (TypeError, ValueError) as exc:
            raise GeoMapError("geomap_match_ratio_invalid") from exc
        if not 0 <= requested_threshold <= 1:
            raise GeoMapError("geomap_match_ratio_invalid")
        threshold = max(configured_threshold, requested_threshold)
        reason_codes: list[str] = []
        if not values:
            reason_codes.append("geomap_no_matched_regions")
        if unmatched:
            reason_codes.append("geomap_regions_unmatched")
        if invalid_values:
            reason_codes.append("geomap_values_invalid")
        if match_ratio < threshold:
            reason_codes.append("geomap_match_ratio_below_threshold")
        normalized_attribution = data_attribution.strip()
        if not normalized_attribution:
            reason_codes.append("geomap_data_attribution_required")
        publication_eligible = (
            bool(values) and not invalid_values and match_ratio >= threshold and bool(normalized_attribution)
        )
        report = JoinReport(
            matched=tuple(item.region_id for item in values),
            unmatched=tuple(sorted(unmatched)),
            duplicates=duplicates,
            missing_geometry=tuple(sorted(set(feature_names) - set(grouped))),
            invalid_values=tuple(sorted(invalid_values)),
            match_ratio=match_ratio,
            minimum_match_ratio=threshold,
            publication_eligible=publication_eligible,
            reason_codes=tuple(reason_codes),
        )
        return GeoMapProjection(
            schema="ananta.geomap-projection.v1",
            map_id=map_definition["id"],
            registry_version=registry_version,
            aggregation=aggregation,
            values=values,
            report=report,
            map_attribution=map_definition["attribution"],
            data_attribution=normalized_attribution,
        )

    @staticmethod
    def _alias_key(value: str) -> str:
        return " ".join(value.strip().casefold().split())

    def _normalize(self, value: str, key_kind: str, aliases: dict[str, str]) -> str:
        alias = aliases.get(self._alias_key(value))
        if alias:
            return alias
        normalized = value.strip().upper()
        if key_kind == "iso-3166-1-alpha-3" and not ISO3.fullmatch(normalized):
            return ""
        if key_kind == "iso-3166-2" and not ISO_SUBDIVISION.fullmatch(normalized):
            return ""
        if key_kind == "nuts" and not NUTS.fullmatch(normalized):
            return ""
        if key_kind == "continent":
            return self._alias_key(value).replace(" ", "-")
        return value.strip()

    @staticmethod
    def _aggregate(values: list[float], aggregation: str) -> float:
        if aggregation == "sum" or aggregation == "count":
            return float(sum(values))
        if aggregation == "mean":
            return float(fmean(values))
        if aggregation == "min":
            return float(min(values))
        if aggregation == "max":
            return float(max(values))
        return float(values[0])
