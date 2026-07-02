# Expert: Risk Analyst
<!-- COSMOS-017 -->

## Zweck

Bewertet das Risiko eines PRs oder Changes systematisch anhand definierter Dimensionen,
Evidence aus Diff und Kontext, und empfiehlt konkrete Verifikationsschritte.

Der Expert darf keine Änderung anwenden. Fehlende Tests werden als Risiko bewertet —
nicht durch Ratschläge kompensiert.

---

## Input

| Feld              | Typ            | Beschreibung                                          |
|-------------------|----------------|-------------------------------------------------------|
| `diff`            | DiffPatch      | Zu bewertender Patch                                  |
| `context_bundle`  | ContextBundle  | Codekontext: abhängige Module, Callgraph, Testbezüge  |
| `policy_scope`    | PolicyScopeRef | Policy-Kontext: welche Dateien als sensitiv gelten    |
| `change_proposal` | ChangeProposal | Optional: Ziel und Scope des Changes                  |

---

## Output: RiskReport

```yaml
risk_report:
  report_id: "risk-<uuid>"
  run_id: "<run_id>"
  created_at: "<iso8601>"
  overall_score: 62          # 0 (kein Risiko) bis 100 (kritisch)
  verdict: elevated          # low | medium | elevated | critical

  dimensions:
    security:
      score: 80
      evidence:
        - "src/auth/token.py:34 — JWT ohne Expiry-Prüfung"
      recommendation: "Expiry-Validierung vor Merge sicherstellen"

    data_loss:
      score: 10
      evidence: []
      recommendation: null

    api_breakage:
      score: 50
      evidence:
        - "api/v1/users — Signatur geändert, 3 externe Aufrufer im Graphen"
      recommendation: "Backwards-Kompatibilität prüfen oder Version bumpen"

    test_gap:
      score: 70
      evidence:
        - "src/auth/token.py hat keine Testdatei; neuer Code in 42 Zeilen"
      recommendation: "Unit-Tests für token.py vor Merge ergänzen"

    runtime_path_criticality: {score: 40, evidence: ["Liegt auf Request-Hot-Path"]}
    config_change:            {score: 0,  evidence: []}
    migration_or_schema_change: {score: 0, evidence: []}
    dependency_change:        {score: 20, evidence: ["requirements.txt: cryptography 41→42"]}
    policy_change:            {score: 0,  evidence: []}

  auto_elevated_by:
    - "security_file_touched: src/auth/token.py"
    - "test_gap: keine Tests für geänderten Code"

  required_verifications:
    - "Manuelle Security-Review: JWT-Handling"
    - "API-Konsumenten prüfen: api/v1/users"
    - "Tests für src/auth/token.py hinzufügen"
```

---

## Expert-Definition (Auszug)

```yaml
expert_id: risk_analyst
version: "1.0"
purpose: "Bewertet PR-Risiko nach Dimensionen mit Evidence"
allowed_tools:
  - read_file
  - read_artifact
  - read_context_bundle
  - read_call_graph
denied_tools:
  - apply_diff
  - shell_exec
  - network_call
output_contract: risk_report
approval_gates: []
```

---

## Automatische Risiko-Erhöhung

Folgende Bedingungen erhöhen den Score einer Dimension automatisch, unabhängig vom LLM-Urteil:

| Bedingung                                      | Betroffene Dimension | Mindest-Score |
|------------------------------------------------|----------------------|---------------|
| Security-/Auth-/Crypto-Datei geändert          | `security`           | 60            |
| CI-Konfiguration geändert (.github/**, CI.yml) | `policy_change`      | 50            |
| Kein Test für neuen/geänderten Code            | `test_gap`           | 40            |
| DB-Schema oder Migration geändert              | `migration_or_schema_change` | 70    |
| Direkte Dependency-Version geändert            | `dependency_change`  | 20            |
| Policy-Datei geändert (policies/**)            | `policy_change`      | 70            |

---

## Score-Aggregation

`overall_score = max(dimension_scores) * 0.5 + weighted_average(dimension_scores) * 0.5`

Verdict-Schwellen (konfigurierbar per Policy):
- 0–24 → `low`
- 25–49 → `medium`
- 50–74 → `elevated`
- 75–100 → `critical`

---

## Grenzen

- Darf keine Änderung anwenden oder vorschlagen — nur bewerten.
- Fehlende Tests sind ein Risiko-Signal (`test_gap`), keine Aufgabe für diesen Expert.
- Score darf nicht durch Empfehlungen künstlich gesenkt werden (z.B. "wenn Tests nachgereicht
  werden, wäre Score niedriger") — Score reflektiert aktuellen Zustand.

---

## Tests

| Testfall                                         | Erwartung                                       |
|--------------------------------------------------|-------------------------------------------------|
| Diff berührt src/auth/, keine Tests vorhanden    | security≥60, test_gap≥40, auto_elevated_by gefüllt |
| Diff nur Dokumentation                           | overall_score≤10, verdict=low                   |
| DB-Migration in Diff enthalten                   | migration_or_schema_change≥70                   |
| Alle Dimensionen evidence-frei                   | Scores=0, required_verifications=[]             |
| Expert versucht apply_diff                       | Tool abgelehnt, Audit-Event erzeugt             |
