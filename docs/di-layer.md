# DI-Adapter-Layer (`agent.services.di`)

## Zweck

Eliminiert Cross-File-Test-Order-Flakes, die entstehen, wenn Service-Klassen
Modul-Level-Repository-Singletons per `from agent.repository import X_repo`
in `__init__` einfangen (siehe Plan
`docs/plans/2026-06-20-di-adapter-layer-cross-file-isolation.md` und
Skill `ananta-pipeline-testing` Pitfall 11b).

## Architektur

`agent/services/di.py` ist ein dünner Adapter-Layer mit 59 Factory-Funktionen
(eine pro Repository-Singleton). Jede Factory liest das Repository-Symbol
zur **Aufrufzeit** aus `agent.repository`, nicht zur Modul-Import-Zeit.

```python
# agent/services/di.py (Auszug)
def get_memory_entry_repository() -> Any:
    """Call-time lookup for the ``memory_entry_repo`` singleton."""
    return _resolve("memory_entry_repo")

def _resolve(name: str) -> Any:
    module_value = globals().get(name, _SENTINEL)
    if module_value is not _SENTINEL:
        return module_value  # test monkeypatch
    from agent import repository
    return getattr(repository, name)

def __getattr__(name: str) -> Any:
    """Resolve ``agent.services.di.<name>`` via ``_resolve``."""
    if name in _KNOWN_REPOS:
        return _resolve(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

## Wie ein Service das DI-Layer nutzt

```python
# In agent/services/<service>.py:

class MyService:
    def __init__(self, *, my_repository=None, ...):
        # Capture explicit override (or None) for test-DI.
        # The default case defers to the property which resolves
        # agent.services.di.<repo> at call time.
        self._my_repository_override = my_repository

    @property
    def _my_repository(self) -> Any:
        if self._my_repository_override is not None:
            return self._my_repository_override
        from agent.services.di import get_my_repository
        return get_my_repository()
```

## Wie Tests monkeypatchen

**Richtig** (DI-Layer-konform):
```python
# Ersetze das ganze Repository-Objekt via Factory-Root:
fake_repo = _FakeRepo()
monkeypatch.setattr("agent.services.di.memory_entry_repo", fake_repo)
# Service-Property resolved jetzt das fake_repo.
```

**Falsch** (alter Stil, funktioniert in vielen Fällen noch, aber risikoreich):
```python
# Mutation des .save Attributs auf dem geteilten Singleton:
monkeypatch.setattr("agent.repository.memory_entry_repo.save", lambda e: ...)
# Das friert nichts ein, aber mutiert den geteilten State.
```

## SOLID-Begründung

- **DIP (Dependency Inversion Principle)**: Services hängen von der
  Aufrufzeit-Abstraktion (`di.get_X_repository()`) ab, nicht vom
  Modul-Level-Cache.
- **OCP (Open/Closed)**: Neue Repositories werden durch neue Factory-
  Funktionen hinzugefügt, ohne Service-Konstruktoren anzufassen.
- **SRP (Single Responsibility)**: `di.py` macht genau eine Sache
  (Factory-Funktionen), keine Domänenlogik, kein SQL, kein I/O.
- **LSP (Liskov Substitution)**: Jedes Objekt mit dem richtigen Protokoll
  kann für den Singleton einspringen — die Factory ist substitutierbar.
- **ISP (Interface Segregation)**: Schmale Factory-Interfaces pro
  Repository, kein Catch-All-Container.

## Backwards-Kompatibilität

Die Modul-Level-Singletons in `agent.repository` bleiben unverändert.
Bestehende Aufrufer, die `from agent.repository import X_repo` nutzen,
funktionieren weiterhin. **Neuer Code** sollte die Factory-Funktionen
verwenden.

## Wann das Pattern **nicht** anwenden

Wenn ein Service ein `or ClassName()` Default-Factory-Pattern verwendet
(d.h. eine Klasse importiert und im Konstruktor instanziiert), gibt es
**keinen** Singleton-Cache. Das DI-Layer bringt hier keinen Nutzen.

```python
# Harmlos — Default-Factory, kein Footgun:
class HeuristicTool:
    def __init__(self, trace_repo=None, ...):
        self._trace_repo = trace_repo or DecisionTraceRepository()
```

Audit-Rezept:
```bash
# Findet Singleton-Cache (Footgun):
grep -rn 'self\._[a-z_]*_repo\s*=\s*[a-z_]*_repo\b' agent/services/

# Findet Default-Factory (harmlos):
grep -rn 'self\._[a-z_]*_repo\s*=\s*[a-z_]*_repo or [A-Z]' agent/services/
```

## Geschichte

- Welle 1 (Commit `8225aa6ee`): `di.py` mit 59 Factories + 5 Tests
- Welle 2 (Commit `454c28980`): `ResultMemoryService` auf Property-Pattern
  umgestellt; 76/76 Tests grün; 0 Regressionen.
- Welle 3 + 4: Befund — Heuristic-Services und
  `planning_track_task_integration_service` nutzen Default-Factory,
  kein Refactor erforderlich.
