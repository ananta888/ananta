# Cross-Track Dependency Map

Cross-track dependencies must use the explicit format:

`<todo-file>.json:<task-id>`

Only approved references are allowed.

## Approved cross-track dependencies

| Source task | Depends on | Reason |
| --- | --- | --- |
| `todo.json:PG-T07` | `todo.kritis.json:K1-AUD-T15` | PR review automation must wait for audit contract stability |
| `todo.json:PG-T07` | `todo.kritis.json:K1-MUT-T13` | PR review flow must not bypass mutation gate guarantees |
| `todo.json:PG-T07` | `todo.kritis.json:K1-EVO-T09` | PR review dogfooding requires evolver release-gate stability |

## Validation command

```bash
python3 scripts/validate_cross_track_dependencies.py
```

The validator fails on:

- missing target files
- missing target task IDs
- references to archived/inactive track files
- circular cross-track dependency chains
