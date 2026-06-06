# Planning Template Inventory (Current Hardcoded State)

## Scope

This inventory documents the current hardcoded planning template behavior in
`agent/services/planning_utils.py` as baseline for the catalog migration.

Task-specific agent behavior is documented separately in:

```text
docs/agent-profiles/README.md
docs/agent-profiles/new-software-project.md
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
| `bug_fix` | bug, fix, fehler, error, crash, broken, kaputt | 5 | none | clean | Code-Repair | pending |
| `feature` | feature, implement, add, neu, new, create, erstellen, erstelle, baue | 5 | none | partial | Scrum, Kanban | pending |
| `refactor` | refactor, cleanup, improve, optimieren, verbessern, clean | 4 | none | partial | Code-Repair, TDD | pending |
| `test` | test, testing, coverage, unit test, integration test | 4 | none | partial | TDD, Code-Repair | pending |
| `tdd` | tdd, test-driven, test driven, test-first, red green, red-green | 7 | `depends_on` | clean | TDD | pending |
| `repo_analysis` | repo_analysis, projekt analysieren, analyse, struktur, risiken | 5 | none | partial | Research | pending |
| `sys_diag` | sys_diag, systemdiagnose, diagnose, fehler, logs, docker, testfehler | 5 | none | partial | Security-Review, Release-Prep | pending |
| `admin_repair` | admin_repair, admin repair, windows 11 repair, ubuntu repair, bounded repair, diagnosis only | 6 | `artifact`, `depends_on`, `risk_focus`, `test_focus`, `review_focus` | partial | Release-Prep, Security-Review | pending |
| `incident` | incident, notfall, ausfall, down, kritisch | 4 | none | partial | Security-Review, Release-Prep | pending |
| `architecture_review` | architecture_review, architekturreview, architektur, design review | 4 | none | partial | Research, Research-Evolution | pending |
| `code_fix` | code_fix, codeproblem, beheben, patch | 5 | none | clean | Code-Repair | pending |
| `new_software_project` | new_software_project, neues softwareprojekt, neues projekt anlegen, projektstart | 6 | `artifact`, `depends_on`, `test_focus`, `review_focus` | planning-only/partial | Scrum, Kanban | `docs/agent-profiles/new-software-project.md` |
| `project_evolution` | project_evolution, existierendes projekt weiterentwickeln, weiterentwicklung, bestehendes projekt | 6 | `artifact`, `depends_on`, `risk_focus`, `test_focus` | partial | Research-Evolution, Scrum-OpenCode | pending |

## Per-template details (keywords + subtasks)

### bug_fix

- Keywords: `bug`, `fix`, `fehler`, `error`, `crash`, `broken`, `kaputt`
- Subtasks:
  - Bug reproduzieren
  - Root Cause Analyse
  - Fix implementieren
  - Test schreiben
  - Code Review

### feature

- Keywords: `feature`, `implement`, `add`, `neu`, `new`, `create`, `erstellen`, `erstelle`, `baue`
- Subtasks:
  - Anforderungen definieren
  - Design/Architektur
  - Implementierung
  - Tests schreiben
  - Dokumentation

### refactor

- Keywords: `refactor`, `cleanup`, `improve`, `optimieren`, `verbessern`, `clean`
- Subtasks:
  - Code-Analyse
  - Refactoring-Plan
  - Refactoring durchfuehren
  - Tests verifizieren

### test

- Keywords: `test`, `testing`, `coverage`, `unit test`, `integration test`
- Subtasks:
  - Test-Strategie
  - Unit Tests
  - Integration Tests
  - Coverage-Report

### tdd

- Keywords: `tdd`, `test-driven`, `test driven`, `test-first`, `red green`, `red-green`
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
- Subtasks:
  - Projektstruktur scannen
  - Abhaengigkeiten pruefen
  - Code-Qualitaet Stichproben
  - Sicherheits-Audit
  - Analyse-Bericht erstellen

### sys_diag

- Keywords: `sys_diag`, `systemdiagnose`, `diagnose`, `fehler`, `logs`, `docker`, `testfehler`
- Subtasks:
  - Logs scannen
  - Laufzeitstatus pruefen
  - Build/Test Re-Run
  - Ursachenanalyse
  - Diagnose-Bericht

### admin_repair

- Keywords: `admin_repair`, `admin repair`, `windows 11 repair`, `ubuntu repair`, `bounded repair`, `diagnosis only`
- Subtasks:
  - Use-case, scope und Modusgrenzen festhalten
  - Environment Summary und bounded evidence erfassen
  - Problemklasse und Diagnose-Artefakt ableiten
  - Repair actions mit hook-ready Feldern vorbereiten
  - Dry-run-first bounded repair plan erzeugen
  - Post-repair verification und Session Trail ausgeben

### incident

- Keywords: `incident`, `notfall`, `ausfall`, `down`, `kritisch`
- Subtasks:
  - Systemstatus pruefen
  - Eingrenzung
  - Mitigation
  - Post-Mortem

### architecture_review

- Keywords: `architecture_review`, `architekturreview`, `architektur`, `design review`
- Subtasks:
  - Struktur-Audit
  - SOLID Check
  - Design-Dokumentation
  - Empfehlungsliste

### code_fix

- Keywords: `code_fix`, `codeproblem`, `beheben`, `patch`
- Subtasks:
  - Analyse & Reproduktion
  - Loesungskonzept
  - Patch erstellen
  - Verifikation
  - Review-Vorschlag

### new_software_project

- Keywords: `new_software_project`, `neues softwareprojekt`, `neues projekt anlegen`, `projektstart`
- Agent profile: `docs/agent-profiles/new-software-project.md`
- Subtasks:
  - Projektidee und Grenzen klaeren
  - Projekt-Blueprint erstellen
  - Initiale Artefakte definieren
  - Initiales Task-Backlog erzeugen
  - Governance und sichere Startpfade pruefen
  - Erste Umsetzungsscheibe planen

### project_evolution

- Keywords: `project_evolution`, `existierendes projekt weiterentwickeln`, `weiterentwicklung`, `bestehendes projekt`
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

The current state is mixed:

- `new_software_project` now has an explicit agent profile.
- AI-Snake-Chat has a local `client_surfaces/operator_tui/AGENTS.md`.
- Other templates still rely on root `AGENTS.md`, planning prompts, and hardcoded subtasks.

Future cleanup should add profiles for at least:

```text
bug_fix
code_fix
refactor
repo_analysis
project_evolution
architecture_review
```

Each profile should stay task-specific and must not change behavior for unrelated paths.
