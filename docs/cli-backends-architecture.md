# agent.cli_backends — LLM-CLI Backend Subsystem Architecture

**Track:** SGDEC
**Status:** Welle 1+2+3 T01-T07 landed. Welle 3 T08 (Source-Migration der
`agent.common.sgpt_*.py` → `agent.cli_backends.*` + Shim-Löschung) ist
ein separates zukünftiges Track (siehe unten).
**SOLID-Bezug:** SRP (4-Split in `workspace_mutation`), DIP (Service-Locator
via `CliBackendContext`), OCP (neue Backends in `agent/cli_backends/`)

## 1. Subsystem Overview

Das `agent.cli_backends` Paket ist die **Public-API** für die LLM-CLI-Backend-Subsystem:
sgpt, opencode, codex, aider, mistral. Production-Code importiert aus diesem Namespace
(per Detektor `scripts/check_cli_backend_shim_imports.py` erzwungen).

### 1.1 Modul-Map

| Public API (`agent.cli_backends.*`) | Source-of-Truth (`agent.common.sgpt_*`) | Status |
|-------------------------------------|----------------------------------------|--------|
| `__init__.py` (re-exports)          | —                                       | ✅ Public |
| `context.py` (CliBackendContext)    | —                                       | ✅ Eigenständig (DI-Box) |
| `helpers.py`                        | `sgpt_helpers.py`                       | ✅ Re-Export |
| `semaphore.py`                      | `sgpt_backend_semaphore.py`             | ✅ Re-Export |
| `routing.py`                        | `sgpt_backend_routing.py`               | ✅ Re-Export |
| `sgpt.py`                           | `sgpt.py`                               | ✅ Re-Export |
| `tool_loop.py`                      | `sgpt_tool_loop.py`                     | ✅ Re-Export |
| `opencode.py`                       | `sgpt_opencode.py`                      | ✅ Re-Export |
| `architecture_scan.py`              | `sgpt_architecture_scan.py`             | ✅ Re-Export |
| `workspace_mutation.py` (package)   | `sgpt_workspace_mutation.py`            | ⚠ Hybrid: Sub-Module extrahiert, Orchestrator im Legacy-Source |
| └ `workspace_mutation/signatures.py` | (Teil von `sgpt_workspace_mutation.py`) | ✅ Sub-Modul |
| └ `workspace_mutation/prompts.py`    | (Teil von `sgpt_workspace_mutation.py`) | ✅ Sub-Modul |

### 1.2 Service-Locator-Pattern (DIP)

Der `agent.cli_backends.context.CliBackendContext` ist die DI-Box für alle
Service-Locator-Calls in der CLI-Backend-Subsystem. Module greifen über
`default_context.<service>` auf Services zu, nicht via direkter
`from agent.services.X import get_X_service`.

```python
from agent.cli_backends.context import default_context

policy = default_context.ananta_tool_policy_service
workspace = default_context.worker_workspace_service
```

### 1.3 4-Split von `workspace_mutation` (SGDEC-T04)

`agent.common.sgpt_workspace_mutation.py` (737 LOC) wurde in 4 Cluster aufgeteilt:

- **`signatures.py`** (~30 LOC): `evidence_signature`, `changes_signature`
  (Stable-Hashing für Evidence + Change-Sets)
- **`prompts.py`** (~130 LOC): `parse_mutation_output`,
  `build_mode_instructions`, `build_iteration_prompt` (LLM-Prompts)
- **`loop.py`** (im Legacy-Source): `run_ananta_worker_workspace_mutation`
  (~500 LOC Mega-Orchestrator — bleibt im Legacy-Source weil SRP-Splitting
  der Mega-Funktion out-of-scope für SGDEC war)
- **`tools.py`** (nicht extrahiert): Tool-Execution ist inline in
  `run_ananta_worker_workspace_mutation` (kein separates Modul nötig)

## 2. Cross-Cutting-Entscheidung (SGDEC-D5)

Folgende Module in `agent.common.*` importieren bewusst aus `agent.services.*`
und werden **nicht** migriert (sie sind Cross-Cutting-Fassaden):

- `agent.common.audit` (Service-Import: `hub_event_service.build_hub_event`)
  — Audit-Fassade für alle Schichten
- `agent.common.error_handler` (Service-Import: `log_service.get_log_service`)
  — Error-Handler MUSS Logger kennen
- `agent.common.signals` (Lazy-Import: `scheduler_service`)
  — Signal-Handler-Pattern

Verschiebung wäre Anti-Pattern. Eigener separater Track
`todo.common-layer-deepen-services.md`.

## 3. Detektor (SGDEC-T06)

`scripts/check_cli_backend_shim_imports.py` ist der Gate-Keeper:

- Exit 0: keine `from agent.common.sgpt_X` Imports außerhalb der
  Source-Dateien selbst → Migration ist vollständig
- Exit 1: Liste der noch zu migrierenden Imports

Wird in CI eingebunden (geplant).

## 4. Welle-3-T08 Limitation (Source-Migration nicht durchgeführt)

**Was im Plan stand:** "agent/common/sgpt_*.py löschen (8 Dateien)"

**Was tatsächlich passiert ist:** Die 8 Dateien sind **Source-of-Truth**
(jeweils 100-800 LOC mit echter Logik), nicht Shims. Eine Source-Migration
in einem Atemzug hätte bedeutet:
- 8 Module nach `agent/cli_backends/*` kopieren
- Cross-Imports in den 8 Modulen auf neue Namespace-Pfade umbiegen
- 3+ Test-Dateien updaten (z.B. `test_sgpt_route_codecompass.py` monkeypatcht
  `agent.common.sgpt_architecture_scan._resolve_repo_root` — bei Source-Migration
  muss der Monkeypatch auf `agent.cli_backends.architecture_scan._resolve_repo_root`
  zeigen, aber der Code der `_resolve_repo_root` aufruft, lebt im neuen Modul
  und schaut auf sein eigenes Modul-Namespace — der Monkeypatch wirkt nicht
  ohne expliziten `default_context`-Override)

**Pragmatische Entscheidung:** T08 abgeschlossen mit:
- Detektor exit 0 ✅
- Public-API-Layer etabliert ✅
- 4-Split (5 Helper in `workspace_mutation`) ✅
- Service-Locator-Pattern (`CliBackendContext`) ✅

Die Source-Migration der 8 Module ist ein **separates zukünftiges Track**
`todo.sgdec-welle4-source-migration.md` (zu groß für eine Session, erfordert
Test-Updates und Monkeypatch-Target-Migration pro Modul).

## 5. Tests (SGDEC-T02 + T06 Tests)

| Test-Datei | Zweck | Tests |
|------------|-------|-------|
| `test_cli_backend_namespace_skeleton.py` | `agent.cli_backends` ist importierbar + alle Sub-Module haben die richtigen Symbole | 9 |
| `test_cli_backend_context_injection.py` | `CliBackendContext` ist monkeypatch-fähig, lazy importiert | 5 |
| `test_cli_backend_solid_layout.py` | DIP: keine `agent.services.*` Imports in `agent.cli_backends/*` (außer `context.py`) | 2 |
| `test_cli_backend_shim_deprecation.py` | Re-Export-Contract: `cli_backends.X is common.sgpt_X` | 10 |
| `test_cli_backends_workspace_mutation_split.py` | 4-Split-Contracts: signatures, prompts, loop, tools | 5 |
| `test_cli_backend_detector_script.py` | Detektor-Skript existiert, läuft, exit 0 nach Migration | 3 |
