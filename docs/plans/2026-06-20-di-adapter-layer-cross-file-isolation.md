# DI-Adapter-Layer für Ananta Services — Cross-File-Order Isolation

> **For Hermes:** Dieser Plan wird via subagent-driven-development ausgeführt, eine Welle pro Schritt mit Two-Stage-Review (Spec, dann Code-Quality).

**Goal:** Systemweite DI-Infrastruktur für Service-Klassen, die Modul-Global-Repos in `__init__` einfangen. Eliminiert Cross-File-Test-Order-Flakes (STAB-OPEN-1, STAB-OPEN-2) durch Aufrufzeit-Lookup statt Initial-Cache. SOLID-konform: Dependency Inversion Principle.

**Architecture:**
1. Neuer `agent.services.di` Layer mit `get_X_service()` Factory-Funktionen für jede der 8 problematischen Services.
2. Refactor-Pattern: `self._X = module_global_X` → `@property` mit Aufrufzeit-`get_X_repository()`-Lookup. Konstruktor akzeptiert weiterhin Optional-Override für Tests.
3. Kompatibilität: Bestehende Modul-Level Singletons bleiben; neue Factory ist die kanonische Quelle.

**Tech Stack:** Python 3.11, pytest, monkeypatch, SQLModel/SQLAlchemy. Keine neuen Dependencies.

**SOLID-Begründung:**
- **DIP**: Services dependieren auf `get_X_repository()`-Abstraktion (call-time lookup), nicht auf das konkrete Singleton-Objekt zum Modul-Import-Zeitpunkt.
- **OCP**: Erweitern um neue Repositories ohne Service-Konstruktoren anzufassen.
- **SRP**: `di.py` macht genau eine Sache (Factory-Funktionen); Services machen weiterhin ihre Domänenlogik.
- **LSP**: `get_X_repository()` ist substitutierbar — Tests können den Aufruf monkeypatchen, Produktion nutzt den Default-Lookup.
- **ISP**: Schmale Factory-Interfaces pro Service, keine Catch-All-DI-Container.

**Breaking-Change-Strategie:** Additive, nicht-breaking. Die alten Modul-Singletons bleiben exportiert. Neue `get_X_service()`-Factories sind die empfohlene Aufruf-Form. Bestehende Aufrufer funktionieren weiterhin (sie greifen via `from agent.repository import X_repo` zu, der weiterhin dasselbe Objekt liefert). Refactor der Konsumenten erfolgt in Wellen.

---

## Discovery-Report (Source-First Sweep)

### Bestätigte Befunde

**Befund 1 — Modul-Level Singleton-Pattern in 8 Services:**
```
agent/services/result_memory_service.py:9                # memory_entry_repo
agent/services/heuristic_runtime/heuristic_tool.py:90    # DecisionTraceRepository
agent/services/heuristic_runtime/heuristic_selection_service.py:87
agent/services/heuristic_runtime/chat_decision_manager.py:51-52
agent/services/heuristic_runtime/snake_decision_manager.py:136-137
agent/services/planning_track_task_integration_service.py:80
```
Muster: `from agent.repository import X_repo` (Modul-Level) + `self._X = X_repo` in `__init__`. Service friert die Referenz zur Instanzierungszeit ein.

**Befund 2 — 23 Service-Module mit Modul-Level Repo-Imports:**
```
agent/services/{ingestion, openai_compat, result_memory, planning_track_task_integration,
repository_registry, worker_pool_scheduler, system_stats, worker_job, task_queue,
task_neighborhood, rag_helper_index, retrieval, autopilot_runtime, system_health, lifecycle,
knowledge_index_retrieval, context_manager, benchmark_job, trigger_runtime,
task_runtime, request_cancellation, retrieval_query_builder}.py
```
plus 8 weitere Module außerhalb `agent/services/` (routes, scheduler, tools, archive_utils, etc.).

**Befund 3 — Test-Mutation-Punkte auf demselben Singleton:**
- `tests/test_awf_worker_fixup_t021_t030.py` (8x) patcht `agent.repository.memory_entry_repo.save` als Objekt-Attribut
- `tests/test_result_memory_and_federation.py:39` patcht `agent.services.result_memory_service.memory_entry_repo` als Modul-Symbol
- Konflikt: `monkeypatch` restored Modul-Symbol-Rebinding und Objekt-Attribut-Rebinding in unterschiedlichen Lebenszyklen.

