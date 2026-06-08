# Task Engine — Deterministic / Hybrid / LLM-Required Policy Router

> Spezifikation und Architektur des Task-Engine-Routing-Layers (te-001 bis te-016).
> Visualisierung: [docs/architecture/task-engine-flow.mmd](architecture/task-engine-flow.mmd)

---

## Ist-Zustand (vor te-001)

Alle Aufgaben — egal ob „list alle Dateien im Hauptverzeichnis" oder „schreibe einen kompletten Feature-Branch" — laufen durch denselben Pfad:

```
User / AI-Snake
    ↓
ProposeOrchestrator  →  LLM (immer)
    ↓
ExecutionService     →  Shell / Tool
    ↓
Artifact / Audit
```

**Konsequenz:** Einfache Lese-Operationen verbrauchen LLM-Tokens, erhöhen Latenz und schaffen unnötige Angriffsfläche für Prompt-Injection.

Vorhandene Services vor te-001:
- `TaskHandlerRegistry` — leeres Extension-Seam
- `DeterministicHandlerStrategy` — Wrapper ohne konkrete Handler
- `PreflightGate` — strukturelle Envelope-Validierung, aber kein Intent-Routing
- `ApprovalPolicyService`, `MutationGateService` — Policy-Prüfung nach dem LLM-Aufruf

---

## Ziel-Zustand (te-001 bis te-016)

```
User / AI-Snake (Control Layer)
    ↓
TaskIntentRouter          ← klassifiziert Intent aus tool_calls / command / task_kind
    ↓
TaskClassResolver         ← kombiniert Kind-Overrides, Capability-Forced-LLM, Intent
    ↓
TaskEnginePolicyGate      ← Konfiguration (enabled / bypass / strict_unknown_tool)
    ↓
         ┌──────────────────────────────────────────────────┐
         │              task_class                          │
         ├─────────────────┬──────────────┬─────────────────┤
         │  deterministic  │    hybrid    │  llm_required   │
         │  (kein LLM)     │  (LLM opt.) │  (LLM immer)    │
         ↓                 ↓             ↓
  DeterministicHandler  RunTestsHandler  ProposeOrchestrator
  (list_files, etc.)    + opt. LLM       → LLM → Execution
         ↓                 ↓             ↓
         └──────────────────────────────────────────────────┘
                           ↓
                  ExecutionService / Artifact / Audit
                  (Pipeline-Trace: task_engine_active = true)
```

**AI-Snake als Control Layer:** Die AI-Snake ist kein unkontrollierter Executor. Sie leitet Tasks an den `TaskIntentRouter` weiter und zeigt den aktuellen `task_class`-Status im Overlay. Der `TaskEngineStatusService` wird pro Task aktualisiert und ist über `/api/task-engine/status` lesbar.

---

## Schlüsselkomponenten

| Komponente | Datei | Zweck |
|---|---|---|
| `TaskIntentRouter` | `agent/services/task_intent_router.py` | Intent aus tool_calls / command / kind |
| `TaskClassResolver` | `agent/services/task_class_resolver.py` | Endgültige Klassen-Entscheidung |
| `TaskEnginePolicyGate` | `agent/services/task_engine_policy_gate.py` | Konfigurierte Policy-Durchsetzung |
| `ToolScopeContract` | `agent/services/tool_scope_contract.py` | Allowed/forbidden tools normalisiert |
| `TaskRoutingContract` | `agent/models.py` | Felder: task_class, intent, llm_required, deterministic_handler_id |
| Readonly-Handler | `agent/services/readonly_handlers.py` | list_files, read_file, grep_search, git_status, git_diff, json_validate, schema_validate |
| `RunTestsHandler` | `agent/services/run_tests_handler.py` | Hybrid: Profile-gated pytest / jest / cargo / go |
| `TaskEngineTraceHelper` | `agent/services/task_engine_trace.py` | Pipeline-Trace-Einträge |
| `TaskEngineStatusService` | `agent/services/task_engine_status_service.py` | Singleton-Status (TUI / Angular polling) |
| API Blueprint | `agent/routes/task_engine.py` | `GET /api/task-engine/status`, `POST /api/task-engine/classify` |

---

## Konfiguration (te-004)

Umgebungsvariablen mit Defaults:

```bash
TASK_ENGINE_ENABLED=true                        # Master-Schalter
TASK_ENGINE_DETERMINISTIC_BYPASS_ENABLED=true   # LLM-Bypass für deterministic/hybrid
TASK_ENGINE_STRICT_UNKNOWN_TOOL_POLICY=false    # Unbekannte Tools blockieren
```

---

## Intent-Vocabulary

