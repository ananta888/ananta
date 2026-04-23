# Template Variable Registry and Runtime Contract

## Purpose

This note documents the current template variable model, runtime rendering contract, and integration touchpoints across templates, blueprints, roles, and teams.

## Canonical Variable Registry

The canonical registry is defined in `agent/services/template_variable_registry.py` and exposed via:

- `GET /templates/variable-registry`
- `GET /templates/sample-contexts`
- `POST /templates/validate`
- `POST /templates/preview`
- `POST /templates/validation-diagnostics`
- `GET /config` (`template_variables_allowlist` + `template_variable_registry`)

The registry now separates variables by scope and stability:

- **Stable runtime variables:** `agent_name`, `task_title`, `task_description`, `team_name`, `role_name`, `team_goal`, `goal_context`, `acceptance_criteria`
- **Legacy aliases:** `anforderungen`, `funktion`, `feature_name`, `title`, `description`, `task`, `beschreibung`
- **Domain-specific placeholders:** `endpoint_name`, `sprache`, `api_details`

## Legacy vs Generic vs Domain-Specific

1. **Generic/stable runtime variables** are directly sourced from task/goal/team/role resolution.
2. **Legacy aliases** are kept for compatibility with existing templates and older German naming.
3. **Domain-specific placeholders** are accepted for specialized prompt families where values are supplied by caller-side payload conventions.

## Integration Audit (Blueprints, Roles, Teams, Runtime)

Main touchpoints:

- Validation at admin CRUD: `agent/routes/config/templates.py`
- Runtime role/template resolution: `agent/services/task_template_resolution.py`
- Runtime placeholder materialization: `agent/services/task_scoped_execution_service.py` (`_get_system_prompt_for_task`)
- Team-type default template seeding: `agent/routes/teams.py` (`ensure_default_templates`)
- Blueprint bundle template references/validation: `agent/services/blueprint_bundle_service.py`

Runtime availability is determined by resolved task context (task, role, team, goal). That means variables can be globally known but still empty in a specific task run if upstream context is missing.

## Runtime Rendering Contract

The runtime contract is exposed via `GET /templates/runtime-contract`.

Contract highlights:

- Rendering mode is **direct placeholder replacement** (`{{variable}}`) without expression evaluation.
- `team_goal` uses a fallback chain (`goal.goal -> task.title -> task.description -> team_name -> default marker`).
- `goal_context` and `acceptance_criteria` are optional and can be empty.
- Unknown variable handling is warn-only by default and can be strict via `template_variable_validation.strict=true`.
- Context-aware validation can be fixed to a default scope via `template_variable_validation.context_scope`.
- Strict-mode errors now distinguish:
  - `unknown_template_variables`
  - `context_unavailable_template_variables`
  - `template_validation_failed` (mixed errors)

## Known Ambiguities (Explicitly Tracked)

- Legacy placeholders overlap semantically with canonical names (`title`, `task`, `description`, `funktion`, `anforderungen`).
- Domain-specific placeholders are intentionally open for specialized workflows and may be empty in standard task execution.

The migration path is to prefer canonical stable names while keeping aliases until template migration notes complete.

See:

- `docs/template-authoring-guide.md`
- `docs/template-variable-migration-notes.md`