**Befund 4 — Modul-Globale `result_memory_service` Initialisierung:**
- `agent/services/result_memory_service.py:432` — `result_memory_service = ResultMemoryService(memory_entry_repository=memory_entry_repo)` wird beim ersten Import einmalig mit der Singleton-Instanz zu diesem Zeitpunkt instanziiert. Spätere Tests, die ein anderes Repo injizieren, sehen diesen Service nicht.

### Verdict
- Subsystem existiert wie benannt (`agent.services.*` + `agent.repository`).
- Pitfall 11b dokumentiert; Fix-Versuche Property-Lookup und conftest-autouse bereits in vorheriger Session durchgespielt — halfen nicht, weil der `result_memory_service` Modul-Global selbst zur Import-Zeit festgelegt wird.
- DI-Factory-Layer ist die saubere Lösung.

---

## Welle 0 — Pre-Flight

### Task 0.1: Test-Reproduktion von STAB-OPEN-1

**Objective:** Ein deterministisches Skript schreiben, das den `MagicMock name='mock.save().id'`-Fehler zuverlässig reproduziert — damit jede Welle messbar verifizierbar ist.

**Files:**
- Create: `tests/scratch/test_di_repro_stab_open_1.py`

**Step 1:** Test schreiben, der `test_awf_worker_fixup_t021_t030.py::test_enabled_false_skips_write` gefolgt von `test_result_memory_service.py::test_result_memory_handles_missing_optional_fields_without_silent_inconsistency` in einer Session ausführt und das Failure-Muster einfängt.

**Step 2:** Run, Failure-Modus bestätigen.

**Step 3:** Skript dokumentieren als `STAB_REPRO_BASELINE`.

**Step 4:** Commit: `test: add STAB-OPEN-1 reproducer baseline`

---

## Welle 1 — DI-Adapter-Layer (`agent/services/di.py`)

### Task 1.1: `di.py` Skeleton

**Objective:** Modul anlegen mit Factory-Funktionen, die per Aufrufzeit-Lookup das aktuelle Modul-Singleton liefern.

**Files:**
- Create: `agent/services/di.py`

**Step 1 — Failing test:**
```python
# tests/test_di_adapter.py
def test_di_returns_singleton_object():
    from agent.services.di import get_memory_entry_repository
    repo_a = get_memory_entry_repository()
    repo_b = get_memory_entry_repository()
    assert repo_a is repo_b  # idempotent
```
**Expected:** `ModuleNotFoundError: agent.services.di`.

**Step 2 — Implementierung:**
```python
# agent/services/di.py
"""DI-Adapter-Layer: Factory-Funktionen, die zur Aufrufzeit das aktuelle
Modul-Singleton-Repository liefern.

Eliminiert Cross-File-Test-Order-Kontamination, weil Tests via
`monkeypatch.setattr("agent.services.di.memory_entry_repo", fake_repo)`
das Symbol rebinden können, ohne dass ein Service die alte Referenz
in __init__ eingefroren hat.

SOLID: DIP (Abhängigkeit von Aufrufzeit-Abstraktion), OCP (neue Repos
ohne Service-Änderung), SRP (nur Factory-Logik).
"""
from __future__ import annotations
from typing import Any

# Late-Binding Pattern: re-export der Modul-Globals mit Funktion.
# Produktionscode: get_memory_entry_repository() → agent.repository.memory_entry_repo (lookup zur Aufrufzeit)
# Test-Code: monkeypatch.setattr("agent.services.di.memory_entry_repo", fake_repo)
#           → nächster Aufruf von get_memory_entry_repository() liefert fake_repo.

# Diese Symbole werden in den Folge-Tasks mit re-exports gefüllt.
__all__ = [
    "get_memory_entry_repository",
    "get_artifact_repository",
    "get_task_repository",
    # ... weitere in späteren Tasks
]


def get_memory_entry_repository() -> Any:
    """Liefert das aktuelle MemoryEntryRepository-Singleton (Aufrufzeit-Lookup)."""
    from agent.repository import memory_entry_repo
    return memory_entry_repo


def get_artifact_repository() -> Any:
    from agent.repository import artifact_repo
    return artifact_repo


def get_task_repository() -> Any:
    from agent.repository import task_repo
    return task_repo
```

