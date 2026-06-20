# Plan: `agent.common.sgpt_*` von `agent.services` entkoppeln

**Track:** `SGDEC`
**Status:** Architektur-Entscheidung getroffen — `decision: "Option B (Sinking)"`, `user_acknowledged: true`
**Erstellt:** 2026-06-20
**SOLID-Bezug:** SRP (mehrere SRP-Verletzungen), DIP (Common-Layer importiert aus Service-Layer), OCP (Tool-Loops sind nicht erweiterbar ohne Common-Layer-Patch)

## 0. Scope-Korrektur (gegen "alles in einem Atemzug")

**Was NICHT in diesem Track enthalten ist** (und warum):

- **`agent.common.audit`** (1 Service-Import: `hub_event_service.build_hub_event`): Hat 84+ Importer über Routes, Services, Tools, Tests. Das ist **kein DIP-Verstoß, sondern eine bewusste Cross-Cutting-Architektur**: die Audit-Fassade lebt absichtlich in `agent.common`, weil alle Schichten (Routes, Services, Tools, Bootstrap, Background-Services) sie brauchen. Service-Import hier = Service-Locator-Pattern für Audit, nicht DIP-Bug. Verschieben würde bedeuten: 84+ Importer anfassen + neues Service-Locator-Pattern. **Eigener Track** `todo.common-layer-deepen-services.md` (D5).
- **`agent.common.error_handler`** (1 Service-Import: `log_service.get_log_service`): Gebraucht für `register_error_handler(app)`. Service-Import ist semantisch korrekt: ein Error-Handler MUSS den Logger-Service kennen, sonst loggt er nichts.
- **`agent.common.signals`** (1 lazy Service-Import: `scheduler_service`): Signal-Handler-Pattern. Lazy-Import ist genau richtig.

**Dokumentations-Pflicht:** In `agent/common/audit.py`, `error_handler.py`, `signals.py` jeweils ein Modul-Docstring-Eintrag: `# ARCHITEKTUR-ENTSCHEIDUNG: Bewusster Service-Import (Cross-Cutting-Fassade). Verschiebung wäre anti-pattern. Siehe todo.common-layer-deepen-services.md.`

**Was in diesem Track enthalten ist:**

1. **SGDEC-D1 (decided):** Option B (Sinking) — `agent/cli_backends/` als neuer Namespace.
2. **SGDEC-D2 (decided):** 4-Split von `sgpt_workspace_mutation.py` (785 LOC) ist **Teil von Welle 2**, nicht separater Track. Grund: vom User explizit gefordert "nichts aufschieben".
3. **SGDEC-D3 (decided):** **Kein 12-Monats-Shim**, sondern **Shim bis Detektor 0 Importer meldet, dann sofort weg** (Detektor-getrieben). Grund: "nichts nur anfangen".
4. **SGDEC-D4 (decided):** `sgpt_helpers.py` zieht MIT nach `agent/cli_backends/helpers.py` um. Grund: von den anderen sgpt_-Modulen intensiv genutzt.
5. **SGDEC-D5 (decided):** `audit.py`/`error_handler.py`/`signals.py` werden **nicht verschoben**, sondern mit Architektur-Begründungs-Docstring versehen. Eigener separater Track für die grundsätzliche "Service-Locator-Pattern statt direkter Imports"-Diskussion.

---

## 1. Discovery-Verdict (Source-First Sweep)

**Subsystem existiert wie benannt:** `agent.common.sgpt_*` ist die **faktische Backbone-Schicht für die LLM-CLI-Ausführung** (sgpt, opencode, codex, aider, mistral). Es ist keine Legacy-Hülse — es ist der primäre Pfad.

**Verdict:** *Common-Layer importiert Service-Layer, nicht umgekehrt.* Die Service-Module sind die "dickeren" Domain-Objekte; die sgpt_-Module sind **dünne Ausführungs-Wrapper**. Architektonisch ist das ein DIP-Verstoß, aber kein klassisches God-Class-Problem.

### Modul-Map (Phase 1)