| Intent | task_class | LLM-Bypass | Handler |
|---|---|---|---|
| `list_files` | deterministic | ✓ | `ListFilesHandler` |
| `read_file` | deterministic | ✓ | `ReadFileHandler` |
| `grep_search` | deterministic | ✓ | `GrepSearchHandler` |
| `git_status` | deterministic | ✓ | `GitStatusHandler` |
| `git_diff` | deterministic | ✓ | `GitDiffHandler` |
| `json_validate` | deterministic | ✓ | `JsonValidateHandler` |
| `schema_validate` | deterministic | ✓ | `SchemaValidateHandler` |
| `run_tests` | hybrid | ✓ | `RunTestsHandler` |
| `llm_generate` | llm_required | ✗ | ProposeOrchestrator |
| `code_review` | llm_required | ✗ | ProposeOrchestrator |
| `llm_unknown` | llm_required | ✗ | ProposeOrchestrator |

---

## Konkrete Beispiele

### „Gib mir alle Dateien im Hauptverzeichnis"

```
tool_calls: [{ name: "list_files", arguments: { path: "." } }]
→ TaskIntentRouter:   intent=list_files, source=tool_name
→ TaskClassResolver:  task_class=deterministic, llm_required=false
→ PolicyGate:         bypass_llm=true, handler_id=list_files
→ ListFilesHandler.execute(task)
→ output: ["README.md", "pyproject.toml", ...]
→ Pipeline: task_engine_active=true, bypassed_llm=true
```
**Kein LLM-Call. Kein Token-Verbrauch.**

---

### „git status"

```
command: "git status --short"
→ TaskIntentRouter:   intent=git_status, source=command_pattern
→ TaskClassResolver:  task_class=deterministic
→ PolicyGate:         bypass_llm=true, handler_id=git_status
→ GitStatusHandler.execute()
→ output: "M agent/models.py\n?? tests/test_task_engine.py"
```

---

### „Validiere JSON-Datei config.json"

```
task_kind: "json_validate"
task: { path: "config.json" }
→ TaskClassResolver:  kind_override → deterministic
→ PolicyGate:         bypass_llm=true
→ JsonValidateHandler.execute()
→ { valid: true, exit_code: 0 }
```

---

### „Schreibe Datei auth.py"

```
required_capabilities: ["write_file"]
→ TaskClassResolver:  capability_forces_llm:write_file
→ task_class=llm_required, llm_required=true
→ PolicyGate:         bypass_llm=false
→ ProposeOrchestrator → LLM → ExecutionService
```
**`write_file`-Capability zwingt den Task in den LLM-Pfad, unabhängig von task_kind.**

---

### „run pytest" (Hybrid)

```
command: "pytest tests/"
→ TaskIntentRouter:   intent=run_tests, task_class=hybrid, source=command_pattern
→ PolicyGate:         bypass_llm=true, handler_id=run_tests
→ RunTestsHandler: _command_allowed("pytest tests/", profile=default) → True
→ subprocess.run(["pytest", "tests/"])
→ exit_code=0 oder exit_code=1
→ (optional) LLM fasst Fehlermeldungen zusammen wenn exit_code ≠ 0
```

---

## Pipeline-Trace

Jeder Task, der durch die Task Engine läuft, hinterlässt Einträge in `TaskScopedStepProposeResponse.pipeline`:

```json
{
  "task_engine_active": true,
  "stages": [
    { "stage": "task_intent_router",            "intent": "list_files", "task_class": "deterministic", "source": "tool_name" },
    { "stage": "task_class_resolver",            "task_class": "deterministic", "reason": "kind_override:list_files" },
    { "stage": "deterministic_handler_dispatch", "handler_id": "list_files", "bypassed_llm": true }
  ]
}
```

---

## Strict Unknown Tool Policy (te-010)

Wenn `TASK_ENGINE_STRICT_UNKNOWN_TOOL_POLICY=true` gesetzt ist, werden Tasks mit unbekannten Tool-Namen **blockiert**, bevor sie zum LLM gelangen:

```python
gate = TaskEnginePolicyGate(strict_unknown_tool_policy=True)
d = gate.evaluate({"tool_calls": [{"name": "exotic_custom_tool"}]})
# d.blocked == True, d.allow == False
# reason: "strict_unknown_tool_policy:blocked:exotic_custom_tool"
```

Bekannte Tools sind in `KNOWN_TOOLS` in `task_engine_policy_gate.py` gelistet. Neue Tools werden dort eingetragen oder per `ToolScopeContract.allowed_tools` für einen spezifischen Task erlaubt.

---

## Backwards-Kompatibilität (te-015)

`TASK_ENGINE_ENABLED=false` deaktiviert das gesamte Routing — alle Tasks gehen unverändert an den ProposeOrchestrator (LLM). Bestehende Propose/Execute-Flows ohne Anpassung weiterhin unterstützt.

---

## TUI-Befehle

```
:te status           — zeigt den letzten Task-Engine-Status
:te classify <kind>  — klassifiziert einen task_kind ohne Ausführung
:help te             — Kurzübersicht
```

---

## Diagramme

Vollständige Mermaid-Diagramme: [docs/architecture/task-engine-flow.mmd](architecture/task-engine-flow.mmd)