**Step 3 — Run:** `pytest tests/test_di_adapter.py -v` → PASS.

**Step 4 — Commit:** `feat(di): add DI-adapter-layer with call-time repository lookups`

### Task 1.2: `di.py` auf alle 23 betroffenen Repos erweitern

**Objective:** Vollständige Factory-Funktions-Liste für alle Repos, die in den 8 problematischen Services + den 23 Modul-Level-Import-Stellen genutzt werden.

**Files:**
- Modify: `agent/services/di.py`

**Step 1 — Audit-Liste:** (aus search-Ergebnis ableiten)
```
memory_entry_repo, artifact_repo, artifact_version_repo, extracted_document_repo,
goal_repo, task_repo, config_repo, banned_ip_repo, login_attempt_repo,
context_bundle_repo, retrieval_run_repo, worker_job_repo, worker_result_repo,
worker_slot_lease_repo, knowledge_index_repo, knowledge_index_run_repo,
team_repo, agent_repo, scheduled_task_repo, refresh_token_repo, user_repo,
playbook_repo, action_pack_repo, agent_repo, artifact_repo,
artifact_version_repo, knowledge_collection_repo, knowledge_index_repo,
knowledge_index_run_repo, knowledge_link_repo, user_instruction_profile_repo,
instruction_overlay_repo, retrieval_run_repo, context_bundle_repo,
context_access_policy_repo, worker_job_repo, worker_result_repo,
worker_slot_lease_repo, evolution_run_repo, evolution_proposal_repo,
memory_entry_repo, team_repo, template_repo, scheduled_task_repo,
task_repo, archived_task_repo, config_repo, goal_repo, plan_repo,
plan_node_repo, policy_decision_repo, verification_record_repo,
stats_repo, audit_repo, login_attempt_repo, banned_ip_repo,
password_history_repo, team_type_repo, role_repo, team_member_repo,
team_blueprint_repo, blueprint_role_repo, blueprint_artifact_repo,
blueprint_workflow_step_repo, team_type_role_link_repo,
planning_run_repo, planning_prompt_version_repo,
planning_model_profile_repo, planning_evaluation_repo,
planning_template_candidate_repo, planning_pattern_cluster_repo,
planning_review_item_repo, terminal_session_repo, terminal_event_repo,
agent_session_repo, tool_call_repo, policy_snapshot_repo
```
(automatisch generiert via `grep -rhoE 'from agent\.repository import ([^)]+)' agent/ | tr ',' '\n' | sort -u`)

**Step 2 — Implementierung:** für jeden Repo-Namen eine Factory-Funktion `get_X_repository()`. Pattern:
```python
def get_X_repository() -> Any:
    from agent.repository import X_repo
    return X_repo
```

**Step 3 — Test:** `test_di_factory_completeness` — assert dass alle 23 Modul-Level-Imports auch in `di.py` verfügbar sind.

**Step 4 — Run + Commit:** `feat(di): complete factory coverage for all 23 service-level repo imports`

---

## Welle 2 — `ResultMemoryService` als Pilot-Refactor

### Task 2.1: `ResultMemoryService` Property-Pattern

**Objective:** Service auf Property-Lookup umstellen. `self._memory_entry_repo` wird zu einem Property, das `di.get_memory_entry_repository()` aufruft.

**Files:**
- Modify: `agent/services/result_memory_service.py:82-96, 432-436`

**Step 1 — Failing test:** Bestehender `test_awf_worker_fixup_t021_t030.py` läuft allein grün, im Pair mit `test_result_memory_and_federation.py` muss er auch grün sein (cross-file-isolation test).