| Modul | LOC | Zweck | Service-Imports |
|---|---:|---|---|
| `agent/common/sgpt.py` | 397 | Top-level: `run_sgpt_command`, `run_llm_cli_command`, `_run_ananta_worker_iterative` | indirekt (lazy) |
| `agent/common/sgpt_helpers.py` | 157 | Pure Helpers: `_get_agent_config`, URL-Normalisierung, Provider-URLs | 0 (nur flask + config + lazy `agent.llm_integration`) |
| `agent/common/sgpt_backend_semaphore.py` | 66 | Bounded-Semaphore pro Backend für Parallelitäts-Limits | 0 (nur `sgpt_helpers`) |
| `agent/common/sgpt_backend_routing.py` | 467 | `SUPPORTED_CLI_BACKENDS`, Capability-Matrix, Pre-Flight-Checks | 0 (nur `sgpt_helpers` + lazy `sgpt_opencode`) |
| `agent/common/sgpt_tool_loop.py` | 486 | Ananta-Worker Tool-Loop, Approval-Request, Tool-Registry | **3 (lazy)**: `approval_request_service`, `ananta_tool_policy_service`, `ananta_tool_registry_service`, `tools`, `tools._evidence` |
| `agent/common/sgpt_workspace_mutation.py` | 785 | Workspace-Mutation, Evidence-Tracking, Iteration-Prompt | **5 (1 top + 4 lazy)**: `generated_source_line_policy_service`, `ananta_workspace_mutation_policy`, `ananta_tool_policy_service`, `tools`, `tools._evidence`, `tools.repo_tools`, `worker_workspace_service` |
| `agent/common/sgpt_opencode.py` | 648 | OpenCode/Codex/Aider/Mistral-Runtime-Adapter, Live-Terminal | **3 (lazy)**: `model_invocation_service`, `opencode_runtime_service`, `live_terminal_session_service` |
| `agent/common/sgpt_architecture_scan.py` | 803 | Architektur-Scan-Loop, Iteration-Prompt, Source-File-Batches | **1 (lazy)**: `architecture_analysis_planner_service` |
| `agent/common/audit.py` | 340 | `log_audit` — geschrieben in `hub_event_service` | `agent.services.hub_event_service` |
| `agent/common/error_handler.py` | 88 | Fehlerbehandlung | `agent.services.log_service` |
| **Total** | **4237** | | |

### Wiring-Map (Phase 3)

**Importer von `agent.common.sgpt_*` (consumer-Seite):**

| Importierer | Modul | Was wird importiert |
|---|---|---|
| `agent/routes/sgpt.py` | Route | `from agent.common.sgpt import (...)` — der primäre HTTP-Pfad `/api/.../sgpt` |
| `agent/routes/snakes_full_scan.py` | Route | `from agent.common.sgpt_architecture_scan import _resolve_repo_root` |
| `agent/sgpt/function.py` | Function-Call | `from agent.common.sgpt_helpers import _get_agent_config` |
| `tests/test_ananta_worker_workspace_feedback_iteration.py` | Test | `from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation` |
| `tests/test_patch_first_range_context.py` | Test | `from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation` |
| `tests/test_ananta_worker_tool_loop.py` | Test | `from agent.common.sgpt_tool_loop import (...)` |
| `tests/test_codecompass_range_context_planner.py` | Test | `from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation` |
| `tests/test_sgpt_route.py`, `test_sgpt_route_codecompass.py`, `test_sgpt_capability_matrix.py` | Test | `from agent.routes import sgpt as sgpt_route` |
| `tests/test_sgpt_parallelism.py` | Test | `from agent.common import sgpt` |
| `tests/test_no_sgpt_in_propose_path.py` | Test | `monkeypatch.setattr("agent.common.sgpt.run_sgpt_command", ...)` |

**`agent.common` (nicht-sgpt) Importer:** 18 Routes + `agent.utils` + `agent.config` + `agent.tools_shell` — der `audit`/`errors`/`logging`/`mfa`/`http`/`vault_source`/`utils`-Anteil ist **bereits sauber** (kein Service-Import oder nur 1 Service für `audit.py`/`error_handler.py`).

### Test-Coverage-Map (Phase 4)

Test-Files mit direktem sgpt-Bezug: 4 dedizierte + ~10 indirekte (über Konfig-Settings `sgpt_routing`, `sgpt_execution_backend`). Pattern: `tests/test_sgpt_*.py`, `tests/test_no_sgpt_in_propose_path.py`, `tests/test_ananta_worker_tool_loop.py`, `tests/test_ananta_worker_workspace_feedback_iteration.py`, `tests/test_patch_first_range_context.py`, `tests/test_codecompass_range_context_planner.py`.

`test_no_sgpt_in_propose_path.py:127` — **kritischer Monkeypatch-Vertrag**:
```python
monkeypatch.setattr("agent.common.sgpt.run_sgpt_command", fake_sgpt, raising=False)
```
Dieser Pfad **muss erhalten bleiben** oder sauber migriert werden.

---

## 2. Das eigentliche Problem

Die `sgpt_*`-Module sind keine "shared utilities" — sie sind **die Ausführungsschicht eines kompletten Subsystems** (LLM-CLI-Backends). Sie haben:

1. **DIP-Verstoß:** `agent.common` (Definitionsschicht: "shared, dünne Utilities") importiert aus `agent.services` (Domain-Schicht: "dicke Geschäftslogik mit DB"). Das ist die falsche Richtung.
2. **SRP-Verstoß pro Modul:** `sgpt_workspace_mutation.py` (785 LOC) macht *gleichzeitig*:
   - Mutation-Output-Parsing (`parse_mutation_output`)
   - Iteration-Prompt-Building (`_build_iteration_prompt`)
   - Evidence-Signatur-Berechnung (`_evidence_signature`, `_changes_signature`)
   - Mode-Instructions (`_build_mode_instructions`)
   - Worker-Loop (`run_ananta_worker_workspace_mutation`)
