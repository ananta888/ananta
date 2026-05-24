# Ananta CLI Taxonomy

Verbindliche Command-Struktur für `ananta`. Grundlage für Help-Vertrag, Tests und Migrations-Planung.

## Grundprinzipien

| Ebene | Muster | Beispiel |
|-------|--------|---------|
| Top-Level | `ananta` | `ananta --help` |
| Domain-Gruppe | `ananta <domain>` | `ananta config --help` |
| Leaf-Command | `ananta <domain> <action>` | `ananta config show` |

- Bestehende Flat-Commands (`ask`, `plan`, `review`, etc.) bleiben als **Convenience-Shortcuts** erhalten.
- Dev/CI-Commands liegen **ausschließlich** unter `ananta dev ...`.

## Standard-Actions (Namensregeln)

| Action | Bedeutung | Scope |
|--------|-----------|-------|
| `list` | Listet eine Sammlung | Alle Domains mit Mehrzahl-Objekten |
| `inspect` | Zeigt ein einzelnes Objekt im Detail | Alle Domains mit Einzelobjekten |
| `create` | Erstellt ein neues Objekt | Mutierende Domains |
| `show` | Zeigt aktuellen Zustand (ohne Objekt-ID) | Config, Hub, Status |
| `status` | Zeigt Status eines Objekts | Goal, Task, Hub, Worker |
| `validate` | Prüft Struktur/Konfiguration, ohne zu mutieren | Config, Dev |
| `export` | Exportiert Daten als Datei oder JSON | Config |
| `run` | Führt explizit eine Aktion aus | Task, Dev |
| `start` | Startet einen Prozess | Hub, Worker |
| `stop` | Stoppt einen Prozess | Hub, Worker |
| `logs` | Zeigt Log-Output | Hub, Worker |
| `apply-*` | Wendet eine benannte Config an | Config |
| `setup-*` | Einmaliger Setup-Schritt | Config |

**Synonyme-Entscheidungen:**
- `show` vs `inspect`: `show` für stateless Konfig/Status; `inspect` für Objekte mit ID.
- `get` ist verboten — immer `show` oder `inspect`.
- `print` ist verboten — Ausgabe ist implizit.
- `view` ist verboten — zu generisch.

## Command-Tree

