# Source Line Limit Policy

The Generated Source Line Policy prevents Ananta from creating new
source monoliths during worker-driven mutations. It complements the
workspace mutation policy: path/scope/security checks still run first,
then line-count quality checks evaluate the changed files.

Default configuration is available under `generated_source_line_policy`
and is disabled by default for backward compatibility.

## Default Thresholds

| Category | Target | Warning | Hard Max | New File Over Hard |
| --- | ---: | ---: | ---: | --- |
| `production_source` | 800 | 600 | 1000 | `block` |
| `facade_or_routes` | 900 | 700 | 1000 | `require_followup` |
| `tests` | 1000 | 1000 | 1500 | `warn` |
| `data_schema_config` | 1200 | 1200 | 1500 | `warn` |
| `generated` | 1200 | 1200 | 1500 | `warn` |

## Examples

- A new `agent/service.py` with 1200 lines is blocked when mode is
  `block`; `repo.write_file` removes the file again and returns
  `source_line_policy_result`.
- A legacy file that was already above 1000 lines and receives a small
  non-growing patch is not blocked. It is marked as warning/follow-up
  so refactoring remains visible.
- A test file above 1500 lines is reported but not hard-blocked by
  default.

## Reports

Tool results include `data.source_line_policy_result`. The workspace
mutation report stores `source_line_policy_summary` and
`final_source_line_policy_result`. If `create_followup_todo` is enabled,
idempotent follow-up metadata is written to
`.ananta/source-line-followups.json`.

Reports and audit events contain paths, categories, line counts,
thresholds, decisions, and reason codes only. They must not include file
contents or large diffs.