3. **Test-Bypass-Risiko:** Monkeypatches auf `agent.common.sgpt_*.X` werden vom Modul selbst umgangen, sobald die Helfer-Modul-ebene Funktionen direkt aufrufen (siehe `ananta-subsystem-discovery` Pitfall "monkeypatch target migration"). Die Lazy-Imports in `sgpt_tool_loop` / `sgpt_workspace_mutation` / `sgpt_opencode` / `sgpt_architecture_scan` machen es Tests schwer, die richtige Stelle zu patchen.
4. **Falsche Schicht:** Die Module gehören semantisch nach `agent/llm_runtime/` oder `agent/cli_backends/`, nicht in `agent.common`.

### Was *nicht* das Problem ist

- `sgpt_helpers.py` (157 LOC, 0 Service-Imports, nur flask + config) — **bereits sauber**, kann bleiben wo es ist.
- `sgpt_backend_semaphore.py` (66 LOC, 0 Service-Imports) — **bereits sauber**, klein genug, kann bleiben.
- `sgpt_backend_routing.py` (467 LOC, 0 Service-Imports) — bis auf die `sgpt_opencode`-Rück-Referenz in zwei Funktionen **bereits sauber**.
- `audit.py` (1 Service-Import für `hub_event_service`) — bewusste Designentscheidung, nicht zu ändern.
- `error_handler.py` (1 Service-Import für `log_service`) — gleiche Klasse.

---

## 3. Zwei Architektur-Optionen

### Option A: **Lifting** — Service-Module nach `agent.common` ziehen
Pro: Beendet den DIP-Verstoß in eine Richtung, kleinster Diff
Contra: `agent.common` wird zur Domain-Schicht, bricht das bestehende Schicht-Modell. Macht `agent.services` zu einem Skelett, das nur noch DB-Mapping hält.
**Verdikt:** Falsche Richtung. `agent.common` soll dünn bleiben.

### Option B: **Sinking** — `sgpt_*` (und ihre Service-Importe) als eigenes Subsystem-Modul konsolidieren
Pro: Korrekte Schicht. Eigener Namespace `agent/cli_backends/` oder `agent/llm_runtime/`. Lokale DI-Klasse macht Service-Handles injizierbar.
Contra: 1. Paket-Shadowing-Risiko (`agent/<name>.py` vs `agent/<name>/`, siehe `lcg-package-shadowing-pitfall`).
Contra: 2. Migration der 18+ Importer + 4 dedizierten Tests + Monkeypatch-Pfade.
Contra: 3. Test-Suite muss re-verifiziert werden.

### Option C: **Neutralisieren** — `sgpt_*` bleibt wo es ist, aber Service-Importe werden durch ein lokales `SGLayer`-Kontextobjekt ersetzt
Pro: Kleinster Diff. Keine Import-Migration. Monkeypatches bleiben valide.
Contra: Macht `agent.common` nicht "dünner", nur abstrakter. Versteckt die DIP-Verletzung statt sie zu lösen.

**Empfehlung:** **Option B (Sinking)** — `agent/cli_backends/` als neuer Namespace, mit lokaler DI-Klasse für die Service-Handles. Die 4 sauberen Module (`sgpt_helpers`, `sgpt_backend_semaphore`, `sgpt_backend_routing`, `sgpt_architecture_scan` ohne Service) bleiben, weil sie keine Service-Importe haben.

**Konkretes Layout unter `agent/cli_backends/`:**

```
agent/cli_backends/
├── __init__.py            # re-export public symbols for back-compat
├── context.py             # CliBackendContext — DI-Box mit get_*_service handles (no-op defaults)
├── helpers.py             # = sgpt_helpers.py (Pure)
├── routing.py             # = sgpt_backend_routing.py (Pure, ohne Service-Import)
├── semaphore.py           # = sgpt_backend_semaphore.py (Pure)
├── tool_loop.py           # = sgpt_tool_loop.py (mit context.get_*_service)
├── workspace_mutation.py  # = sgpt_workspace_mutation.py (mit context.get_*_service)
├── opencode.py            # = sgpt_opencode.py (mit context.get_*_service)
├── architecture_scan.py   # = sgpt_architecture_scan.py (mit context.get_*_service)
└── shim/
    ├── __init__.py
    ├── sgpt.py            # re-exports from cli_backends + DeprecationWarning
    ├── sgpt_helpers.py
    ├── sgpt_backend_routing.py
    ├── sgpt_backend_semaphore.py
    ├── sgpt_tool_loop.py
    ├── sgpt_workspace_mutation.py
    ├── sgpt_opencode.py
    └── sgpt_architecture_scan.py
```

