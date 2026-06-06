# Planning Template Inventory (Current Hardcoded State)

## Scope

This inventory documents the current hardcoded planning template behavior in
`agent/services/planning_utils.py` as baseline for the catalog migration.

Task-specific agent behavior is documented separately in:

```text
docs/agent-profiles/README.md
docs/agent-profiles/profile-map.json
client_surfaces/operator_tui/AGENTS.md
```

The inventory below describes template resolution and baseline task shapes. The agent profiles describe how a selected path should behave once active.

### Hardcoded sources

1. `GOAL_TEMPLATES` (fachliche planning templates, keywords, subtasks)
2. `EXECUTION_FOCUSED_GOAL_HINTS` (fallback trigger hints)
3. `build_execution_focused_goal_template(...)` (fallback subtask generator)
4. `match_goal_template(...)` (resolution order and behavior)

### Current resolution behavior in `match_goal_template(...)`

1. Exact template-id match (`goal in GOAL_TEMPLATES`)
2. TDD keyword shortcut (`tdd`, `test-driven`, `red green`, ...)
3. Execution-focused fallback if one of `EXECUTION_FOCUSED_GOAL_HINTS` matches
4. Generic keyword scan across all `GOAL_TEMPLATES` entries
5. `None` if nothing matches

## Template matrix (all current GOAL_TEMPLATES entries)

| Template ID | Keywords | Subtasks | Metadata fields in subtasks | Mapping assessment | Related standard blueprints | Agent profile |
| --- | --- | ---: | --- | --- | --- | --- |
| `bug_fix` | bug, fix, fehler, error, crash, broken, kaputt | 5 | none | clean | Code-Repair | `docs/agent-profiles/bug_fix/AGENTS.md` |
| `feature` | feature, implement, add, neu, new, create, erstellen, erstelle, baue | 5 | none | partial | Scrum, Kanban | `docs/agent-profiles/feature/AGENTS.md` |
| `refactor` | refactor, cleanup, improve, optimieren, verbessern, clean | 4 | none | partial | Code-Repair, TDD | `docs/agent-profiles/refactor/AGENTS.md` |
| `test` | test, testing, coverage, unit test, integration test | 4 | none | partial | TDD, Code-Repair | `docs/agent-profiles/test/AGENTS.md` |
| `tdd` | tdd, test-driven, test driven, test-first, red green, red-green | 7 | `depends_on` | clean | TDD | `docs/agent-profiles/tdd/AGENTS.md` |
| `repo_analysis` | repo_analysis, projekt analysieren, analyse, struktur, risiken | 5 | none | partial | Research | `docs/agent-profiles/repo_analysis/AGENTS.md` |
| `sys_diag` | sys_diag, systemdiagnose, diagnose, fehler, logs, docker, testfehler | 5 | none | partial | Security-Review, Release-Prep | `docs/agent-profiles/sys_diag/AGENTS.md` |
| `admin_repair` | admin_repair, admin repair, windows 11 repair, ubuntu repair, bounded repair, diagnosis only | 6 | `artifact`, `depends_on`, `risk_focus`, `test_focus`, `review_focus` | partial | Release-Prep, Security-Review | `docs/agent-profiles/admin_repair/AGENTS.md` |
| `incident` | incident, notfall, ausfall, down, kritisch | 4 | none | partial | Security-Review, Release-Prep | `docs/agent-profiles/incident/AGENTS.md` |
| `architecture_review` | architecture_review, architekturreview, architektur, design review | 4 | none | partial | Research, Research-Evolution | `docs/agent-profiles/architecture_review/AGENTS.md` |
| `code_fix` | code_fix, codeproblem, beheben, patch | 5 | none | clean | Code-Repair | `docs/agent-profiles/code_fix/AGENTS.md` |
| `new_software_project` | new_software_project, neues softwareprojekt, neues projekt anlegen, projektstart | 6 | `artifact`, `depends_on`, `test_focus`, `review_focus` | planning-only/partial | Scrum, Kanban | `docs/agent-profiles/new_software_project/AGENTS.md` |
| `project_evolution` | project_evolution, existierendes projekt weiterentwickeln, weiterentwicklung, bestehendes projekt | 6 | `artifact`, `depends_on`, `risk_focus`, `test_focus` | partial | Research-Evolution, Scrum-OpenCode | `docs/agent-profiles/project_evolution/AGENTS.md` |

## Per-template details (keywords + subtasks)

### bug_fix

- Keywords: `bug`, `fix`, `fehler`, `error`, `crash`, `broken`, `kaputt`
- Agent profile: `docs/agent-profiles/bug_fix/AGENTS.md`
- Subtasks:
  - Bug reproduzieren
  - Root Cause Analyse
  - Fix implementieren
  - Test schreiben
  - Code Review

### feature

- Keywords: `feature`, `implement`, `add`, `neu`, `new`, `create`, `erstellen`, `erstelle`, `baue`
- Agent profile: `docs/agent-profiles/feature/AGENTS.md`
- Subtasks:
  - Anforderungen definieren
  - Design/Architektur
  - Implementierung
  - Tests schreiben
  - Dokumentation

### refactor

- Keywords: `refactor`, `cleanup`, `improve`, `optimieren`, `verbessern`, `clean`
- Agent profile: `docs/agent-profiles/refactor/AGENTS.md`
- Subtasks:
  - Code-Analyse
  - Refactoring-Plan
  - Refactoring durchfuehren
  - Tests verifizieren

### test

