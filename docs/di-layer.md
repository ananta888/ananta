# DI-Seam (`agent.services.di`)

## Zweck

Eliminiert Cross-File-Test-Order-Flakes, die entstehen, wenn Service-Klassen
Modul-Level-Repository-Singletons per `from agent.repository import X_repo`
in `__init__` einfrieren (Skill `ananta-pipeline-testing` Pitfall 11b).

CLN-030 hat die früheren 59 Factory-Funktionen entfernt — genau eine hatte
einen Produktionskonsumenten. Übrig bleibt der **kanonische DI-Seam**:

- `get_repository_registry()` — liefert das Repository-Registry-Modul
  (`agent.repository`), das die Singletons besitzt. Das ist der
  Kanonische-Injektionspunkt.
- `get_memory_entry_repository()` — die einzige Factory mit lebendem
  Konsumenten (`ResultMemoryService`); liest `memory_entry_repo` zur
  **Aufrufzeit** über die Registry.

## Architektur

```python
# agent/services/di.py (vollständig verkürzt)
def get_repository_registry() -> Any:
    import agent.repository as registry
    return registry


def get_memory_entry_repository() -> Any:
    return getattr(get_repository_registry(), "memory_entry_repo")
```

SOLID: DIP — Konsumenten lesen zur Aufrufzeit über die Registry statt eine
Import-Referenz einzufrieren; ISP — genau ein schmaler Accessor pro real
genutztem Repository statt eines Catch-all-Containers.

## Wie ein Service den Seam nutzt

```python
class ResultMemoryService:
    @property
    def _memory_entry_repo(self):
        from agent.services.di import get_memory_entry_repository
        return get_memory_entry_repository()
```

## Wie Tests substituieren

Fein (ein Repository): das Singleton auf der Registry patchen —

```python
monkeypatch.setattr("agent.repository.memory_entry_repo", fake_repo)
```

Grob (ganzer Seam): den Accessor patchen —

```python
monkeypatch.setattr("agent.services.di.get_memory_entry_repository", lambda: fake)
```

Beide Pfade sind in `tests/test_di_adapter.py` verankert; ein Guard-Test
hält die Modul-Oberfläche schmal, damit die Factory-Wucherung nicht
zurückkehrt.