**Migrations-Pfad:**
1. Phase 0: Vertrag — `CliBackendContext` mit `get_*_service`-Proxies (lazy, per Default delegiert an `agent.services.get_*_service`).
2. Phase 1: Source-Module in `agent/cli_backends/` anlegen, `agent/common/sgpt_*.py` zu Re-Export-Shims machen.
3. Phase 2: Service-Importer in den sgpt_-Source-Modulen durch `context.X` ersetzen.
4. Phase 3: Tests auf neuen Namespace migrieren, alte `agent.common.sgpt.run_sgpt_command`-Pfade via Shim erhalten.
5. Phase 4: Shim-Entfernungs-Detektor (`scripts/check_cli_backend_shim_imports.py`).

---

## 4. SOLID-Diagnose pro Modul

| Modul | SRP | OCP | DIP | LSP | ISP | Diagnose |
|---|---|---|---|---|---|---|
| `sgpt_helpers.py` | ✓ | ✓ | ✓ | ✓ | ✓ | **sauber** |
| `sgpt_backend_semaphore.py` | ✓ | ✓ | ✓ | ✓ | ✓ | **sauber** |
| `sgpt_backend_routing.py` | ⚠ | ✓ | ✓ | ✓ | ✓ | Eine SRP-Verletzung: mischt Capability-Matrix + Pre-Flight-Runner + Runtime-Status-Resolver. Refactor in 2 Sub-Module optional. |
| `sgpt_tool_loop.py` | ⚠ | ⚠ | ✗ | ✓ | ⚠ | Mischt Tool-Loop + Approval-Request-Triggering + Tool-Registry-Lookup. DIP: importiert 3 Services. ISP: exportiert 9 top-level Funktionen, davon sind 2 nur intern. |
| `sgpt_workspace_mutation.py` | ✗ | ✗ | ✗ | ✓ | ⚠ | 785 LOC, mind. 4 SRP-Cluster. DIP: 5 Service-Importe. |
| `sgpt_opencode.py` | ⚠ | ✗ | ✗ | ✓ | ⚠ | Mischt 4 CLI-Backends (opencode/codex/aider/mistral). DIP: 3 Service-Importe. Jedes Backend verdient eine eigene Sub-Klasse oder eigenes Modul. |
| `sgpt_architecture_scan.py` | ⚠ | ✓ | ✗ | ✓ | ✓ | Mischt Repo-Root-Resolution + Source-File-Batching + Iteration-Prompt + Full-Scan-Loop. DIP: 1 Service-Import. |

**Largest SRP-Cluster (`sgpt_workspace_mutation.py`):**
- Cluster A (L66-200): Mode-Instructions, Evidence-Signatur
- Cluster B (L200-400): Mutation-Output-Parsing, Iteration-Prompt
- Cluster C (L400-600): Worker-Loop-Orchestrierung
- Cluster D (L600-785): Tool-Execution-Hooks, Evidence-Append
→ Bester Kandidat für 4-Module-Split gemäß `ananta-subsystem-discovery` 5-Module-Split-Template (kleiner: 4 statt 5).

---

## 5. Wellen-Plan (3-Wellen, atomic pro Welle)

**Entscheidungen bereits getroffen:**
- **D1:** Option B (Sinking)
- **D2:** 4-Split von `sgpt_workspace_mutation.py` ist TEIL von Welle 2, nicht separater Track
- **D3:** Detektor-getriebener Shim (sofort weg bei 0 Importern), kein 12-Monats-Fenster
- **D4:** `sgpt_helpers.py` zieht mit um
- **D5:** `audit.py` / `error_handler.py` / `signals.py` bleiben in `agent.common` (separate Architektur-Diskussion im eigenen Track)

### Welle 1: Vertrag & Skelett (parallelisierbar)
- **SGDEC-T01:** `agent/cli_backends/__init__.py` + `context.py` + Shim-Skelett (`agent/common/sgpt_*.py` → Re-Export). RED: `tests/test_cli_backend_namespace_skeleton.py` (Import-Test). GREEN: Skelett-Dateien anlegen, Re-Exports funktionieren, alle bestehenden Tests grün.
- **SGDEC-T02:** `agent/common/sgpt_*.py` werden zu Re-Export-Shims mit `DeprecationWarning` beim ersten Import. RED: `tests/test_cli_backend_shim_deprecation.py` (Warning wird emittiert). GREEN: `warnings.warn(..., DeprecationWarning, stacklevel=2)` in jeden Top-Level-Import. **Wichtig:** Tests dürfen NICHT brechen — die Warning-Suppression in bestehenden Tests erfolgt über `pytest.ini` Filter (`filterwarnings = ignore::DeprecationWarning:agent.common.sgpt`).

**Verifikations-Gate:** `pytest tests/test_sgpt_*.py tests/test_no_sgpt_in_propose_path.py` — alle grün, keine Verhaltens-Änderung.

