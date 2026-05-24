# Ananta CLI Migration Guide

Alte Befehle → neue kanonische Befehle.

## scripts/goal_cli.py → ananta goal / ananta config

| Alt | Neu | Notiz |
|-----|-----|-------|
| `python scripts/goal_cli.py run "Build API"` | `ananta goal create "Build API"` | `--profile` bleibt |
| `python scripts/goal_cli.py run "..." --profile X` | `ananta goal create "..." --profile X` | |
| `python scripts/goal_cli.py goals` | `ananta goal list` | |
| `python scripts/goal_cli.py status <id>` | `ananta goal status <id>` | Prefix-Match + Picker |
| `python scripts/goal_cli.py setup-planning` | `ananta config setup-planning` | Gleiche Payload |
| `python scripts/goal_cli.py setup-planning --git-workspace` | `ananta config setup-planning --git-workspace` | |
| `python scripts/goal_cli.py setup-planning --artifact-sync` | `ananta config setup-planning --artifact-sync` | |

**scripts/goal_cli.py bleibt weiterhin lauffähig** (kein Wrapper nötig; Datei ist unverändert).

---

## scripts/first_goal_acceptance_runner.py → ananta dev acceptance

| Alt | Neu |
|-----|-----|
| `python scripts/first_goal_acceptance_runner.py --scenario-file scenario_lmstudio.json --sla-seconds 900 --password test123` | `ananta dev acceptance --scenario-file scenario_lmstudio.json --sla-seconds 900 --password test123` |

Alle Argumente werden 1:1 an das Originalskript weitergereicht.

---

## ananta llm-log → ananta llm log

| Alt | Neu |
|-----|-----|
| `ananta llm-log tail` | `ananta llm log tail` |
| `ananta llm-log tail --limit 10` | `ananta llm log tail --limit 10` |
| `ananta llm-log tail --goal-id <id>` | `ananta llm log tail --goal-id <id>` |

`ananta llm-log` bleibt als Compat-Alias erhalten.

---

## Flat Goal-Aliases → ananta goal (oder als Shortcut behalten)

Alle folgenden Flat-Commands bleiben **dauerhaft** erhalten:

| Alt (bleibt) | Domain-Äquivalent |
|-------------|-------------------|
| `ananta ask "..."` | `ananta goal ask "..."` |
| `ananta plan "..."` | `ananta goal plan "..."` |
| `ananta analyze "..."` | `ananta goal ask "..."` |
| `ananta review "..."` | `ananta goal review "..."` |
| `ananta diagnose "..."` | `ananta goal diagnose "..."` |
| `ananta patch "..."` | `ananta goal patch "..."` |
| `ananta repair-admin "..."` | `ananta goal repair-admin "..."` |
| `ananta new-project "..."` | `ananta goal new-project "..."` |
| `ananta evolve-project "..."` | `ananta goal evolve-project "..."` |

---

## Dev/CI-Skripte → ananta dev check / audit / validate

### Check-Skripte

| Alt | Neu |
|-----|-----|
| `python scripts/check_pipeline.py` | `ananta dev check pipeline` |
| `python scripts/check_cycles.py` | `ananta dev check cycles` |
| `python scripts/check_dead_code.py` | `ananta dev check dead-code` |
| `python scripts/check_docs_present.py` | `ananta dev check docs` |
| `python scripts/check_duplicates.py` | `ananta dev check duplicates` |
| `python scripts/check_imports.py` | `ananta dev check imports` |
| `python scripts/check_planning_contract.py` | `ananta dev check planning-contract` |
| `python scripts/check_policy_and_routing.py` | `ananta dev check policy-and-routing` |
| `python scripts/check_service_boundaries.py` | `ananta dev check service-boundaries` |
| `python scripts/check_core_provider_boundaries.py` | `ananta dev check provider-boundaries` |
| `python scripts/check_hotspot_guardrails.py` | `ananta dev check hotspot-guardrails` |
| `python scripts/check_hub_storage.py` | `ananta dev check hub-storage` |
| `python scripts/run_security_invariant_checks.py` | `ananta dev check security-invariants` |

### Audit-Skripte

| Alt | Neu |
|-----|-----|
| `python scripts/audit_client_surface_entrypoints.py` | `ananta dev audit client-surface` |
| `python scripts/audit_domain_integrations.py` | `ananta dev audit domain-integrations` |
| `python scripts/audit_runtime.py` | `ananta dev audit runtime` |

### Validate-Skripte

| Alt | Neu |
|-----|-----|
| `python scripts/validate_cross_track_dependencies.py` | `ananta dev validate cross-track-deps` |
| `python scripts/validate_todo_consistency.py` | `ananta dev validate todo-consistency` |

### Release

| Alt | Neu |
|-----|-----|
| `python scripts/run_release_gate.py` | `ananta dev release-gate` |
| `python scripts/release_gate.py` | `ananta dev release-gate` |

### Smoke

| Alt | Neu |
|-----|-----|
| `python scripts/run_blender_smoke_checks.py` | `ananta dev smoke blender` |
| `python scripts/run_freecad_smoke_checks.py` | `ananta dev smoke freecad` |
| `python scripts/smoke_client_golden_paths.py` | `ananta dev smoke client` |
| `python scripts/smoke_eclipse_runtime_bootstrap.py` | `ananta dev smoke eclipse` |
| `python scripts/smoke_nvim_runtime.py` | `ananta dev smoke nvim` |
| `python scripts/smoke_tui_runtime.py` | `ananta dev smoke tui` |

### Benchmark

| Alt | Neu |
|-----|-----|
| `python scripts/benchmark_concurrency.py` | `ananta dev benchmark concurrency` |
| `python scripts/retrieval_benchmark.py` | `ananta dev benchmark retrieval` |
| `python scripts/bench_models_live.py` | `ananta dev benchmark models` |
| `python scripts/run_live_click_dual_benchmark.py` | `ananta dev benchmark live-click` |

### Evidence

| Alt | Neu |
|-----|-----|
| `python scripts/run_real_worker_runtime_evidence.py` | `ananta dev evidence real-worker-runtime` |
| `python scripts/run_core_evidence_flow.py` | `ananta dev evidence core-flow` |
| `python scripts/goal_latency_diagnostics.py` | `ananta dev latency-diagnostics` |
| `python scripts/run_e2e_dogfood_checks.py` | `ananta dev e2e` |

---

## Bekannte Breaking Changes

Keine. Alle alten Pfade bleiben lauffähig:
- `scripts/*.py` werden nicht gelöscht.
- Flat-Aliases werden nicht entfernt.
- `ananta llm-log` bleibt als Compat-Alias.
- `ananta goal|goals` bleibt als Compat-Alias.

---

## CI-Migration

Ersetze in CI-Pipelines:

```bash
# Alt
python scripts/first_goal_acceptance_runner.py --scenario-file ... --sla-seconds 900

# Neu (kanonisch)
ananta dev acceptance --scenario-file ... --sla-seconds 900
```

```bash
# Alt
python scripts/check_cycles.py

# Neu
ananta dev check cycles
```