**Step 2 — Refactor:**
```python
class ResultMemoryService:
    def __init__(
        self,
        *,
        memory_entry_repository: Any = None,
        memory_tree_ingestion_service: Any = None,
        auto_ingest_tree: bool = False,
    ) -> None:
        # Override direkt im Konstruktor erlaubt (Test-DI).
        # Kein module-globales Fallback mehr — Default ist di.get_memory_entry_repository().
        self._memory_entry_repository_override = memory_entry_repository
        self._memory_tree_ingestion_svc = memory_tree_ingestion_service
        self._auto_ingest_tree = auto_ingest_tree

    @property
    def _memory_entry_repo(self) -> Any:
        """Call-time repository lookup. Tests monkeypatchen di.memory_entry_repo;
        Aufrufzeit-Auflösung sieht den aktuellen Wert statt des __init__-Cache."""
        if self._memory_entry_repository_override is not None:
            return self._memory_entry_repository_override
        from agent.services.di import get_memory_entry_repository
        return get_memory_entry_repository()
```

**Step 3 — Modul-Globale Factory statt direktem Singleton:**
```python
# agent/services/result_memory_service.py:432 ersetzen
def get_result_memory_service() -> ResultMemoryService:
    """Factory: erstellt Service mit aktuellen di-Repos.
    Tests können via monkeypatch.setattr("agent.services.di.memory_entry_repo", fake)
    das gebundene Repo ersetzen, ohne dass ein Singleton-Init das einfriert.
    """
    return ResultMemoryService()
```

**Step 4 — Kompatibilitäts-Backward-Pointer:**
```python
# Am Ende der Datei:
result_memory_service: ResultMemoryService | None = None
def _get_or_create_result_memory_service() -> ResultMemoryService:
    global result_memory_service
    if result_memory_service is None:
        result_memory_service = ResultMemoryService()
    return result_memory_service
```

**Step 5 — Test:** `test_di_isolates_result_memory_cross_file` — beide Tests in einer Session, beide grün.

**Step 6 — Run + Commit:** `refactor(result-memory): switch to di-property for repository lookup (cross-file-isolation)`

### Task 2.2: Test-Update für direkten Singleton-Init

**Objective:** `test_awf_worker_fixup_t021_t030.py` und `test_result_memory_and_federation.py` patchen jetzt `agent.services.di.memory_entry_repo` statt `agent.repository.memory_entry_repo.save`.

**Files:**
- Modify: `tests/test_awf_worker_fixup_t021_t030.py` (8 Patches)
- Modify: `tests/test_result_memory_and_federation.py:39`

**Step 1 — Test-Updates:**
```python
# alt:
monkeypatch.setattr("agent.repository.memory_entry_repo.save", lambda e: saved.append(e) or e)
# neu:
monkeypatch.setattr("agent.services.di.memory_entry_repo", _FakeRepo())
```
wobei `_FakeRepo` ein minimaler Stub mit `.save(entry) -> entry` ist.

**Step 2 — Run:** beide Test-Dateien, in allen Kombinationen, alle grün.

**Step 3 — Commit:** `test(result-memory): switch monkeypatch target to di-layer symbol`

### Task 2.3: STAB-OPEN-1 Full-Run Verifikation

**Objective:** Beweisen, dass der Flake weg ist. Full-Run 3x, alle 0 Failures im result_memory-Bereich.

**Files:** keine Änderung.

**Step 1:** `python -m pytest tests/test_awf_worker_fixup_t021_t030.py tests/test_result_memory_and_federation.py tests/test_result_memory_service.py tests/test_memory_tree_store_service.py -v --count=3` (3 Iterationen).

**Step 2:** Full-Run: `python -m pytest tests/ -q 2>&1 | tail -20`.

**Step 3:** Beide grün. STAB-OPEN-1 status auf `done`.

**Step 4 — Commit:** `test(stab-open-1): verify flake eliminated by di-layer refactor`

---

## Welle 3 — Heuristic Runtime Services

### Befund: Welle 3 entfällt

**Source-First-Re-Verifikation hat ergeben, dass das ursprünglich identifizierte
``self._X = module_global_X``-Pattern in den Heuristic-Services und
``planning_track_task_integration_service`` KEIN Singleton-Cache-Problem
darstellt, sondern ein normales Default-Factory-Pattern.**

Konkret:

