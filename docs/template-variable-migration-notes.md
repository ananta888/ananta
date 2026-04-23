# Template Variable Migration Notes

This note documents the transition from legacy/flat template variables to the canonical registry model.

## Why migrate

- clearer runtime semantics
- scope-aware validation
- consistent frontend/backend/test behavior
- safer strict-mode enforcement

## Compatibility status

Legacy aliases remain supported, but are marked as deprecated in validation metadata.

Common mappings:

- `anforderungen` -> `team_goal`
- `funktion` -> `task_description`
- `feature_name` -> `task_title`
- `title` -> `task_title`
- `description` -> `task_description`
- `task` -> `task_title`
- `beschreibung` -> `task_description`

## Migration checklist

1. Export or list current templates via `/templates`.
2. Validate each template with `POST /templates/validate` using intended context scope.
3. Replace deprecated aliases with canonical names.
4. Run `POST /templates/preview` to verify rendered output and missing values.
5. Enable strict mode incrementally after cleanup:
   - `template_variable_validation.strict=true`
   - optional: `template_variable_validation.context_scope=<target-scope>`

## Potential breaking points

- Templates relying on domain-specific placeholders in task context may fail strict context checks.
- Mixed-language aliases can hide semantic intent and trigger deprecated warnings.
- Strict mode now rejects context-unavailable variables in addition to unknown variables.

## Recommended rollout

1. Warn-only phase (strict off) and collect diagnostics.
2. Migrate high-traffic templates first.
3. Enable strict mode for admin save paths.
4. Set context scope only after templates for that scope are clean.
