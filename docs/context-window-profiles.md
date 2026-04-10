# Context Window Profiles

Ananta now treats worker context window sizing as a first-class runtime profile.

## Supported Profiles

- `compact_12k`
- `standard_32k` (default)
- `full_64k`

## Policy Fields

`context_bundle_policy` supports:

- `window_profile`
- `compact_budget_tokens`
- `standard_budget_tokens`
- `full_budget_tokens`
- `budget_tokens_by_mode` (derived map)

`standard_32k` is the recommended default for regular worker subtasks. This keeps retrieval and bundle behavior predictable across different hardware classes while allowing runtime to differ.