```python
# agent/services/heuristic_runtime/heuristic_tool.py:90
self._trace_repo = trace_repo or DecisionTraceRepository()  # <- Klassenkonstruktor, kein Modul-Singleton

# agent/services/heuristic_runtime/heuristic_selection_service.py:87
self._lease_repo = lease_repo or HeuristicLeaseRepository()  # <- dto

# agent/services/heuristic_runtime/chat_decision_manager.py:51-52
self._lease_repo = lease_repo or HeuristicLeaseRepository()
self._trace_repo = trace_repo or DecisionTraceRepository()  # <- dto

# agent/services/heuristic_runtime/snake_decision_manager.py:136-137
self._lease_repo = lease_repo or HeuristicLeaseRepository()
self._trace_repo = trace_repo or DecisionTraceRepository()  # <- dto

# agent/services/planning_track_task_integration_service.py:80
self._artifact_repo = goal_artifact_repository or GoalArtifactRepository()  # <- dto
```

Im Gegensatz zu ``result_memory_service.py:94``:
```python
self._memory_entry_repo = memory_entry_repository if memory_entry_repository is not None else memory_entry_repo
# ^^^^^ Modul-Level-Singleton-Cache (Import-time frozen)
```

**Unterschied:** Die Heuristic-Services importieren ``DecisionTraceRepository``
und ``HeuristicLeaseRepository`` als **Klassen** (nicht als Instanzen) und
rufen den Konstruktor in ``__init__`` auf. Es gibt **keinen** Modul-Level-Singleton,
dessen Referenz eingefroren werden könnte. Die ``or ClassName()``-Default-Factory
erzeugt jedes Mal eine frische Instanz, wenn kein Override übergeben wird.

**Konsequenz:** Das DI-Layer-Refactoring-Pattern (Property-Lookup via
``agent.services.di``) bringt hier keinen Nutzen — es gibt keine
Aufrufzeit-Referenz aufzulösen, weil kein Modul-Singleton existiert.

**Verifikation:** Audit-Rezept für zukünftige Re-Verifikation:

```bash
# Findet Singleton-Cache (das Problem):
grep -rn 'self\._[a-z_]\_repo\s*=\s*[a-z_]\_repo\b' agent/services/

# Findet Default-Factory (kein Problem):
grep -rn 'self\._[a-z_]\_repo\s*=\s*[a-z_]\+_repo or [A-Z]' agent/services/
```

Der erste Grep findet nur noch ``result_memory_service.py`` (via Property →
nicht mehr als Cache, sondern als late-binding). Der zweite findet die
Heuristic-Services — alle bestätigt als harmlos.

**Tests dazu:** Null Tests in ``tests/`` monkeypatchen
``DecisionTraceRepository`` oder ``HeuristicLeaseRepository``. Die Heuristic-
Services sind nicht in STAB-OPEN-1 oder STAB-OPEN-2 enthalten.

**Aktion:** Welle 3 + Welle 4 sind nicht erforderlich. Der systemweite
DI-Layer ist durch Welle 1 + Welle 2 abgedeckt. Welle 5 schließt mit
diesem Befund dokumentiert ab.

---

## Welle 4 — entfällt (siehe Welle 3 Befund)

Welle 4 (``planning_track_task_integration_service.py`` Property-Pattern
plus Grep-Verifikation) ist nach dem Welle-3-Befund obsolet. Der
Grep-Audit ist trotzdem sinnvoll und wird in Welle 5 nachgeholt.

---

## Welle 5 — Dokumentation + Tracker-Close

### Task 5.1: `docs/di-layer.md` schreiben

**Objective:** Pattern dokumentieren für zukünftige Services.

**Files:** Create `docs/di-layer.md`

**Inhalt:**
- Problem (Cross-File-Test-Order-Flakes durch Module-Singleton-Cache)
- Pattern (Property-Lookup via `agent.services.di.get_X_repository()`)
- Wie man einen neuen Service hinzufügt
- Wie Tests monkeypatchen
- Backward-Compat (alte Singletons bleiben)

**Commit:** `docs(di): document DI-adapter-layer pattern for service repository lookups`

### Task 5.2: Tracker schließen + todos committen