### Welle 2: Atomic-Migration + 4-Split (eine PR, sequenziell)
- **SGDEC-T03 (Source-Migration):** `agent/cli_backends/tool_loop.py` + `opencode.py` + `architecture_scan.py` als Source-of-Truth (drei kleinere Module, 486+648+803 LOC, je 1-3 Service-Importe). Service-Importer durch `context.get_*_service` ersetzt. Shim-Module re-exportieren aus dem neuen Namespace. Tests aktualisieren Monkeypatch-Targets: `monkeypatch.setattr("agent.cli_backends.tool_loop.run_ananta_worker_tool_loop", ...)`.
- **SGDEC-T04 (4-Split von `sgpt_workspace_mutation.py`):** Aufteilung in 4 SRP-konforme Sub-Module:
  - `workspace_mutation/signatures.py` (~150 LOC) — Evidence-Signatur, Mode-Instructions
  - `workspace_mutation/prompts.py` (~200 LOC) — Mutation-Output-Parsing, Iteration-Prompt
  - `workspace_mutation/loop.py` (~250 LOC) — Worker-Loop-Orchestrierung
  - `workspace_mutation/tools.py` (~190 LOC) — Tool-Execution-Hooks, Evidence-Append
  - `agent/cli_backends/workspace_mutation.py` (~30 LOC) — Re-Export-Public-API (ersetzt den 785-LOC-Monolithen)
- **SGDEC-T05 (Shim-Migration):** `agent/common/sgpt_workspace_mutation.py` re-exportiert aus dem neuen Namespace. Shim muss Mutable-Structures (z.B. `user_requests.clear()` in `test_sgpt_route.py:11-15`) gleich exportieren.

**Verifikations-Gate:** Vollständige Test-Suite grün, insbesondere:
- `tests/test_no_sgpt_in_propose_path.py` — Monkeypatch funktioniert weiterhin über den Shim.
- `tests/test_sgpt_parallelism.py` — Bounded-Semaphore-Verhalten unverändert.
- `tests/test_ananta_worker_tool_loop.py` — Approval-Request-Trigger unverändert.
- `tests/test_ananta_worker_workspace_feedback_iteration.py` — Workspace-Mutation-Loop unverändert.
- `tests/test_patch_first_range_context.py` — Range-Context-Parser unverändert.
- `tests/test_codecompass_range_context_planner.py` — CodeCompass-Plan unverändert.

### Welle 3: Detektor & sofortiger Cleanup
- **SGDEC-T06:** `scripts/check_cli_backend_shim_imports.py` — scannt Repo nach `from agent.common.sgpt_` (ohne Shim-Pfad), gibt aus welche Importer noch migriert werden müssen, hat CI-Exit-Code 1 bei Funden. Script-Output ist sowohl Human-readable (Liste) als auch JSON (`--json`) für CI.
- **SGDEC-T07 (alle Importer migrieren):** Sequenzielle Migration aller Importer von `agent.common.sgpt_*` → `agent.cli_backends.*`:
  - `agent/routes/sgpt.py` (Haupt-Import)
  - `agent/routes/snakes_full_scan.py` (`_resolve_repo_root`)
  - `agent/sgpt/function.py` (`_get_agent_config` → `agent.cli_backends.helpers._get_agent_config`)
  - 4 Test-Files: `test_ananta_worker_workspace_feedback_iteration.py`, `test_patch_first_range_context.py`, `test_ananta_worker_tool_loop.py`, `test_codecompass_range_context_planner.py`, `test_sgpt_parallelism.py`, `test_no_sgpt_in_propose_path.py`
  - 3 indirekte Routes: `test_sgpt_route.py`, `test_sgpt_route_codecompass.py`, `test_sgpt_capability_matrix.py`
- **SGDEC-T08 (Shim löschen + Detektor-grün verifizieren):** Wenn `scripts/check_cli_backend_shim_imports.py` 0 Importer meldet: `agent/common/sgpt_*.py` löschen, Shim-Tests löschen, `pytest.ini`-Warning-Filter entfernen, Detektor-Script mit `--ci-gate` aufrufen.

**Verifikations-Gate:** `python scripts/check_cli_backend_shim_imports.py` exit 0, keine `agent.common.sgpt_*` Dateien mehr im Repo, `python -c "import agent.cli_backends"` ohne Warning, vollständige Test-Suite grün.

---

## 6. Risiko-Matrix

