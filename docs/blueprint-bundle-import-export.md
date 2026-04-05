# Blueprint bundle import/export

This runbook documents the operator flow for transporting blueprint configurations as JSON bundles.

## Purpose

The bundle API moves a blueprint together with its referenced templates and, optionally, one team configuration. The payload stays portable because references are resolved by **names**, not by environment-specific IDs.

## Endpoints

1. `GET /teams/blueprints/<blueprint_id>/bundle`
2. `POST /teams/blueprints/import`

Both endpoints require admin authentication.

## Bundle format

Every bundle contains:

- `schema_version` - currently `1.0`
- `mode` - `full` or `split`
- `parts` - active sections for split-mode imports/exports
- `blueprint` - blueprint metadata plus nested roles and artifacts
- `templates` - referenced prompt templates
- `team` - optional team configuration
- `bundle_metadata` - exporter hints such as `include_members`

## Full mode

Use `mode=full` when you want one reproducible JSON artifact for a blueprint.

Typical export:

```text
GET /teams/blueprints/<blueprint_id>/bundle?mode=full&team_id=<team_id>&include_members=true
```

Typical import:

```json
{
  "conflict_strategy": "overwrite",
  "dry_run": false,
  "bundle": {
    "schema_version": "1.0",
    "mode": "full",
    "...": "..."
  }
}
```

## Split mode

Use `mode=split` when templates, blueprint, and team should be migrated in stages.

Examples:

1. export only templates: `GET .../bundle?mode=split&parts=templates`
2. import templates first with `parts=["templates"]`
3. import the blueprint afterwards with `parts=["blueprint"]`
4. import the team later with `parts=["team"]`

The blueprint section intentionally keeps **roles and artifacts together** to avoid partial blueprint states.

## Conflict strategies

- `fail` - abort on name collisions and return `409 bundle_import_conflict`
- `skip` - leave existing objects untouched
- `overwrite` - update existing objects in place; repeated imports with the same content stay idempotent

## Dry-run preview

Set `dry_run=true` to preview the import without writing anything.

The response contains:

- `diff.templates`
- `diff.blueprints`
- `diff.teams`
- `summary`

Each item reports `create`, `update`, `skip`, `unchanged`, or `conflict`.

## Reference resolution rules

- blueprint role templates resolve via `template_name`
- team `role_templates` resolve via `role_name -> template_name`
- team members resolve via `role_name`, optional `blueprint_role_name`, and optional `custom_template_name`
- missing references fail explicitly instead of being silently ignored

## Operator recommendations

1. Run a `dry_run` first for production-like environments.
2. Prefer `overwrite` for controlled roundtrips and environment synchronization.
3. Use `split` when templates should be deployed before blueprint or team activation.
4. Export with `include_members=true` only when worker/member bindings should move as well.