- Keywords: `test`, `testing`, `coverage`, `unit test`, `integration test`
- Agent profile: `docs/agent-profiles/test/AGENTS.md`
- Subtasks:
  - Test-Strategie
  - Unit Tests
  - Integration Tests
  - Coverage-Report

### tdd

- Keywords: `tdd`, `test-driven`, `test driven`, `test-first`, `red green`, `red-green`
- Agent profile: `docs/agent-profiles/tdd/AGENTS.md`
- Subtasks:
  - Verhalten und Akzeptanzgrenzen klaeren
  - Test zuerst schreiben oder anpassen
  - Red-Phase ausfuehren und Evidenz sichern
  - Minimalen Patch planen und umsetzen
  - Green-Phase verifizieren
  - Optional refactoren mit Sicherheitsnetz
  - Finale Verifikation und Abschluss

### repo_analysis

- Keywords: `repo_analysis`, `projekt analysieren`, `analyse`, `struktur`, `risiken`
- Agent profile: `docs/agent-profiles/repo_analysis/AGENTS.md`
- Subtasks:
  - Projektstruktur scannen
  - Abhaengigkeiten pruefen
  - Code-Qualitaet Stichproben
  - Sicherheits-Audit
  - Analyse-Bericht erstellen

### sys_diag

- Keywords: `sys_diag`, `systemdiagnose`, `diagnose`, `fehler`, `logs`, `docker`, `testfehler`
- Agent profile: `docs/agent-profiles/sys_diag/AGENTS.md`
- Subtasks:
  - Logs scannen
  - Laufzeitstatus pruefen
  - Build/Test Re-Run
  - Ursachenanalyse
  - Diagnose-Bericht

### admin_repair

- Keywords: `admin_repair`, `admin repair`, `windows 11 repair`, `ubuntu repair`, `bounded repair`, `diagnosis only`
- Agent profile: `docs/agent-profiles/admin_repair/AGENTS.md`
- Subtasks:
  - Use-case, scope und Modusgrenzen festhalten
  - Environment Summary und bounded evidence erfassen
  - Problemklasse und Diagnose-Artefakt ableiten
  - Repair actions mit hook-ready Feldern vorbereiten
  - Dry-run-first bounded repair plan erzeugen
  - Post-repair verification und Session Trail ausgeben

### incident

- Keywords: `incident`, `notfall`, `ausfall`, `down`, `kritisch`
- Agent profile: `docs/agent-profiles/incident/AGENTS.md`
- Subtasks:
  - Systemstatus pruefen
  - Eingrenzung
  - Mitigation
  - Post-Mortem

### architecture_review

- Keywords: `architecture_review`, `architekturreview`, `architektur`, `design review`
- Agent profile: `docs/agent-profiles/architecture_review/AGENTS.md`
- Subtasks:
  - Struktur-Audit
  - SOLID Check
  - Design-Dokumentation
  - Empfehlungsliste

### code_fix

- Keywords: `code_fix`, `codeproblem`, `beheben`, `patch`
- Agent profile: `docs/agent-profiles/code_fix/AGENTS.md`
- Subtasks:
  - Analyse & Reproduktion
  - Loesungskonzept
  - Patch erstellen
  - Verifikation
  - Review-Vorschlag

### new_software_project

- Keywords: `new_software_project`, `neues softwareprojekt`, `neues projekt anlegen`, `projektstart`
- Agent profile: `docs/agent-profiles/new_software_project/AGENTS.md`
- Legacy profile note: `docs/agent-profiles/new-software-project.md` exists as background documentation and should be consolidated later.
- Subtasks:
  - Projektidee und Grenzen klaeren
  - Projekt-Blueprint erstellen
  - Initiale Artefakte definieren
  - Initiales Task-Backlog erzeugen
  - Governance und sichere Startpfade pruefen
  - Erste Umsetzungsscheibe planen

### project_evolution

- Keywords: `project_evolution`, `existierendes projekt weiterentwickeln`, `weiterentwicklung`, `bestehendes projekt`
- Agent profile: `docs/agent-profiles/project_evolution/AGENTS.md`
- Subtasks:
  - Ist-Kontext und betroffene Bereiche schaerfen
  - Aenderungsziel und Restriktionen abgrenzen
  - Risiko-, Diff- und Testsicht erstellen
  - Aenderung in kleine Schritte zerlegen
  - Kleinste verifizierbare Aenderung vorbereiten
  - Review- und Rollback-Plan festlegen

## Execution-focused fallback (separate from GOAL_TEMPLATES)

- Triggered by `EXECUTION_FOCUSED_GOAL_HINTS` (coding/test/repo-related terms).
- Uses `build_execution_focused_goal_template(goal)` and currently emits a deterministic 4-step flow:
  1. implement change
  2. add automated tests
  3. execute and validate tests
  4. summarize changed files
- This fallback is independent behavior and must stay explicitly modeled during migration.

## Profile Migration Notes

The profile documentation state is now explicit:

- Root rules live in `AGENTS.md`.
- AI-Snake-Chat has `client_surfaces/operator_tui/AGENTS.md`.
- Each current hardcoded standard planning template has a corresponding `docs/agent-profiles/<template_id>/AGENTS.md`.
- `docs/agent-profiles/profile-map.json` records the mapping.

Runtime cleanup still needed:

```text
AgentProfileLoader
  -> resolve active profile by task mode/template/path
  -> compose root AGENTS.md + active AGENTS.md
  -> inject into OpenCode workspace AGENTS.md and ananta-worker context
```

Each profile should stay task-specific and must not change behavior for unrelated paths.
