# Threading Audit – Autopilot Tick Engine

Bezug: `todo.threading.json`, Tasks thr-001 bis thr-016  
Stand: **alle 16 Tasks abgeschlossen**

---

## Scope

Die Funktion `execute_autopilot_tick()` in `autopilot_tick_engine.py` verarbeitet Tasks
parallel via `ThreadPoolExecutor`. Dieses Dokument dokumentiert alle abgesicherten
Shared-State-Zugriffe, das finale Lock-Schema und die Thread-Safety-Garantien.

---

## Finales Lock-Schema

| Lock | Feld | Typ | Schreibzugriffe | Thread-safe? |
|------|------|-----|-----------------|--------------|
| `_counters_lock` | `loop.dispatched_count` | `int` | `_increment_dispatched()` | **JA** – unter Lock |
| `_counters_lock` | `loop.completed_count` | `int` | `_increment_completed()` | **JA** – unter Lock |
| `_counters_lock` | `loop.failed_count` | `int` | `_increment_failed()` | **JA** – unter Lock |
| `_counters_lock` | `loop.last_error` | `str\|None` | `_set_last_error()` | **JA** – unter Lock |
| `_routing_lock` | `loop._worker_cursor` | `int` | `_assign_worker()` | **JA** – atomar lesen+schreiben |
| `_routing_lock` | `loop._worker_circuit_open_until` | `dict` | `_record_worker_failure/success()` | **JA** – unter Lock |
| `_routing_lock` | `loop._worker_failure_streak` | `dict` | `_record_worker_failure/success()` | **JA** – unter Lock |
| `_lock` (bestehend) | `loop.running` | `bool` | `start()`, `stop()` | **JA** – unter `_lock` |
| `_active_goal_ticks` (thr-016) | Per-Goal Tick-Serialisierung | `set[str]` | `tick_once()` | **JA** – selbes Goal kein concurrent Tick; verschiedene Goals parallel |
| `_counters_lock` | `loop.tick_count` | `int` | `_increment_tick_count()` | **JA** – unter Lock (thr-016) |
| *(kein Lock)* | `loop.last_tick_at` | `float` | nur am Tick-Ende | **JA** – kein Race |

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

`_record_worker_failure()` setzt `last_error` (unter `_counters_lock`) erst NACH
Freigabe von `_routing_lock` — diese Reihenfolge wird im Code explizit eingehalten.

`_lock` (start/stop) ist davon getrennt und wird nicht für Counter/Routing verwendet.
`_tick_lock` wurde durch `_active_goal_ticks` (thr-016) ersetzt — verschiedene
Goals ticken parallel; selbes Goal blockiert sich selbst.

---

## SQLAlchemy Session – Thread-Safety (thr-004)

**Ergebnis: thread-safe. Kein zusätzlicher Lock nötig.**

`database.py` verwendet `Session(engine)` als Context-Manager:
```python
with Session(engine) as session:
    ...
```
Jeder Aufruf von `update_local_task_status()` und `append_trace_event()` öffnet eine
**eigene Session** und schließt sie am Ende. Der `engine` (SQLAlchemy QueuePool) ist
selbst thread-safe.

Da Tasks im Batch unterschiedliche IDs haben (jeder Task geht an höchstens einen Thread),
gibt es kein concurrent Read-Modify-Write auf dieselbe Task-ID.

---

## Flask App-Context pro Thread (thr-008)

`_dispatch_one_task()` öffnet beim Einstieg einen eigenen App-Context:

```python
_ctx = app.app_context() if app is not None else contextlib.nullcontext()
with _ctx:
    return _dispatch_one_task_inner(...)
```

`app` wird als Parameter übergeben, nicht aus `loop._app` gelesen, um
Thread-Safety zu gewährleisten. Im Test-Modus läuft die Funktion ohne App-Context durch.

---

## Worker Pre-Assignment (thr-010)

Worker-Zuteilung für alle Tasks im Batch passiert **sequenziell vor dem ThreadPoolExecutor**
unter `_routing_lock`:

```python
task_assignments: list[tuple[task, target_worker, was_assigned]] = []
for task in candidates[:effective_concurrency]:
    target_worker, was_assigned = loop._assign_worker(task, workers)
    ...
    task_assignments.append((task, target_worker, was_assigned))

with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
    futures = {executor.submit(_dispatch_one_task, task=task, target_worker=target_worker, ...): task.id
               for task, target_worker, was_assigned in task_assignments}
```

→ Kein Thread bekommt denselben Worker-Slot, Round-Robin ist korrekt.

---

## propose_timeout + per_task_hard_timeout (thr-011)

