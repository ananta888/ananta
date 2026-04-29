# Worker Execution Profiles

Der ananta-worker nutzt drei feste Profile:

- `safe`
- `balanced`
- `fast`

## Ziel

Profile reduzieren oder erhoehen Friktion, ohne harte Invarianten auszuschalten.

## Harte Invarianten (immer aktiv)

1. Schema-Validation fuer Worker-Artefakte.
2. Command-/Capability-Policy mit Deny-Block.
3. Harte Budgets fuer Loop und Kontext.
4. Audit-faehige Trace-Metadaten.

## Profilwirkung

### safe

- konservative Budgets
- keine auto-allow Erweiterung ausser expliziter Allowlist

### balanced (Default)

- moderat erweiterte Budgets
- auto-allow fuer deterministische read-only Diagnostik (z. B. `git status`) bei Hub-`allow`

### fast

- hoehere Budgets fuer Iteration/Runtime/Kontext
- weiterhin keine Umgehung von Deny/Approval/Schema-Gates

## Sources

`profile_source` dokumentiert, woher das Profil stammt:

- `agent_default`
- `task_context`
- `task_override`
- `runtime_override`
