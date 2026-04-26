# ADR: Planning Templates move to Catalog/Blueprint-backed path

- **Status:** Accepted
- **Date:** 2026-04-26
- **Scope:** AutoPlanner template resolution and planning blueprint consistency

## Context

`agent/services/planning_utils.py` currently mixes technical helper logic with
fachliche planning template ownership (`GOAL_TEMPLATES`, keyword matching and
execution-focused fallback hints).

At the same time, Team/Blueprint/Role/Template data already exists as a
separate model in backend services and routes. This creates multiple planning
truth sources and increases drift risk.

## Decision

1. Fachliche planning templates move to a dedicated planning catalog
   (`config/planning_templates.json`) with schema validation
   (`schemas/planning/planning_template_catalog.v1.json`).
2. `planning_utils.py` remains technical utility only:
   sanitize/validate input, JSON extraction, subtask normalization, followup parsing.
3. Template strategy resolves from catalog and (later) optional
   blueprint-backed adapter instead of hardcoded dictionaries.
4. Team Blueprints remain the source for team instantiation/starter artifacts;
   planning template catalog is the source for AutoPlanner subtask template resolution.
5. Legacy template names and keyword behavior stay compatibility-supported during migration.

## Relationship model

1. **Role Template / Team Blueprint layer:** team setup, role assignments, starter artifacts.
2. **Planning template catalog layer:** deterministic goal-to-subtask template resolution.
3. **PlanNode/Task materialization layer:** materialized execution artifacts and lifecycle state.

No client surface owns orchestration, routing or policy decisions.

## Compatibility behavior

- Keep existing template IDs (`bug_fix`, `feature`, `refactor`, `test`, `repo_analysis`,
  `sys_diag`, `code_fix`, `new_software_project`, `project_evolution`, etc.).
- Keep keyword matching semantics for representative German/English intents.
- Unknown templates return no template match and allow fallback to HubCopilot/LLM strategy.

## Consequences

- Planning truth becomes auditable and data-driven.
- Hardcoded Python dictionaries stop being the preferred planning source.
- Strategy migration can proceed incrementally without breaking current API behavior.
- Governance boundaries remain intact: no policy bypass and no client-side orchestration.
