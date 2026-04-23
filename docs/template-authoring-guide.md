# Template Authoring Guide

This guide is for writing and maintaining prompt templates in Ananta.

## 1. Preferred Variable Strategy

Use canonical variables first:

- `agent_name`
- `task_title`
- `task_description`
- `team_name`
- `role_name`
- `team_goal`
- `goal_context`
- `acceptance_criteria`

These variables are stable and aligned with runtime task rendering.

## 2. Scopes and Availability

A variable can be globally known but still unavailable in a selected context.

Available context scopes:

- `task`
- `team`
- `role`
- `blueprint`
- `agent`
- `artifact`
- `domain_specific`

Use:

- `GET /templates/variable-registry` for canonical metadata
- `POST /templates/validate` to verify context validity before save

## 3. Validation Workflow

1. Draft template text with `{{variable}}` placeholders.
2. Validate with selected `context_scope`.
3. Use preview to check resolved output and missing values.
4. Save template only after unknown/context-invalid issues are cleared.

Validation endpoint:

- `POST /templates/validate`

Preview endpoint:

- `POST /templates/preview`

## 4. Preview and Sample Contexts

Preview does not require storing a template first.

Use sample contexts:

- `GET /templates/sample-contexts`

You can override sample values in `context_payload` for focused checks.

## 5. Strict Mode Behavior

Config flag:

- `template_variable_validation.strict`

When enabled:

- unknown variables are rejected
- context-unavailable variables are rejected
- mixed error cases return a combined validation failure

Optional context baseline:

- `template_variable_validation.context_scope`

## 6. Legacy Variables

Legacy aliases are still accepted for compatibility but should be migrated.

Examples:

- `anforderungen` -> `team_goal`
- `funktion` -> `task_description`
- `title` -> `task_title`

Keep legacy usage only where migration is not yet complete.

## 7. Operator Diagnostics

For safe debugging without leaking context values:

- `POST /templates/validation-diagnostics`

The response includes issue severity/codes, context keys, and render completeness, but no raw context values by default.