| Risiko | Wahrscheinlichkeit | Impact | Mitigation |
|---|---|---|---|
| Paket-Shadowing `agent/cli_backends/` ↔ `agent/cli_backends.py` (existiert nicht, aber Namespace muss vorab geprüft werden) | niedrig | hoch | **Phase 0.1:** `ls -la agent/cli_backends.py agent/cli_backends/` vor dem ersten Schritt. Falls Datei existiert: anderen Namespace wählen (`agent/cli_backend_runtime/`). |
| Monkeypatch-Migration bricht `test_no_sgpt_in_propose_path.py` | mittel | mittel | Shim erhält die alte `agent.common.sgpt.run_sgpt_command`-Adresse. Tests können schrittweise migriert werden. |
| Service-Getter sind nicht thread-safe / flask-context-gebunden | hoch | mittel | `CliBackendContext` cached die Handles pro App-Context (analog zum `current_app.config`-Pattern), nicht pro Prozess. |
| Refactor in Welle 2 versteckt eine latente Sub-Bug | mittel | hoch | **Pre-Branch-Snapshot:** `pytest -k "sgpt or worker_tool_loop or workspace_mutation"` Baseline-Failures dokumentieren (Pitfall "passing test count with baseline failures"). Nach Welle 2: identische Failures als Erfolgs-Kriterium. |
| 18+ Routes müssen angefasst werden, falls Shim nicht re-exportiert | mittel | hoch | Shim-Phase 1+2 ist genau dafür: jede `agent/common/sgpt_*.py` wird zu 5-Zeilen-Re-Export. Kein Route-Touch in Welle 1. |
| `test_sgpt_route.py:11-15` mutiert `sgpt_route.user_requests.clear()` (in-place, keine Service-Setter) | mittel | mittel | Shim muss die gleiche Mutable-Structure exportieren, kein Re-Init. Verified per Pre-Commit-Test. |
| Refactor bringt mehr Module → mehr Import-Lines pro Test (pytest-collection langsamer) | niedrig | niedrig | Optional: Sammel-`__init__.py` für `agent/cli_backends/` mit `__all__` Liste. |

---

## 7. Akzeptanzkriterien (Definition of Done)

**Strukturell:**
- [ ] `agent/common/sgpt_*.py` haben **0** direkten `from agent.services.*`-Imports (lazy + top-level).
- [ ] `agent/common/sgpt_*.py` sind am Ende **gelöscht** (kein 12-Monats-Shim).
- [ ] `agent/cli_backends/` ist Source-of-Truth, alle 4 + 4 = 8 Sub-Module + 1 Public-API-Modul (nach 4-Split).
- [ ] Service-Zugriff in `agent/cli_backends/*.py` **ausschließlich** über `context.get_*_service`.
- [ ] `scripts/check_cli_backend_shim_imports.py` ist im Repo, mit `--strict` und `--json` Modus für CI.

**Test-Status:**
- [ ] **Welle 1 Ende:** alle bestehenden Tests grün, ohne dass Test-Imports geändert wurden (Shim reicht).
- [ ] **Welle 2 Ende:** alle bestehenden Tests grün, Monkeypatch-Targets ggf. angepasst auf neuen Namespace.
- [ ] **Welle 3 Ende:** Detektor exit 0, vollständige Test-Suite grün.
- [ ] Keine **neuen** Test-Failures im Vergleich zur Baseline (`pytest -k "sgpt or worker_tool_loop or workspace_mutation"` Snapshot dokumentiert in Commit-Body).
- [ ] `tests/test_no_sgpt_in_propose_path.py` Monkeypatch funktioniert.
- [ ] `tests/test_sgpt_route.py:11-15` Mutable-Structures (z.B. `user_requests.clear()`) funktionieren weiterhin.

**Neue Tests:**
- [ ] `tests/test_cli_backend_namespace_skeleton.py` — Import-Test des neuen Namespace.
- [ ] `tests/test_cli_backend_shim_deprecation.py` — `DeprecationWarning` wird emittiert.
- [ ] `tests/test_cli_backend_context_injection.py` — `CliBackendContext` Override funktioniert (DI-Test).
- [ ] `tests/test_cli_backend_solid_layout.py` — Erzwingt dass keine `agent.services.*` Imports in `agent/cli_backends/*.py` auftauchen (statische Analyse).
- [ ] `tests/test_cli_backends_workspace_mutation_split.py` — 4-Split-Tests für die Sub-Module.
- [ ] `tests/test_check_cli_backend_shim_imports.py` — Detektor-Skript selbst ist getestet (False-Positive-Free).

**Dokumentation:**
- [ ] `docs/cli-backends-architecture.md` — Vertrag, Module-Map, Migrations-Pfad, SOLID-Diagnose pro Sub-Modul.
- [ ] `AGENTS.md` — `agent.cli_backends` als "Domain-Subsystem" eingetragen, nicht "Common-Utility".
- [ ] `AGENTS.md` — `agent.common.audit` / `error_handler` / `signals` mit Verweis auf Cross-Cutting-Entscheidung.
- [ ] `CONTRIBUTING.md` — Hinweis: Neue LLM-CLI-Backends nach `agent/cli_backends/`, nicht `agent/common/`.
- [ ] `todos/todo.common-layer-deepen-services.md` — eigener Track für `audit.py`/`error_handler.py` Service-Locator-Pattern.

---

## 8. Backlog-Eintrag für `todo.<track>.json`

Datei: `todos/todo.sgpt-decouple-from-services.json`

