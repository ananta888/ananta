# Expert: Deep Code Reviewer
<!-- COSMOS-016 -->

## Zweck

Tiefer Code-Review mit Fokus auf Architektur, Security, Testabdeckung, Regressionsrisiken
und Policy-Verletzungen. Jeder Fund verweist auf konkreten Evidence aus Diff oder
Kontextbundle — keine pauschalen Aussagen.

Der Expert kann keine Änderungen direkt anwenden. Security-Funde werden zusätzlich als
separates `risk_report`-Artefakt gespeichert.

---

## Input

| Feld             | Typ            | Beschreibung                                          |
|------------------|----------------|-------------------------------------------------------|
| `diff`           | DiffPatch      | Zu reviewender Patch                                  |
| `context_bundle` | ContextBundle  | Relevanter Codekontext (CodeCompass-Abfrage)          |
| `test_refs`      | []TestRef      | Referenzen auf relevante Tests (aus Test-Selektion)   |
| `policy_scope`   | PolicyScopeRef | Geltende Policy: welche Regeln geprüft werden         |

---

## Output: ReviewReport

```yaml
review_report:
  report_id: "rev-<uuid>"
  run_id: "<run_id>"
  reviewer_expert: deep_code_reviewer
  created_at: "<iso8601>"

  summary:
    blocking_count: 2
    warning_count: 4
    suggestion_count: 3
    question_count: 1
    overall_verdict: needs_changes   # approved | needs_changes | rejected

  findings:
    - id: "f1"
      severity: blocking             # blocking | warning | suggestion | question
      category: security             # security | architecture | test_gap | regression | policy | style
      title: "SQL-Konkatenation ohne Parametrisierung"
      location:
        file: "src/db/query.py"
        line_start: 42
        line_end: 44
      evidence: "Diff+42: f\"SELECT * FROM users WHERE id={user_id}\""
      policy_ref: null
      recommendation: "Parametrisierte Query verwenden"

    - id: "f2"
      severity: warning
      category: test_gap
      title: "Neuer Pfad hat keine Tests"
      location:
        file: "src/api/export.py"
        line_start: 88
      evidence: "Kein Test in tests/ referenziert export.py"
      recommendation: "Unit-Test für export_handler hinzufügen"

  security_findings_artifact: "runs/<run_id>/security_findings.json"
  # Alle findings mit category=security werden zusätzlich hier gespeichert
```

---

## Expert-Definition (Auszug)

```yaml
expert_id: deep_code_reviewer
version: "1.0"
purpose: "Tiefer Code-Review: Architektur, Security, Tests, Regressionen, Policy"
allowed_tools:
  - read_file
  - read_artifact
  - read_context_bundle
  - search_test_refs
denied_tools:
  - apply_diff
  - shell_exec
  - network_call
  - create_pull_request
output_contract: review_report
approval_gates: []
```

---

## Severity-Klassifikation

| Severity     | Bedeutung                                              | Wirkung                          |
|--------------|--------------------------------------------------------|----------------------------------|
| `blocking`   | Fehler, der Change verhindert (Security, Datenverlust) | Blockiert Apply-/Merge-Gate      |
| `warning`    | Signifikantes Problem, sollte behoben werden           | Sichtbar im PR, kein hartes Gate |
| `suggestion` | Verbesserungsidee, kein Pflichtpunkt                   | Optional                         |
| `question`   | Unklar — Klärung durch Autor nötig                     | Kein Gate, aber Sichtbarkeit     |

---

## Grenzen

- Kann keine Änderungen anwenden (`apply_diff` ist denied).
- Security-Funde erzeugen immer ein eigenes `risk_report`-Artefakt, unabhängig vom Verdict.
- Alle Funde müssen Evidence aus Diff oder ContextBundle zitieren.
- Pauschalaussagen wie "könnte unsicher sein" ohne konkrete Stelle sind nicht zulässig.

---

## Tests

| Testfall                                      | Erwartung                                             |
|-----------------------------------------------|-------------------------------------------------------|
| Diff mit SQL-Injection-Pattern                | Finding severity=blocking, category=security          |
| Diff ohne neue Tests, neuer Pfad vorhanden    | Finding severity=warning, category=test_gap           |
| Finding ohne Evidence-Angabe                  | ValidationError bei Report-Serialisierung             |
| Security-Finding vorhanden                    | security_findings_artifact erzeugt                    |
| Verdict=approved bei 0 blocking               | overall_verdict=approved                              |
| Expert versucht apply_diff                    | Tool-Aufruf abgelehnt, im Audit protokolliert         |
