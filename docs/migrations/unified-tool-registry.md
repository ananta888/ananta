# Migration: Unified Tool Registry

Inventory of legacy `agent/tools.py` tools and their migration status.

## Legacy `agent.tools.ToolRegistry` tools

| Tool name | Status | Notes |
|-----------|--------|-------|
| `create_template` | `keep_legacy` | Hub admin via SGPT; not an ananta-worker tool |
| `update_template` | `keep_legacy` | Hub admin via SGPT |
| `delete_template` | `keep_legacy` | Hub admin via SGPT |
| `create_team` | `keep_legacy` | Hub admin via SGPT |
| `ensure_team_templates` | `keep_legacy` | Hub admin via SGPT |
| `list_teams` | `keep_legacy` | Hub admin via SGPT |
| `update_config` | `admin_only` | Config mutation; blocked for worker loop |
| `analyze_logs` | `admin_only` | Audit read; not worker-facing |
| `read_agent_logs` | `admin_only` | Log read; not worker-facing |
| `assign_role` | `keep_legacy` | Hub admin via SGPT |
| `list_roles` | `keep_legacy` | Hub admin via SGPT |
| `list_agents` | `keep_legacy` | Hub admin via SGPT |
| `list_templates` | `keep_legacy` | Hub admin via SGPT |
| `upsert_team_type` | `keep_legacy` | Hub admin via SGPT |
| `delete_team_type` | `keep_legacy` | Hub admin via SGPT |
| `upsert_role` | `keep_legacy` | Hub admin via SGPT |
| `delete_role` | `keep_legacy` | Hub admin via SGPT |
| `link_role_to_team_type` | `keep_legacy` | Hub admin via SGPT |
| `unlink_role_from_team_type` | `keep_legacy` | Hub admin via SGPT |
| `set_role_template_mapping` | `keep_legacy` | Hub admin via SGPT |
| `upsert_team` | `keep_legacy` | Hub admin via SGPT |
| `delete_team` | `keep_legacy` | Hub admin via SGPT |
| `activate_team` | `keep_legacy` | Hub admin via SGPT |
| `configure_auto_planner` | `admin_only` | Runtime config; not worker-facing |
| `configure_triggers` | `admin_only` | Runtime config; not worker-facing |
| `set_autopilot_state` | `admin_only` | Autopilot control; not worker-facing |
| `file_read` (alias: `read_file` …) | `migrate` | Replaced by `repo.read_file_range` in AnantaToolRegistryService |
| `file_write` (alias: `write_file` …) | `migrate` | Replaced by `repo.write_file` |
| `file_list` (alias: `list_files` …) | `migrate` | Replaced by `repo.list_files` |
| `file_patch` (alias: `edit_file` …) | `migrate` | Replaced by `repo.apply_patch` |
| `shell_execute` (alias: `bash` …) | `migrate` | Replaced by `shell.run_allowlisted` (requires approval) |
| `web_search` | `remove` | No ananta-worker equivalent; external-agent pattern not yet approved |
| `web_fetch` | `remove` | Same as web_search |
| `git_status` | `migrate` | Replaced by `git.status` |
| `git_diff` | `migrate` | Replaced by `git.diff_readonly` |
| `git_log` | `keep_legacy` | Not yet in AnantaToolRegistryService |
| `git_commit` | `admin_only` | Blocked in worker loop (CATEGORY_BLOCKED) |
| `git_push` | `admin_only` | Blocked in worker loop (CATEGORY_BLOCKED) |
| `doc_extract` | `keep_legacy` | No ananta-worker equivalent yet |

## Status legend

- `migrate` — equivalent exists in `AnantaToolRegistryService`; callers should switch
- `keep_legacy` — hub-admin / SGPT-CLI tool; not worker-facing; keep as-is
- `admin_only` — dangerous mutation; blocked or restricted; keep behind explicit approval
- `remove` — no planned ananta-worker equivalent; candidates for removal once SGPT CLI usage is audited