```jsonc
{
  "version": 1,
  "owner": "ananta",
  "track": "sgpt-decouple-from-services",
  "status_scale": ["todo", "in_progress", "blocked", "done"],
  "priority_scale": ["P0", "P1", "P2"],
  "risk_scale": ["low", "medium", "high"],
  "purpose": "Option B (Sinking) entschieden: agent.common.sgpt_* (4237 LOC, 1+5+1+3+1 Service-Importe über 5 Module) wird in agent/cli_backends/ konsolidiert. 4-Split von sgpt_workspace_mutation.py (785 LOC) ist Teil von Welle 2. Detektor-getriebener Shim-Lifecycle: kein 12-Monats-Fenster, sondern sofortiger Cleanup bei 0 Importern. audit.py/error_handler.py/signals.py bleiben in agent.common (separate Architektur-Diskussion in todo.common-layer-deepen-services.md).",
  "source_of_truth": {
    "context": "agent/cli_backends/context.py",
    "helpers": "agent/cli_backends/helpers.py",
    "routing": "agent/cli_backends/routing.py",
    "semaphore": "agent/cli_backends/semaphore.py",
    "tool_loop": "agent/cli_backends/tool_loop.py",
    "workspace_mutation_root": "agent/cli_backends/workspace_mutation.py",
    "workspace_mutation_signatures": "agent/cli_backends/workspace_mutation/signatures.py",
    "workspace_mutation_prompts": "agent/cli_backends/workspace_mutation/prompts.py",
    "workspace_mutation_loop": "agent/cli_backends/workspace_mutation/loop.py",
    "workspace_mutation_tools": "agent/cli_backends/workspace_mutation/tools.py",
    "opencode": "agent/cli_backends/opencode.py",
    "architecture_scan": "agent/cli_backends/architecture_scan.py",
    "detector": "scripts/check_cli_backend_shim_imports.py"
  },
  "milestones": [
    { "id": "SGDEC-M1", "title": "Welle 1: Vertrag & Skelett", "evidence": "..." },
    { "id": "SGDEC-M2", "title": "Welle 2: Migration + 4-Split (atomic PR)", "evidence": "..." },
    { "id": "SGDEC-M3", "title": "Welle 3: Detektor + Cleanup", "evidence": "..." }
  ],
  "tasks": [
    { "id": "SGDEC-T01", "title": "Namespace-Skelett (agent/cli_backends/ + context.py)", "scope": "cli_backends", "priority": "P0", "risk": "medium" },
    { "id": "SGDEC-T02", "title": "Shim-Deprecation-Warning", "scope": "cli_backends", "priority": "P0", "risk": "low" },
    { "id": "SGDEC-T03", "title": "Source-Migration tool_loop + opencode + architecture_scan", "scope": "cli_backends", "priority": "P0", "risk": "high" },
    { "id": "SGDEC-T04", "title": "4-Split sgpt_workspace_mutation (signatures/prompts/loop/tools)", "scope": "cli_backends", "priority": "P0", "risk": "high" },
    { "id": "SGDEC-T05", "title": "Shim-Migration sgpt_workspace_mutation (Mutable-Structures erhalten)", "scope": "cli_backends", "priority": "P0", "risk": "medium" },
    { "id": "SGDEC-T06", "title": "Detektor-Skript scripts/check_cli_backend_shim_imports.py", "scope": "cli_backends", "priority": "P0", "risk": "low" },
    { "id": "SGDEC-T07", "title": "Importer-Migration: 3 Routes + 6 Tests + 1 Function", "scope": "cli_backends", "priority": "P0", "risk": "medium" },
    { "id": "SGDEC-T08", "title": "Shim löschen + Detektor-grün verifizieren", "scope": "cli_backends", "priority": "P0", "risk": "medium" }
  ],
  "open_decisions": [],
  "decision_points": [],
  "decision_log": [
    {
      "id": "SGDEC-D1",
      "decided_at": "2026-06-20T12:55:00Z",
      "decided_by": "user (akzeptiert Empfehlung)",
      "mode": "recommendation_accepted",
      "topic": "Architektur-Option für sgpt-Entkopplung",
      "decision": "Option B (Sinking): agent/cli_backends/ als neuer Namespace, lokale DI-Klasse CliBackendContext",
      "rationale": "Korrekte Schicht. agent.common bleibt dünn (Cross-Cutting-Utilities), sgpt-Subsystem bekommt eigene Domain-Heimat. Vermeidet Option A (agent.common wird zur Domain-Schicht) und Option C (versteckt DIP-Verstoß nur).",
      "consequence": "Migration von 4237 LOC + 18+ Importern + 4+ Tests. Welle 1+2+3 mit je einem Verifikations-Gate."
    },
    {
      "id": "SGDEC-D2",
      "decided_at": "2026-06-20T12:55:00Z",
      "decided_by": "user (alle Empfehlungen akzeptiert)",
      "mode": "recommendation_accepted",
      "topic": "4-Split von sgpt_workspace_mutation.py — separater Track oder Teil von Welle 2?",
      "decision": "Teil von Welle 2 (atomic), nicht separater Track",
      "rationale": "User-Vorgabe 'nichts aufschieben, nichts nur anfangen' verlangt die vollständige Umsetzung in einem Zug.",
      "consequence": "Welle 2 wird größer (eine PR mit 4 neuen Sub-Modulen + Re-Export-Modul), aber Review-Iteration-Kosten bleiben kalkulierbar."
    },
    {
      "id": "SGDEC-D3",
      "decided_at": "2026-06-20T12:55:00Z",
      "decided_by": "user (alle Empfehlungen akzeptiert)",
      "mode": "recommendation_accepted",
      "topic": "Shim-Lebensdauer — 6 Monate, 12 Monate oder sofort?",
      "decision": "Detektor-getrieben: sofortiger Cleanup wenn scripts/check_cli_backend_shim_imports.py 0 Importer meldet",
      "rationale": "User-Vorgabe 'nichts nur anfangen' verbietet 12-Monats-Shim, der als 'machen wir später' endet. Detektor + sofortige Migration = vollständige Lösung.",
      "consequence": "Welle 3 muss Detektor + Migration + Shim-Löschung als eine Einheit liefern."
    },
    {
      "id": "SGDEC-D4",
      "decided_at": "2026-06-20T12:55:00Z",
      "decided_by": "user (alle Empfehlungen akzeptiert)",
      "mode": "recommendation_accepted",
      "topic": "sgpt_helpers.py — mit umziehen oder im agent.common belassen?",
      "decision": "MIT umziehen nach agent/cli_backends/helpers.py",
      "rationale": "sgpt_helpers.py wird von allen anderen sgpt_-Modulen intensiv genutzt. Split zwischen agent/common/sgpt_helpers + agent/cli_backends/* würde Verwirrung stiften und 'Common importiert Domain' wieder einführen.",
      "consequence": "Auch agent.sgpt.function.py Import muss in Welle 3 migriert werden."
    },
    {
      "id": "SGDEC-D5",
      "decided_at": "2026-06-20T12:55:00Z",
      "decided_by": "user (alle Empfehlungen akzeptiert) + Korrektur durch Agent",
      "mode": "recommendation_accepted_with_correction",
      "topic": "audit.py / error_handler.py / signals.py — Service-Imports in gleichem Atemzug mit-migrieren?",
      "decision": "NEIN — bleiben in agent.common, eigene Cross-Cutting-Diskussion in todo.common-layer-deepen-services.md",
      "rationale": "audit.py hat 84+ Importer über alle Schichten (Routes, Services, Tools, Tests, Bootstrap, Background). Das ist keine DIP-Verletzung, sondern eine bewusste Cross-Cutting-Fassade. Service-Import dort = Service-Locator-Pattern, nicht Bug. Verschieben würde 84+ Importer brechen + neues Service-Locator-Pattern erfordern (eigener Architektur-Track). Gleiche Argumentation für error_handler.py (1 Service-Import für Logger, semantisch korrekt) und signals.py (Lazy-Import für Signal-Handler).",
      "consequence": "SGDEC-Track bleibt fokussiert auf sgpt-Subsystem. Cross-Cutting-Architektur-Diskussion wird in separaten Track todo.common-layer-deepen-services.md ausgelagert. In Welle 1 wird in audit.py/error_handler.py/signals.py ein Modul-Docstring-Eintrag ergänzt, der die Architektur-Entscheidung dokumentiert."
    }
  ]
}
```

