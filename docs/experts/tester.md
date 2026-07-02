# Expert: Tester
<!-- COSMOS-018 -->

## Zweck

Wählt passende Tests für einen Diff aus, führt sie in Sandbox oder kontrolliertem
Task-Workspace aus und erstellt TestReport-Artefakte. Der Expert schlägt Testbefehle
vor — die tatsächliche Ausführung kommt aus Projektconfig und Policy (der Hub entscheidet).

---

## Input

| Feld             | Typ            | Beschreibung                                              |
|------------------|----------------|-----------------------------------------------------------|
| `diff`           | DiffPatch      | Geänderter Code (für Test-Selektion)                      |
| `context_bundle` | ContextBundle  | Callgraph, Testbezüge, bekannte Testdateien               |
| `project_config` | ProjectConfig  | Testbefehl-Quelle: pytest-Pfad, make-Targets, CI-Befehle  |
| `sandbox_ref`    | SandboxRef     | Verweis auf bereitzustellende Sandbox-Instanz             |
| `policy_scope`   | PolicyScopeRef | Welche Testpfade erlaubt sind                             |

---

## Test-Selektion

Auswahl nach Priorität:

1. **Direkt betroffene Tests** — Testdateien, die geänderte Module importieren (aus Callgraph).
2. **Pfad-assoziierte Tests** — `tests/test_<modul>.py` zu geänderten `src/<modul>.py`.
3. **Regressions-Kandidaten** — Module, die geänderte Funktionen aufrufen (Callgraph-Tiefe 1).
4. **Full Suite** — Nur wenn Policy es erlaubt und kein Subset identifizierbar.

Der Expert gibt einen `test_selection`-Artefakt aus, bevor die Ausführung startet.
Der Hub kann die Auswahl überschreiben oder verkleinern (Policy-Grenzen).

---

## Output: TestReport

```yaml
test_report:
  report_id: "test-<uuid>"
  run_id: "<run_id>"
  sandbox_ref: "<sandbox_id>"
  created_at: "<iso8601>"

  selection:
    strategy: "direct_and_regression"
    test_files:
      - "tests/test_token.py"
      - "tests/test_auth_flow.py"
    rationale: "src/auth/token.py geändert; 2 direkte Test-Imports gefunden"

  execution:
    command: "pytest tests/test_token.py tests/test_auth_flow.py -v --tb=short"
    source: "project_config.test_command_template"
    started_at: "<iso8601>"
    finished_at: "<iso8601>"
    duration_seconds: 8.4
    exit_code: 0

  results:
    total: 14
    passed: 14
    failed: 0
    skipped: 1
    flaky: []              # Test-IDs die als flaky markiert wurden
    timeout: []            # Test-IDs die timeouted sind

  verdict: passed          # passed | failed | partial | blocked | timeout
  gate_effect: none        # none | blocks_apply | blocks_merge

  log_excerpt: |
    ========================= 14 passed, 1 skipped =========================
    PASSED tests/test_token.py::test_token_expiry
    ...

  artifact_paths:
    - "runs/<run_id>/test_report.json"
    - "runs/<run_id>/pytest_output.txt"
```

---

## Expert-Definition (Auszug)

```yaml
expert_id: test_runner
version: "1.0"
purpose: "Wählt Tests aus, führt sie in Sandbox aus, erzeugt TestReport"
allowed_tools:
  - read_file
  - read_context_bundle
  - read_call_graph
  - sandbox_exec
  - sandbox_copy_in
  - sandbox_copy_out
denied_tools:
  - apply_diff
  - network_call
  - create_pull_request
output_contract: test_report
approval_gates:
  - run_tests_in_sandbox
```

---

## Gate-Wirkung bei Fehlern

| Situation                         | verdict      | gate_effect    |
|-----------------------------------|--------------|----------------|
| Alle Tests bestanden              | `passed`     | `none`         |
| ≥1 Test fehlgeschlagen            | `failed`     | `blocks_apply` |
| Flaky Tests, Rest bestanden       | `partial`    | `blocks_merge` (je Policy) |
| Timeout überschritten             | `timeout`    | `blocks_apply` |
| Keine Tests selektierbar          | `blocked`    | `blocks_apply` (wenn Policy test_required=true) |

Gate-Wirkung ist Policy-konfigurierbar; Default: `failed` → blockiert Apply.

---

## Grenzen

- Testbefehle kommen aus `project_config`, nicht aus Expert-Eigenentscheidung.
- Expert kann Befehle vorschlagen; Hub bestätigt und startet Sandbox.
- Sandbox-Ausführung ist auditiert (start, exec, stop als Audit-Events).
- Netzwerk in Sandbox ist per Default deaktiviert.

---

## Tests

| Testfall                                          | Erwartung                                          |
|---------------------------------------------------|----------------------------------------------------|
| Diff mit direktem Test-Import, Tests passen       | verdict=passed, gate_effect=none                   |
| Diff mit Test, exit_code=1                        | verdict=failed, gate_effect=blocks_apply           |
| Kein Test für neuen Code, policy test_required    | verdict=blocked, gate_effect=blocks_apply          |
| Flaky Test (3 Versuche, 1 fail)                   | flaky=[test_id], verdict=partial                   |
| Sandbox-Timeout nach 60s                          | timeout=[test_id], verdict=timeout                 |
| Expert versucht apply_diff aus Sandbox heraus     | Tool abgelehnt, Audit-Event                        |
