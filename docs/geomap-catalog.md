# GeoMap catalog

GeoMap Studio turns CSV or tabular rows into offline choropleth maps. Open
`/geomaps`, choose a CSV, select a built-in map, bind the region and value
columns, and choose an aggregation. The Hub returns one deterministic join
projection used by both the Web renderer and all exports.

Built-in keys are ISO 3166-1 alpha-3 for world and European countries,
lower-case catalog identifiers for continents, and ISO 3166-2 (`DE-BW` through
`DE-TH`) for German states. Only aliases explicitly listed in the registry are
accepted. Duplicate keys require `sum`, `mean`, `min`, `max`, or `count`;
`preaggregated` fails closed on duplicates.

The preview exposes matched, unmatched, duplicate, invalid-value, and
geometry-without-data sets. The Hub blocks publication below the map's policy
threshold, when values are invalid, or when data-source attribution is empty.
A caller may increase the threshold but cannot lower it. The export endpoint
rechecks that Hub decision. SVG, PNG, PDF, and self-contained HTML exports include the registry version,
aggregation, join report, creation time, data attribution, and map attribution.
No approval or other human interaction is required for a bounded headless run.

## Python use

The dependency-light exporter is available in the base installation. The
optional Plotly adapter is installed with `pip install 'ananta[geomap]'`.
Pandas frames are converted by
`agent.services.geomaps.join.rows_from_dataframe`; CSV and SQL-style mapping
rows use `rows_from_csv` and `rows_from_result`. All feed the same
`GeoMapService.project` contract.

```python
from agent.services.geomaps.service import GeoMapService

service = GeoMapService()
projection = service.project(
    map_id="de-states",
    rows=[{"region": "DE-BE", "value": 3}, {"region": "DE-BB", "value": 5}],
    region_key="region",
    value_key="value",
    aggregation="sum",
    data_attribution="Example data",
    minimum_match_ratio=1,
)
artifact = service.export(projection=projection, output_format="svg", title="Example")
```

## Adding a map

1. Put the bounded GeoJSON or SVG file under `assets/geomaps/`. Runtime network
   URLs are invalid.
2. Validate an upload through the admin-only `/api/geomaps/validate-upload`
   endpoint. Store the returned sanitized bytes, digest, size, feature count,
   and stable identifiers.
3. Add one entry to `config/geomaps/registry.v1.json`, including license URL and
   attribution. SVG maps support only ECharts; Plotly and portable exports need
   Polygon/MultiPolygon GeoJSON.
4. Run `python scripts/run_geomap_release_gate.py`. The gate verifies schema,
   digests, size budgets, unique identifiers, offline behavior, joins, exports,
   attribution, and active-content rejection.

GeoJSON accepts only Polygon and MultiPolygon FeatureCollections with unique
`properties.id` values. SVG rejects DTD/entities, script-capable elements,
event handlers, JavaScript URLs, stylesheets, and external or data resources.
Every interactive SVG region needs a unique `name` or `id`.

The source refresh command is explicit and never used at runtime:

```shell
python scripts/build_geomap_assets.py --refresh
```

It downloads only pinned Natural Earth and deutschlandGeoJSON revisions and
rebuilds deterministic compact assets plus `assets/geomaps/attribution.v1.json`.