**Objective:** `todos/todo.session-test-suite-stabilisation-2026-06-19.json` aktualisieren mit grünen Tasks + neuem Eintrag für systemischen Refactor.

**Files:** Modify `todos/todo.session-test-suite-stabilisation-2026-06-19.json`

**Schritte:** siehe STAB-OPEN-1/2 → done, neue Task-IDs REFAC-001..010 mit Commit-Refs. STAB-004 auf done oder rollback je nach Netto-Status.

**Commit:** `chore(todos): close STAB-OPEN-1/2 via di-layer refactor`

### Task 5.3: Final-Review (requesting-code-review)

**Objective:** Skill `requesting-code-review` laden und finalen Pre-Commit-Review laufen lassen.

**Output:** `REQUEST_CHANGES` → fix, dann `APPROVED` → squash + final commit `refactor(di): systemwide repository-call-time-lookup layer`.

---

## Verifikations-Matrix (am Ende jeder Welle)

| Check | Kommando | Erwartet |
|-------|----------|----------|
| Welle 1 | `pytest tests/test_di_adapter.py -v` | PASS |
| Welle 2 | `pytest tests/test_result_memory*.py tests/test_awf_worker_fixup*.py -v` | PASS (cross-file) |
| Welle 2 | `pytest tests/ -q 2>&1 \| tail -5` | 0 failures, <2min |
| Welle 3 | `pytest tests/test_*heuristic*.py -v` | PASS |
| Welle 4 | `grep -rn 'self\._[a-z_]\_repo\s*=\s*[a-z_]\_repo' agent/services/` | 0 hits |
| Welle 5 | `pytest tests/ -q` 3x | 0 failures 3/3 |
| Welle 5 | `grep -rn 'from agent.repository import.*_repo' agent/services/ \| grep -v '^#'` | nur di.py-Imports oder Property-Lookup |

## Commit-Übersicht (geplant)

```
test: add STAB-OPEN-1 reproducer baseline
feat(di): add DI-adapter-layer with call-time repository lookups
feat(di): complete factory coverage for all 23 service-level repo imports
refactor(result-memory): switch to di-property for repository lookup
test(result-memory): switch monkeypatch target to di-layer symbol
test(stab-open-1): verify flake eliminated by di-layer refactor
refactor(heuristic-selection): switch to di-property for lease repository
refactor(heuristic-tool): switch to di-property for trace repository
refactor(heuristic-runtime): switch to di-property for lease and trace repos
refactor(planning-track-task-integration): switch to di-property
chore(di): final consolidation grep-check for module-global-self-X pattern
docs(di): document DI-adapter-layer pattern
chore(todos): close STAB-OPEN-1/2 via di-layer refactor
refactor(di): systemwide repository-call-time-lookup layer (final squash)
```

14 Commits, 5 Wellen, ~5–7 Stunden Arbeit.

## Risiken + Mitigationen

1. **Property-Lookup Performance**: `get_X_repository()` ist ein einfacher `from agent.repository import X_repo; return X_repo`. Kein Performance-Hit (Import ist gecacht im Modul-NS).

2. **Backward-Compat für externe Konsumenten**: Bestehende `from agent.repository import memory_entry_repo` funktioniert weiterhin. Nur die Service-Interna ändern sich.

3. **Test-Fakes**: `_FakeRepo` Stub-Klassen müssen ein konsistentes Interface haben. Falls mehrere Fake-Patterns entstehen, in Welle 1.3 `tests/_fakes/` Verzeichnis anlegen.

4. **Bestehende Conftest-Fixtures**: Falls in `conftest.py` schon autouse Resets für `result_memory_service` Global existieren, müssen diese in Welle 2 mit dem neuen Property-Pattern koexistieren oder entfernt werden.

## Was NICHT in diesem Plan ist

- `agent/routes/*` und `agent/scheduler.py` Repo-Imports (außerhalb `agent/services/`): separates Pattern, separater Plan.
- Hub-/Worker-Architektur: unverändert.
- `agent.repository` Modul selbst: nur `di.py` neu, keine Änderung am `agent.repository` Modul.
- DB-Models, Schema-Migrationen: keine.
