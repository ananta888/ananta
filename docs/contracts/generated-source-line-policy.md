# Generated Source Line Policy Contract

Schema: `generated_source_line_policy_result.v1`

The Generated Source Line Policy is a post-mutation quality guard for
Ananta worker writes. It does not replace workspace scope validation,
approval gates, or path security checks. The hub/tool execution path
evaluates changed files and returns bounded metadata only.

## Result Shape

```json
{
  "schema": "generated_source_line_policy_result.v1",
  "enabled": true,
  "status": "ok|warning|followup_required|blocked",
  "summary": {
    "total_files": 1,
    "ok": 0,
    "warning": 0,
    "followup_required": 0,
    "blocked": 1
  },
  "file_results": [
    {
      "path": "agent/example.py",
      "category": "production_source",
      "before_lines": null,
      "after_lines": 1200,
      "decision": "blocked",
      "action": "block",
      "reason_code": "new_file_over_hard_limit",
      "threshold": 1000,
      "warning_threshold": 600,
      "unreadable_reason": null
    }
  ],
  "warnings": [],
  "followup_todos": []
}
```

## Categories

- `production_source`: normal source files.
- `facade_or_routes`: API, route, router, or facade source files.
- `tests`: test files and files under test paths.
- `generated`: generated source such as `*.generated.*`, minified JS, and protobuf outputs.
- `data_schema_config`: JSON/YAML/TOML/config/schema-like files.
- `excluded`: ignored paths or unsupported extensions.

## Actions

- `allow`: no policy concern.
- `warn`: mutation remains applied and a warning is returned.
- `require_followup`: mutation remains applied, but a follow-up entry is required.
- `block`: mutation must not remain applied; callers must pre-check or roll back.

## Reason Codes

- `new_file_over_hard_limit`: a new counted file exceeds the hard threshold.
- `crossed_hard_limit`: an existing file crossed the hard threshold.
- `existing_over_limit_grew`: a legacy file over the hard threshold grew further, or stayed over the limit.
- `over_warning_threshold`: a file exceeds the warning threshold.
- `category_excluded`: the path is excluded from counted source policy.
- `generated_allowed_with_reason`: generated output is allowed but visible.
- `unreadable_file`: the policy could not read the file and evaluated conservatively.

## Rollout Modes

- `off`: equivalent to disabled.
- `warn`: block/follow-up actions are downgraded to warnings.
- `followup_required`: block actions are downgraded to follow-up-required.
- `block`: configured actions are enforced.
