# Tool Intent Taxonomy (GEC-T013)

This taxonomy provides canonical intent classes used by resolver, policy checks and audit logs.

## Intents

- `shell_command`
  - Tools: `bash`, `shell_execute`, `run_command`, `execute_command`
  - Risk: `high`
  - Tool class: `admin`
- `file_write`
  - Tools: `file_write`, `file_patch`
  - Risk: `medium`
  - Tool class: `write`
- `file_read`
  - Tools: `file_read`, `file_list`
  - Risk: `low`
  - Tool class: `read`
- `web_search`
  - Tools: `web_search`, `web_fetch`
  - Risk: `low`
  - Tool class: `read`
- `git_query`
  - Tools: `git_status`, `git_diff`, `git_log`
  - Risk: `low`
  - Tool class: `read`
- `git_commit`
  - Tools: `git_commit`
  - Risk: `high`
  - Tool class: `admin`

## Governance behavior

- Unknown tools are first normalized and remapped conservatively.
- Remap events log: `original_tool`, `resolved_tool`, `resolved_intent`, `resolved_risk`, `tool_class`, `reason`.
- Post-remap policy enforces `allowed_tool_classes` from `worker_execution_contract` before execution.

