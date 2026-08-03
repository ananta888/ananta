# Blueprint bundle import/export

This runbook documents the operator flow for transporting blueprint configurations as JSON bundles.

## Purpose

The bundle API moves a blueprint together with its referenced templates and, optionally, one team configuration. The payload stays portable because references are resolved by **names**, not by environment-specific IDs.

This is the backward-compatible Team Blueprint Bundle v1 path. Multi-team
organizations use Organization Bundle v2; v1 endpoints are not renamed and a
v1 import never infers or creates an Organization Instance.

## Organization Bundle v2

Bundle v2 is a closed, versioned envelope for role-template fragments, Team
Blueprints, Organization Blueprints, complete workflow envelopes, policies,
handoffs and limit profiles. Organization definitions carry portable unit,
role-slot cardinality and typed-relation declarations. Stable key/version
references replace environment database IDs. Runtime transport is opt-in and
never serializes a source snapshot: `include_instances=true` emits only a
target-recompile recipe, while `include_assignments=true` emits pseudonymized
assignment intents and requires `include_instances=true`.

Organization export, import-preview and import-apply are separate scoped Hub
endpoints. Export omits source tenant/project/organization IDs, local database
IDs, compiled plans, credentials and local agent URLs. Assignments are excluded
by default. If explicitly included, each source principal becomes an opaque
`principal_ref`; import requires an explicit mapping from that reference to an
eligible Agent URL registered in the target environment. Template/prompt
bodies are never written to audit records.

Import is always preview-first:

1. reject an oversized body before expensive parsing;
2. validate the closed envelope and all fragment references;
3. calculate a write-free grouped diff and effective limits;
4. bind plan digest to bundle digest, principal, tenant/project scope,
   definition revision, policy revision/hash, conflict strategy and expiry;
5. submit the complete preview to `/import-grants`; the Hub independently
   recomputes it and issues a short-lived, principal-bound one-shot grant only
   for an exact match;
6. apply the unchanged plan once through one Unit of Work.

Stale, consumed, scope-mismatched or content-mismatched plans fail before the
first write. Faults roll back the whole operation. Definition sections preserve
workflows, policies, topology and role-slot semantics through their stable
references. Optional instance recipes are recompiled in the authenticated
target project and materialized in that same transaction; the target Hub
allocates all runtime IDs. Optional assignment intents are accepted only after
target-local rebinding, capability/capacity checks and Separation-of-Duties
validation. Custom compositions additionally require an instance-bound,
one-shot admission-exception reference in the preview request.

Organization Bundle v2 endpoints:

- `GET /api/organization-bundles/export?organization_id=<id>&include_instances=<bool>&include_assignments=<bool>`
- `POST /api/organization-bundles/import-preview`
- `POST /api/organization-bundles/import-grants`
- `POST /api/organization-bundles/import-apply`

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
5. For Organization Bundle v2, never insert source-scoped IDs, compiled plans,
   topology snapshots or raw Agent URLs. Use only target-recompile recipes and
   explicit target-local assignment rebinding.
6. Never edit a preview digest or reuse its grant for another bundle/scope.
