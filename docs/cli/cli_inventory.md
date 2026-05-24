# Ananta CLI Inventory

Stand: 2026-05-24. Alle ausführbaren Einstiegspunkte in diesem Repository.

## Klassifikationen

| Kürzel | Bedeutung |
|--------|-----------|
| **public** | Stabiler User-Befehl; `ananta <domain> <action>` |
| **shortcut** | Convenience-Alias auf `ananta goal create`; bleibt erhalten |
| **dev-cli** | Entwickler/CI-Befehl; `ananta dev ...`; nicht für Endnutzer |
| **internal** | Internes Hilfsskript; kein öffentlicher CLI-Anspruch |
| **deprecated** | Wird durch neuen Befehl ersetzt; Deprecation-Meldung vorhanden |

---

## agent/cli/ — Hauptmodul

| Datei | Befehl | Typ |
|-------|--------|-----|
| `agent/cli/main.py` | `ananta` (Entrypoint) | public |
| `agent/cli/doctor.py` | `ananta doctor` | public |
| `agent/cli/init_wizard.py` | `ananta init`, `ananta first-run` | public |
| `agent/cli/update.py` | `ananta update` | public |
| `agent/cli/voice_file.py` | `ananta voice-file` | public |
| `agent/cli/prompt_inspect.py` | `ananta prompt *` | public |
| `agent/cli/goal_aliases.py` | Shortcut-Layer für ask/plan/review/... | shortcut |
| `agent/cli/deployment_profile_writer.py` | Intern; von init_wizard genutzt | internal |

## agent/cli/commands/ — Domain-Module (neu)

| Modul | Befehle |
|-------|---------|
| `commands/config.py` | `ananta config show|validate|export|setup-planning|apply-profile` |
| `commands/runtime.py` | `ananta runtime list|inspect|recommend` |
| `commands/llm.py` | `ananta llm list|test|benchmark|log` |
| `commands/hub.py` | `ananta hub start|status|stop|logs` |
| `commands/worker.py` | `ananta worker list|register|start|status|logs` |
| `commands/goal.py` | `ananta goal create|list|inspect|status` + shortcuts |
| `commands/task.py` | `ananta task inspect|list|create|run|cancel` |
| `commands/project.py` | `ananta project init|scan|context` |
| `commands/rag.py` | `ananta rag index|query|explain|policy-check` |
| `commands/repair.py` | `ananta repair analyze|propose|run|verify` |
| `commands/prompt.py` | `ananta prompt inspect|render|goal-traces|...` |
| `commands/dev.py` | `ananta dev acceptance|check|audit|validate|smoke|benchmark|evidence|e2e|release-gate` |

---

## scripts/ — Skripte

### User-facing (für Migration / Deprecation geplant)

| Skript | Aktueller Befehl | Migrationsziel | Typ |
|--------|-----------------|----------------|-----|
| `scripts/goal_cli.py` | `python scripts/goal_cli.py run|status|goals|setup-planning` | `ananta goal create/list/status`, `ananta config setup-planning` | deprecated |

### Akzeptanz- und E2E-Tests (dev-cli)

| Skript | Migrationsziel |
|--------|----------------|
| `scripts/first_goal_acceptance_runner.py` | `ananta dev acceptance` |
| `scripts/run_e2e_dogfood_checks.py` | `ananta dev e2e` |
| `scripts/run_real_worker_runtime_evidence.py` | `ananta dev evidence real-worker-runtime` |
| `scripts/run_full_local_test_suite.py` | internal |
| `scripts/run_client_surface_test_gate.py` | internal |
| `scripts/run_tdd_blueprint_smoke.py` | internal |
| `scripts/run_worker_checks.py` | internal |
| `scripts/run_core_evidence_flow.py` | `ananta dev evidence core-flow` |
| `scripts/eclipse_ui_golden_path_runner.py` | internal |
| `scripts/run_eclipse_ui_golden_path.py` | internal |

### Smoke-Tests (dev-cli)

| Skript | Migrationsziel |
|--------|----------------|
| `scripts/smoke_client_golden_paths.py` | `ananta dev smoke client` |
| `scripts/smoke_eclipse_runtime_bootstrap.py` | `ananta dev smoke eclipse` |
| `scripts/smoke_eclipse_runtime_headless.py` | internal |
| `scripts/smoke_nvim_runtime.py` | `ananta dev smoke nvim` |
| `scripts/smoke_tui_runtime.py` | `ananta dev smoke tui` |
| `scripts/run_blender_smoke_checks.py` | `ananta dev smoke blender` |
| `scripts/run_blender_install_smoke.py` | internal |
| `scripts/run_blender_background_e2e.py` | internal |
| `scripts/run_freecad_smoke_checks.py` | `ananta dev smoke freecad` |
| `scripts/run_freecad_install_smoke.py` | internal |

