# GeoMap catalog architecture

## Repository audit

The GeoMap slice is additive and reuses Ananta's existing boundaries instead
of creating another dashboard or artifact subsystem.

- `frontend-angular/src/app/features/spreadsheet-studio/` is the existing
  tabular import, preview, and guided-action surface. The GeoMap workflow is a
  focused child feature of this flow.
- `frontend-angular/src/app/features/model-training/jobs/training-metrics-chart.component.ts`
  proves the existing lightweight SVG chart approach, but it is not a reusable
  geographic renderer. The audited package manifest contains neither ECharts
  nor Plotly, so both adapters must be introduced explicitly.
- `agent/bootstrap/routes.py` is the central Flask blueprint composition root.
  A GeoMap route delegates validation, joins, policy, and export preparation to
  focused services and never embeds business rules in handlers.
- `agent/artifacts/`, `agent/repositories/artifacts.py`, and the versioned Worker
  artifact contracts remain the only artifact boundary. GeoMap export must not
  create a second persistence model.
- Worker export patterns in `worker/training/exports.py` may be reused for
  isolated rendering, but registry admission, publication thresholds, license
  policy, task ownership, and routing remain Hub responsibilities.

The input contract is a bounded JSON row sequence. CSV and SQL adapters produce
that contract, Pandas data frames use `to_dict(orient="records")`, and Spreadsheet
Studio projects workbook ranges to the same rows. The renderer-independent Hub
join result is the sole value source for both Web and Python adapters.

## Target structure

The versioned source of truth is `config/geomaps/registry.v1.json`, validated by
`schemas/geomaps/registry.v1.json`. Packaged geometry and source metadata live
under `assets/geomaps/`. Focused services under `agent/services/geomaps/` own
registry loading, file validation, deterministic joins, and renderer ports.
`agent/routes/geomaps.py` exposes authenticated Hub APIs and is wired only by
the existing bootstrap composition root.

The Angular feature under `frontend-angular/src/app/features/geomaps/` owns the
guided workflow and an ECharts adapter. It consumes only Hub projections; it
does not decide publication eligibility. Python/Plotly export consumes the same
closed join projection and returns a typed transport artifact that callers may
persist through the existing artifact boundary; GeoMap introduces no storage.

## Canonical keys and data policy

Built-in country maps use ISO 3166-1 alpha-3, German states use ISO 3166-2, and
continent maps use an explicit catalog key. NUTS and custom identifiers are
allowed only when the registry declares their key kind. Names, including
umlaut spellings, are aliases only when declared in the registry; heuristic
name matching is forbidden.

Duplicate input keys require an explicit aggregation (`sum`, `mean`, `min`,
`max`, or `count`). Missing, unknown, and geometry-less identifiers are
reported separately. The Hub blocks publication below the configured match
threshold, on invalid values, or without data-source attribution and returns a
bounded machine-readable result. It enforces that decision again on export; no
interactive human step is required by tests or headless production runs.

## Offline, provenance, and security

All built-in geometry is installed locally and is loaded by registry-relative
paths. Runtime CDN and repository fetches are forbidden. Every data file has a
digest, version, origin, license, attribution, feature count, and byte budget.
Exports show both data-source attribution supplied by the caller and map-source
attribution from the registry.

Custom GeoJSON accepts only bounded Polygon/MultiPolygon FeatureCollections.
Custom SVG is sanitized before storage: executable elements, event handlers,
external references, stylesheets, and non-local URL values are rejected. A
Worker may render only the immutable projection admitted and assigned by the
Hub; it cannot widen the registry entry or mint evidence identity.

## SOLID boundary check

Registry loading, geometry validation, joining, export rendering, HTTP
transport, and Angular interaction are separate responsibilities (SRP). Web
and Python renderers consume a small shared projection rather than each other
(ISP/DIP), so a renderer is substitutable without changing join semantics
(LSP/OCP). Registry and artifact paths are injected at composition boundaries;
there is no hidden mutable global cache. These seams support deterministic,
headless unit, contract, component, and end-to-end tests.