```python
per_task_hard_timeout = (
    int(policy.get("propose_timeout", 120))   # LLM-Call-Budget
    + int(policy.get("execute_timeout", 60))  # Shell-Execute-Budget
    + 30                                       # Puffer
)
```

Standard-Werte aus `resolve_security_policy()`:

| Level | propose_timeout | execute_timeout | Hard-Timeout |
|-------|----------------|----------------|--------------|
| safe | 120s | 45s | 195s |
| balanced | 120s | 60s | 210s |
| aggressive | 180s | 120s | 330s |

Beide Werte sind über `autopilot_security_policies.<level>.propose_timeout` /
`execute_timeout` in `agent_config` konfigurierbar.

---

## Tick-Result-Aggregation (thr-012)

`execute_autopilot_tick()` gibt zurück:

```python
{
    "dispatched": int,       # Anzahl erfolgreich dispatchter Tasks
    "completed": int,        # Davon mit completed=True
    "failed": int,           # Fehlgeschlagene (dispatched-failed + pre-dispatch-failed)
    "task_ids": list[str],   # IDs der dispatchten Tasks
    "reason": "ok",
    "debug": {...},
}
```

`_run_loop` wertet `dispatched > 0` aus um den Inter-Tick-Sleep zu überspringen.

---

## effective_max_concurrency im Status (thr-013)

`GET /tasks/autopilot/status` enthält:

```json
{
  "max_concurrency": 4,
  "effective_max_concurrency": 2,   // nach Security-Policy-Cap (balanced → cap=2)
  "effective_security_policy": { ... }
}
```

`max_concurrency` kann während des Betriebs per `POST /tasks/autopilot/start` mit
`{"max_concurrency": N}` geändert werden. Der laufende Tick wird nicht unterbrochen;
der neue Wert gilt ab dem nächsten Tick.

---

## Parallelitäts-Sequenzdiagramm (Finalzustand)

```
execute_autopilot_tick()
│
├── [sequenziell] Ollama-Probe (gecacht 30s)
├── [sequenziell] Stale-proposing-Reset (>90s ohne Output → force todo)
├── [sequenziell] Worker-Zuteilung für alle Tasks unter _routing_lock
│     task_A → worker_alpha
│     task_B → worker_beta
│
├── ThreadPoolExecutor(max_workers=effective_concurrency)
│     ├── Thread A: _dispatch_one_task(task_A, worker_alpha)
│     │     ├── with app.app_context()          [thr-008]
│     │     ├── log = _task_log(task_A.id)      [thr-009]
│     │     ├── propose → HTTP → worker_alpha → Ollama  [GPU]
│     │     └── execute → HTTP → worker_alpha → Shell
│     │     └── return TaskDispatchResult(dispatched=True, completed=True)
│     │
│     └── Thread B: _dispatch_one_task(task_B, worker_beta)   [parallel!]
│           ├── with app.app_context()
│           ├── log = _task_log(task_B.id)
│           ├── propose → HTTP → worker_beta → Ollama  [GPU]
│           └── execute → HTTP → worker_beta → Shell
│           └── return TaskDispatchResult(dispatched=True, completed=True)
│
├── as_completed(timeout=per_task_hard_timeout)
│     Bei TimeoutError: future.cancel(), task → failed  [thr-007]
│
├── [sequenziell] Aggregation (thr-012)
│     dispatched=2, completed=2, failed=0
│     loop._increment_dispatched() × 2     [_counters_lock, thr-002]
│     loop._increment_completed() × 2      [_counters_lock]
│
└── [sequenziell] _persist_state(), last_tick_at, tick_count
      loop.wake() → nächster Tick sofort   [kein Sleep bei dispatched>0]
```

---

## Bekannte Einschränkungen

| Einschränkung | Erläuterung |
|---------------|-------------|
| `append_trace_event` Lost-Update | Falls zwei Threads dieselbe Task-ID gleichzeitig schreiben, gewinnt der letzte Write (history-Entry verloren). Im aktuellen Design geht jeder Task an genau einen Thread → kein Problem. |
| Circuit-Breaker-Read ohne Lock im Thread | `_dispatch_one_task_inner` liest `_is_worker_circuit_open` nach Worker-Pre-Assignment. Der Wert kann sich zwischen Pre-Assignment und Thread-Ausführung ändern. Risiko niedrig (Pre-Assignment ist kurz vorher). |
| Graceful Stop wartet max. 2s | `stop()` joinet den Loop-Thread mit `timeout=2.0`. Laufende Task-Threads werden durch den `as_completed()` Timeout (bis ~330s) abgebrochen, nicht hart. |