### Benchmarks (dev-cli)

| Skript | Migrationsziel |
|--------|----------------|
| `scripts/benchmark_concurrency.py` | `ananta dev benchmark concurrency` |
| `scripts/retrieval_benchmark.py` | `ananta dev benchmark retrieval` |
| `scripts/bench_models_live.py` | `ananta dev benchmark models` |
| `scripts/run_live_click_dual_benchmark.py` | `ananta dev benchmark live-click` |
| `scripts/firefox_live_click_demo.py` | internal |
| `scripts/firefox_live_click_extended.py` | internal |
| `scripts/firefox_live_click_opencode_tui.py` | internal |
| `scripts/firefox_live_click_terminal_focus.py` | internal |

### Release / CI (dev-cli)

| Skript | Migrationsziel |
|--------|----------------|
| `scripts/check_pipeline.py` | `ananta dev check pipeline` |
| `scripts/release_gate.py` | `ananta dev release-gate` |
| `scripts/run_release_gate.py` | `ananta dev release-gate` |
| `scripts/validate_cross_track_dependencies.py` | `ananta dev validate cross-track-deps` |
| `scripts/validate_todo_consistency.py` | `ananta dev validate todo-consistency` |
| `scripts/run_security_invariant_checks.py` | `ananta dev check security-invariants` |
| `scripts/generate_release_sbom.py` | internal |
| `scripts/generate_quality_artifacts.py` | internal |

### Check / Audit (dev-cli)

| Skript | Migrationsziel |
|--------|----------------|
| `scripts/audit_client_surface_entrypoints.py` | `ananta dev audit client-surface` |
| `scripts/audit_domain_integrations.py` | `ananta dev audit domain-integrations` |
| `scripts/audit_runtime.py` | `ananta dev audit runtime` |
| `scripts/check_core_provider_boundaries.py` | `ananta dev check provider-boundaries` |
| `scripts/check_cycles.py` | `ananta dev check cycles` |
| `scripts/check_dead_code.py` | `ananta dev check dead-code` |
| `scripts/check_docs_present.py` | `ananta dev check docs` |
| `scripts/check_duplicates.py` | `ananta dev check duplicates` |
| `scripts/check_hotspot_guardrails.py` | `ananta dev check hotspot-guardrails` |
| `scripts/check_hub_storage.py` | `ananta dev check hub-storage` |
| `scripts/check_imports.py` | `ananta dev check imports` |
| `scripts/check_planning_contract.py` | `ananta dev check planning-contract` |
| `scripts/check_policy_and_routing.py` | `ananta dev check policy-and-routing` |
| `scripts/check_service_boundaries.py` | `ananta dev check service-boundaries` |
| `scripts/analyze_import_times.py` | internal |

### Build (internal)

| Skript | Typ |
|--------|-----|
| `scripts/build_blender_addon_package.py` | internal |
| `scripts/build_eclipse_runtime_plugin.py` | internal |
| `scripts/build_eclipse_update_site.py` | internal |
| `scripts/build_freecad_workbench_package.py` | internal |

### Dev / Demo (internal)

| Skript | Typ |
|--------|-----|
| `scripts/demo_plan.py` | internal |
| `scripts/evidence_hub_server.py` | internal |
| `scripts/evidence_worker_daemon.py` | internal |
| `scripts/evidence_worker_runtime.py` | internal |
| `scripts/goal_latency_diagnostics.py` | `ananta dev latency-diagnostics` |
| `scripts/test_env_cleanup.py` | internal |
| `scripts/test_env_cleanup_unit.py` | internal |

---

## Packaging

| Datei | Inhalt |
|-------|--------|
| `pyproject.toml` | `[project.scripts] ananta = "agent.cli.main:main"` |

## Docs

| Datei | Inhalt |
|-------|--------|
| `docs/cli/commands.md` | User-Pfad-Dokumentation |
| `docs/cli/developer_entrypoints.md` | Dev-Fallback-Pfade |
| `docs/cli/cli_inventory.md` | Diese Datei |
| `docs/cli/cli_taxonomy.md` | Command-Tree und Namensregeln |
| `docs/cli/cli_help_contract.md` | Help-Vertrag für alle Commands |
| `docs/cli/cli_migration.md` | Migrationspfade alt → neu |
| `docs/golden-path-cli.md` | Quickstart Golden Path |
| `docs/local-llm-cli-strategy.md` | LLM-Backend-Strategie |