---

## 9. Entscheidungen (alle decided)

**Alle 5 Entscheidungen sind entschieden** (siehe `decision_log` in Sektion 8):

| ID | Entscheidung | Modus |
|---|---|---|
| SGDEC-D1 | Option B (Sinking) | `recommendation_accepted` |
| SGDEC-D2 | 4-Split in Welle 2, nicht separater Track | `recommendation_accepted` |
| SGDEC-D3 | Detektor-getriebener Shim (sofort weg) | `recommendation_accepted` |
| SGDEC-D4 | `sgpt_helpers.py` zieht mit um | `recommendation_accepted` |
| SGDEC-D5 | `audit.py` / `error_handler.py` / `signals.py` bleiben + eigene Diskussion | `recommendation_accepted_with_correction` |

---

## 10. Verweise (Source-First Skill-Patterns)

- `ananta-subsystem-discovery` Pitfall: "monkeypatch target migration" — erklärt warum Shim wichtig ist.
- `ananta-subsystem-discovery` Pitfall: "lcg-package-shadowing" — präskriptiv für Welle 0.1.
- `ananta-subsystem-discovery` Pattern: "5-Module-Split" — Vorlage für Welle 2 T04.
- `ananta-subsystem-discovery` Pattern: "3-Welle-Order" — Vorlage für Wellen-Aufteilung.
- `ananta-subsystem-discovery` Reference: `refactor-before-plan-template.md` — für Welle 2 T04 Sub-Split.