```
ananta
├── init                    [public] Initialize runtime/profile/config
├── first-run               [public] Interactive first-run wizard
├── doctor                  [public] Local environment checks
├── status                  [public] Hub and agent status (flat compat)
├── update                  [public] Update CLI or hub components
│
├── config
│   ├── show                [public, read] Effective config from hub
│   ├── validate            [public, read] Validate local config.json
│   ├── export              [public, read] Export config as JSON
│   ├── setup-planning      [public, MUTATING] Apply LMStudio planning policy
│   └── apply-profile       [public, MUTATING] Apply a named config profile
│
├── runtime
│   ├── list                [public, read]
│   ├── inspect             [public, read]
│   └── recommend           [public, read]
│
├── llm
│   ├── list                [public, read]
│   ├── test                [public, read]
│   ├── benchmark           [public, read]
│   └── log
│       └── tail            [public, read]
│
├── hub
│   ├── start               [public, MUTATING]
│   ├── status              [public, read]
│   ├── stop                [public, MUTATING]
│   └── logs                [public, read]
│
├── worker
│   ├── list                [public, read]
│   ├── register            [public, MUTATING]
│   ├── start               [public, MUTATING]
│   ├── status              [public, read]
│   └── logs                [public, read]
│
├── goal
│   ├── create              [public, MUTATING]
│   ├── list                [public, read]
│   ├── inspect             [public, read]  ← interactive ID picker
│   ├── status              [public, read]  ← interactive ID picker
│   ├── ask                 [shortcut → create mode=generic]
│   ├── plan                [shortcut → create mode=generic]
│   ├── review              [shortcut → create mode=generic]
│   ├── diagnose            [shortcut → create mode=generic]
│   ├── patch               [shortcut → create mode=generic]
│   ├── repair-admin        [shortcut → create mode=generic]
│   ├── new-project         [shortcut → create mode=new_software_project]
│   └── evolve-project      [shortcut → create mode=generic]
│
├── task
│   ├── inspect             [public, read]  ← interactive ID picker
│   ├── list                [public, read]
│   ├── create              [planned, MUTATING]
│   ├── run                 [planned, MUTATING]
│   └── cancel              [planned, MUTATING]
│
├── project
│   ├── init                [planned, MUTATING]
│   ├── scan                [planned, read]
│   └── context             [planned, read]
│
├── rag
│   ├── index               [planned, MUTATING]
│   ├── query               [planned, read]
│   ├── explain             [planned, read]
│   └── policy-check        [planned, read]
│
├── repair
│   ├── analyze             [planned, read]
│   ├── propose             [planned, read]
│   ├── run                 [planned, MUTATING]
│   └── verify              [planned, read]
│
├── prompt
│   ├── inspect             [public, read]
│   ├── render              [public, read]
│   ├── goal-traces         [public, read]
│   ├── goal-report         [public, read]
│   ├── delegation-report   [public, read]
│   ├── task-report         [public, read]
│   ├── task-traces         [public, read]
│   ├── task-inspect        [public, read]
│   ├── task-why            [public, read]
│   ├── learning-report     [public, read]
│   ├── learning-status     [public, read]
│   ├── planner-profiles    [public, read]
│   ├── goal-flows          [public, read]
│   ├── goal-stuck          [public, read]
│   ├── goal-execmap        [public, read]
│   ├── artifact-provenance [public, read]
│   ├── goal-artifact-matrix [public, read]
│   └── goal-worker-traces  [public, read]
│
└── dev                     [dev/CI only — not shown in user --help]
    ├── acceptance           ← scripts/first_goal_acceptance_runner.py
    ├── e2e                  ← scripts/run_e2e_dogfood_checks.py
    ├── release-gate         ← scripts/run_release_gate.py
    ├── latency-diagnostics  ← scripts/goal_latency_diagnostics.py
    ├── check
    │   ├── pipeline         ← scripts/check_pipeline.py
    │   ├── cycles           ← scripts/check_cycles.py
    │   ├── dead-code        ← scripts/check_dead_code.py
    │   ├── docs             ← scripts/check_docs_present.py
    │   ├── duplicates       ← scripts/check_duplicates.py
    │   ├── imports          ← scripts/check_imports.py
    │   ├── planning-contract ← scripts/check_planning_contract.py
    │   ├── policy-and-routing ← scripts/check_policy_and_routing.py
    │   ├── service-boundaries ← scripts/check_service_boundaries.py
    │   ├── provider-boundaries ← scripts/check_core_provider_boundaries.py
    │   ├── hotspot-guardrails ← scripts/check_hotspot_guardrails.py
    │   ├── hub-storage      ← scripts/check_hub_storage.py
    │   └── security-invariants ← scripts/run_security_invariant_checks.py
    ├── audit
    │   ├── client-surface   ← scripts/audit_client_surface_entrypoints.py
    │   ├── domain-integrations ← scripts/audit_domain_integrations.py
    │   └── runtime          ← scripts/audit_runtime.py
    ├── validate
    │   ├── cross-track-deps ← scripts/validate_cross_track_dependencies.py
    │   └── todo-consistency ← scripts/validate_todo_consistency.py
    ├── smoke
    │   ├── blender          ← scripts/run_blender_smoke_checks.py
    │   ├── freecad          ← scripts/run_freecad_smoke_checks.py
    │   ├── client           ← scripts/smoke_client_golden_paths.py
    │   ├── eclipse          ← scripts/smoke_eclipse_runtime_bootstrap.py
    │   ├── nvim             ← scripts/smoke_nvim_runtime.py
    │   └── tui              ← scripts/smoke_tui_runtime.py
    ├── benchmark
    │   ├── concurrency      ← scripts/benchmark_concurrency.py
    │   ├── retrieval        ← scripts/retrieval_benchmark.py
    │   ├── models           ← scripts/bench_models_live.py
    │   └── live-click       ← scripts/run_live_click_dual_benchmark.py
    └── evidence
        ├── real-worker-runtime ← scripts/run_real_worker_runtime_evidence.py
        └── core-flow        ← scripts/run_core_evidence_flow.py
```

## Backward-Compat-Layer

Folgende Flat-Commands bleiben dauerhaft erhalten (keine Entfernung geplant):

| Flat-Command | Mapped auf |
|-------------|-----------|
| `ananta ask <goal>` | `ananta goal ask <goal>` |
| `ananta plan <goal>` | `ananta goal plan <goal>` |
| `ananta analyze <goal>` | `ananta goal ask <goal>` |
| `ananta review <goal>` | `ananta goal review <goal>` |
| `ananta diagnose <goal>` | `ananta goal diagnose <goal>` |
| `ananta patch <goal>` | `ananta goal patch <goal>` |
| `ananta repair-admin <goal>` | `ananta goal repair-admin <goal>` |
| `ananta new-project <goal>` | `ananta goal new-project <goal>` |
| `ananta evolve-project <goal>` | `ananta goal evolve-project <goal>` |
| `ananta llm-log tail` | `ananta llm log tail` |
| `ananta prompt *` | `ananta prompt *` (unverändert) |
| `ananta task inspect` | `ananta task inspect` (unverändert) |
| `ananta status` | Flat-Status-View (bleibt) |
| `ananta doctor` | `ananta doctor` (unverändert) |
| `ananta init` | `ananta init` (unverändert) |
