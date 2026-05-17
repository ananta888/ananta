# First Goal Scenario Acceptance

## Target Mode
Use `goal_scoped` as default mode for scenario acceptance.

## Scenario Matrix
- `opencode_preconfigured`
- `opencode_ollama_local`
- `ananta_ollama_local`

## Runner
```bash
python scripts/first_goal_acceptance_runner.py \
  --config-mode goal_scoped \
  --scenario-repeats 3 \
  --parallel-goals-per-scenario 1 \
  --reset-db
```

## Legacy Fallback Mode
Use only for compatibility/regression checks:
```bash
python scripts/first_goal_acceptance_runner.py --config-mode legacy_global_config
```

Parallel mode is blocked for `legacy_global_config` unless explicitly enabled with `--allow-unsafe-global-parallel`.

## Required Evidence
Each run should include:
- `config_mode`
- `config_profile`
- `config_checksum`
- `goal_config_source`
- `effective_config_endpoint_status`
- `final_goal_status`

## Pass Conditions
- no cross-scenario config contamination
- workspace write phase reached
- verification signal present
- goal terminates (`completed` or clean `failed`)
