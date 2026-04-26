# Documentation command usage inventory

This inventory classifies command snippets against the executable CLI contract in `docs/status/documentation-command-contract.json`.

## User-path snippets (`ananta ...`)

| Document | Classification | Notes |
| --- | --- | --- |
| `README.md` | user_path | CLI-first and full-stack paths now separated; examples use `ananta ...` |
| `docs/setup/quickstart.md` | user_path | CLI-only quickstart; cross-link to full-stack path |
| `docs/setup/bootstrap-install.md` | user_path | bootstrap + post-install commands use `ananta ...` |
| `docs/setup/ananta_init.md` | user_path | init contract with `--endpoint-url` |
| `docs/cli/commands.md` | user_path | canonical user command overview |
| `docs/golden-path-cli.md` | user_path | official CLI path and success signal |
| `docs/demo-flows.md` | user_path | demo flow CLI examples moved to `ananta ...` |

## Developer fallback snippets

| Document | Classification | Notes |
| --- | --- | --- |
| `docs/cli/developer_entrypoints.md` | dev_fallback | explicitly keeps `python -m agent.cli_goals ...` for internal/dev compatibility |

## Drift fixes applied from this inventory

| Drift item | Previous state | Current state | Related task |
| --- | --- | --- | --- |
| OpenAI init URL flag in bootstrap | `--base-url` | `--endpoint-url` | DOC-T04 |
| CLI next-step output in goal CLI | `python -m agent.cli_goals ...` | `ananta ...` | DOC-T06 |
| Demo flow default command path | `python -m agent.cli_goals ...` | `ananta ...` | DOC-T16 |
| Onboarding path ambiguity | mixed default narrative | explicit CLI-first vs full-stack split | DOC-T07 |
