# Check-Skripte-Katalog (CLN-028)

Status der `scripts/check_*.py`-Detektoren (Stand 2026-11-01, nach M1-M9-Ausbau). Alle Skripte
sind in der Check-Umgebung lauffähig (reines stdlib + deklarierte Deps); der
Katalog dient als Referenz für CI-Anbindung und Weiterentwicklung.

**Aufruf:** grundsätzlich aus dem Repo-Root (`python scripts/check_x.py`).
Skripte, die `agent.*` importieren, tragen einen `sys.path`-Bootstrap und sind
aufrufunabhängig. Exit-Codes: 0 = sauber, 1 = Befund/Verstoss, 2 = Detektor-
Fehler oder fehlende Argumente.

## Gate-Detektoren (exit 1 bei Verstoss, in CI verankert oder vorgesehen)

| Skript | Status | Anmerkung |
|---|---|---|
| `check_cli_backend_shim_imports.py` | gruen | Legacy-`agent.common.sgpt_*`-Import-Gate |
| `check_shim_imports.py` | gruen | allgemeines Shim-Import-Gate |
| `check_imports.py` | 19 bekannte Verstoesse | Memory-Subsystem + self_development; Fix unter CLN-047, nicht rebaseline |
| `check_service_boundaries.py` | 2 bekannte Verstoesse | `self_development.runtime -> routes` (CLN-051) |
| `check_collaboration_workspace_boundaries.py` | gruen | |
| `check_core_provider_boundaries.py` | gruen | |
| `check_dendritic_memory_boundaries.py` | gruen | |
| `check_dspy_import_boundaries.py` | gruen | |
| `check_meet_worker_boundaries.py` | gruen | worker/meet_media-Paketgrenzen |
| `check_research_training_boundaries.py` | gruen | |
| `check_spreadsheet_studio_boundaries.py` | gruen | |
| `check_source_control_boundaries.py` | gruen (langsam, ~20-60 s) | Agent-Import-Startup dominiert |
| `check_memory_legacy_access.py` | gruen (langsam, ~20-60 s) | dito |
| `check_memory_channels.py` | 2 Befunde | `worker/harness/sandbox.py`, `worker/training/growth/checkpoint.py` |
| `check_cycles.py` | gruen | CLN-027: pure-stdlib Tarjan-SCC; Baseline `reports/import-cycle-baseline.txt` |
| `check_worker_boundaries.py` | gruen (mit Baseline) | CLN-029/CLN-055: worker->agent.* und agent/services->worker.* Kopplung; Baseline `reports/worker-boundary-baseline.txt` (91 Dateien, CLN-054 räumt ab) |
| `check_complexity_suppressions.py` | gruen | verbietet `# noqa: C901` |
| `check_hotspot_guardrails.py` | gruen (mit Allowlist) | CLN-026: `--fail-on-over-budget`, Ausnahmen in `reports/hotspot-budget-allowlist.json` |
| `check_singleton_freeze.py` | gruen | CLN-035: Freeze der get_*_service-Boilerplate (343 Module, reports/singleton-freeze.txt) |
| `check_test_line_budget.py` | gruen (mit Allowlist) | CLN-046: Test-Budget 1200 Zeilen, reports/test-line-budget-allowlist.json |
| `check_cli_backend_context_seam.py` | gruen | CLN-052: cli_backends erreichen services nur ueber default_context |
| `check_worker_boundaries.py` | gruen (mit Baseline) | CLN-029/055: hub/worker Importgrenzen |
| `check_output_boundary.py` | gruen | CLN-014: Runtime-Artefakte duerfen nicht getrackt sein |
| `check_docs_present.py` | gruen | |
| `check_planning_contract.py` | gruen | |
| `check_policy_and_routing.py` | gruen | |
| `check_hub_storage.py` | gruen | |
| `check_semantic_media_python_build.py` | gruen | |

## Release-Gates mit Evidenz-Argument (benoetigen Input, exit 2 ohne)

| Skript | Status | Anmerkung |
|---|---|---|
| `check_tiny_tool_router.py` | lauffaehig | Gate ueber Manifest/Evidenz; ohne Input `status: failed` |
| `check_source_control_release_gate.py` | lauffaehig | benoetigt `manifest`-Argument |
| `check_dspy_dependency_advisories.py` | lauffaehig | benoetigt `REPORT.json` |
| `check_hrm_experiments_release_gate.py` | gruen | |
| `check_hrm_research_pack.py` | gruen | |
| `check_knowledge_hygiene_release_gate.py` | gruen | |
| `check_python_verification_gate.py` | gruen | |
| `check_spreadsheet_studio_research_pack.py` | gruen | sys.path-Bootstrap ergaenzt (CLN-028) |

## Umgebungs-/Zustands-Detektoren (ergebnisabhaengig)

| Skript | Status | Anmerkung |
|---|---|---|
| `check_meet_runtime_lock.py` | lokaler Befund | prueft Lock-Zustand der Meet-Runtime |
| `check_meet_live_readiness.py` | gruen | sys.path-Bootstrap ergaenzt (CLN-028) |
| `check_meet_media_local.py` | lokaler Befund | braucht lokale Meet-Media-Runtime |
| `check_python_verification_supply_chain.py` | Env-Drift | vergleicht installierte gegen erwartete Versionen |

## Treiber / langsam (keine Einzel-Gates)

| Skript | Status | Anmerkung |
|---|---|---|
| `check_pipeline.py` | Treiber | ruft die Gates nach Modus auf (fast/standard/deep) |
| `check_workflow_gates.py` | Treiber | startet pytest-Subset (WFG-*) |
| `check_duplicates.py` | zu langsam als Gate | O(n^2)-difflib ueber 2.500 Module; nicht in CI, strukturelle Duplikate werden anders gefangen (CLN-005-Analyse) |
| `check_dead_code.py` | informativ | AST-Heuristik, immer exit 0; zu laut fuer ein Gate |

## CI-Anbindung (Stand)

- `check_pipeline.py --mode standard` (CI): format, lint, mypy (skip-types aktiv),
  check_imports, check_cycles, check_worker_boundaries, check_duplicates,
  check_dead_code, check_complexity_suppressions, check_hotspot_budgets,
  fast tests
- zusaetzlich in Workflows einzeln: `check_cli_backend_shim_imports.py` (Release-Gate),
  Release-Gates der Subtracks, `check_output_boundary` ueber Release-Regel
- Nach CLN-047/CLN-051 werden `check_imports` und `check_service_boundaries`
  gruen und muessen in keinem Baseline-Modus mehr gefuehrt werden.
