# Threading Audit – Autopilot Tick Engine

Bezug: `todo.threading.json`, Tasks thr-001 bis thr-016  
Stand: thr-001 abgeschlossen (thr-002/003/004/005 in Arbeit)

---

## Scope

Die Funktion `execute_autopilot_tick()` in `autopilot_tick_engine.py` verarbeitet derzeit Tasks
sequenziell in einer `for`-Schleife. Ziel ist parallele Ausführung per `ThreadPoolExecutor`.
Dieses Dokument kartiert alle Shared-State-Zugriffe, die bei paralleler Ausführung zu
Race Conditions führen würden.

---

## Gemeinsam genutzter Zustand (AutonomousLoopManager-Felder)

| Feld | Typ | Wo geschrieben | Thread-safe? | Absicherung (Ziel) |
|------|-----|----------------|--------------|---------------------|
| `loop.dispatched_count` | `int` | tick_engine.py:909 | **NEIN** – `+=` nicht atomar | `_counters_lock` (thr-002) |
| `loop.completed_count` | `int` | tick_engine.py:912 | **NEIN** | `_counters_lock` (thr-002) |
| `loop.failed_count` | `int` | tick_engine.py:518,738,762,787,830,867,925 | **NEIN** | `_counters_lock` (thr-002) |
| `loop.last_error` | `str\|None` | tick_engine.py:377,450,928 | **NEIN** – Last-Write-Wins ohne Lock | `_counters_lock` (thr-002) |
| `loop.last_tick_at` | `float\|None` | tick_engine.py:422,451,927 | Nur am Tick-Ende, kein Race | Kein Lock nötig (nach Threads fertig) |
| `loop.tick_count` | `int` | tick_engine.py:423,452,929 | Nur am Tick-Ende, kein Race | Kein Lock nötig |
| `loop._worker_cursor` | `int` | tick_engine.py:478 (via resolve_target_worker_for_task) | **NEIN** – Lesen+Schreiben nicht atomar | `_routing_lock` (thr-003) |
| `loop._worker_circuit_open_until` | `dict[str,float]` | autopilot.py:295 (_record_worker_failure) | **NEIN** | `_routing_lock` (thr-003) |
| `loop._worker_failure_streak` | `dict[str,int]` | autopilot.py:291,292 | **NEIN** | `_routing_lock` (thr-003) |
| `loop.running` | `bool` | autopilot.py:178,191 | Nur in start()/stop() unter `_lock` | Bestehender `_lock` reicht |
| `loop._persist_state()` | Methode | tick_engine.py:424,453,930 | Intern unter `_lock` | Nur am Tick-Ende aufrufen (nach Threads) |

---

## Lock-Hierarchie (Deadlock-Vermeidung)

**Invariante: `_routing_lock` darf NIEMALS unter `_counters_lock` gehalten werden.**

```
Erlaubt:
  with _routing_lock:
      cursor = ...          # Worker-Zuteilung lesen/schreiben
  with _counters_lock:
      failed_count += 1     # Zähler aktualisieren

VERBOTEN:
  with _counters_lock:
      with _routing_lock:   # DEADLOCK-RISIKO
          ...
```

Die bestehenden Locks (`_lock` für start/stop, `_tick_lock` für Tick-Serialisierung)
sind davon getrennt und werden nicht für Counter/Routing verwendet.

---

## SQLAlchemy Session – Thread-Safety (thr-004)

**Ergebnis: thread-safe. Kein zusätzlicher Lock nötig.**

`database.py` verwendet `Session(engine)` als Context-Manager:
```python
with Session(engine) as session:
    ...
```
Jeder Aufruf von `update_local_task_status()` und `append_trace_event()` öffnet eine
**eigene Session** und schließt sie am Ende. Der `engine` (SQLAlchemy Connection Pool)
ist selbst thread-safe (PostgreSQL: `NullPool`/`QueuePool`, beide designed für
Multi-Threading).

Einziges Race-Condition-Risiko: Read-Modify-Write auf denselben Task von zwei Threads.
Da Tasks im Batch unterschiedliche IDs haben (jeder Task geht an höchstens einen Thread),
tritt dieser Fall nicht auf. Für `append_trace_event` liest Thread A `task.history`,
Thread B modifiziert `task.status` → verschiedene Felder, kein Lost-Update bei PostgreSQL
(row-level locking).

**Kommentar wird in `update_local_task_status()` ergänzt (thr-004).**

---

## append_trace_event – Thread-Safety

`_append_trace_event()` → `autopilot_support_service.append_trace_event()`:
- Öffnet eigene DB-Session
- Liest Task, hängt an `task.history` an, schreibt zurück
- Bei gleichem Task-ID aus zwei Threads: letzter Write gewinnt (history-Eintrag verloren)

**Mitigierung**: Im Batch bekommt jeder Task genau einen Thread. Kein konkurrierender
Zugriff auf dieselbe Task-ID. Akzeptiert als safe for current use case.

---

## resolve_target_worker_for_task – kritischer Race-Condition-Kandidat

```python
# autopilot_tick_engine.py:478 – NICHT thread-safe:
target_worker, loop._worker_cursor, was_assigned = resolve_target_worker_for_task(
    task=task,
    workers=workers,
    worker_cursor=loop._worker_cursor,  # lesen
)
# loop._worker_cursor = neuer Wert          # schreiben
```

Wenn zwei Threads gleichzeitig ausgeführt werden, lesen beide denselben `_worker_cursor`
und bekommen denselben Worker zugeteilt.

**Fix (thr-003)**: Worker-Zuteilung für alle Tasks im Batch passiert sequenziell VOR dem
ThreadPoolExecutor unter `_routing_lock`. Jeder Task bekommt seinen `target_worker` als
Parameter (thr-010 formalisiert das).

---

## Parallelitäts-Sequenzdiagramm (Zielzustand nach thr-006)

```
execute_autopilot_tick()
│
├── [sequenziell] Ollama-Probe (gecacht 30s)
├── [sequenziell] Worker-Zuteilung für alle Tasks unter _routing_lock
│     task_A → worker_alpha
│     task_B → worker_beta
│
├── ThreadPoolExecutor(max_workers=2)
│     ├── Thread A: _dispatch_one_task(task_A, worker_alpha)
│     │     ├── propose → HTTP → worker_alpha → Ollama  [GPU]
│     │     └── execute → HTTP → worker_alpha → Shell
│     │
│     └── Thread B: _dispatch_one_task(task_B, worker_beta)   [parallel!]
│           ├── propose → HTTP → worker_beta → Ollama  [GPU]
│           └── execute → HTTP → worker_beta → Shell
│
├── [sequenziell] Ergebnisse aggregieren (under _counters_lock)
│     dispatched_count += 2
│     completed_count += n
│     failed_count += m
│
└── [sequenziell] _persist_state(), last_tick_at, tick_count
```

---

## Offene Risiken (nach thr-006, vor thr-010)

| Risiko | Schwere | Addressiert durch |
|--------|---------|-------------------|
| Worker-Cursor Race (beide Threads selber Worker) | Mittel | thr-010 |
| Flask App-Context fehlt in neuem Thread | Hoch | thr-008 |
| SQLAlchemy DetachedInstanceError wenn Task-Objekt thread-übergreifend genutzt | Mittel | thr-008 (App-Context pro Thread) |
| Circuit-Breaker-Read ohne Lock im Thread | Niedrig | thr-003 (read unter _routing_lock) |
