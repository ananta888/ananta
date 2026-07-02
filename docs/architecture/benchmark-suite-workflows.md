# Benchmark Suite: Ananta/CodeCompass Workflow-Evaluation
<!-- COSMOS-023 -->

## Zweck

Erweitert die bestehende Retrieval-Benchmark-Suite um vollständige End-to-End-Workflows,
die Cosmos-ähnliche Use Cases abdecken: Triage, Planung, Authoring, Review, Risk und Testing.

Messbar ist nicht nur Retrieval-Qualität, sondern Workflow-Qualität vom Input bis zum
entscheidbaren Output. Externe Systeme (Augment) bleiben optional; Fake-Modus ist Standard.

---

## Benchmark-Workflows

| Workflow-ID                | Input                         | Expected Output                    | Primäre Metriken                          |
|----------------------------|-------------------------------|------------------------------------|-------------------------------------------|
| `issue_to_plan`            | Issue-Text                    | DispatchPlan                       | Vollständigkeit, Schritt-Korrektheit, Zeit, Kosten |
| `plan_to_change_proposal`  | Plan + Kontext-Bundle         | ChangeProposal + Diff              | Korrektheit, Scope-Treffer, False-Edits   |
| `change_to_pr_draft`       | Diff + TestReport + RiskReport | PR-Body (Markdown)                | Abschnittsvollständigkeit, Qualitätsscore |
| `diff_to_deep_review`      | Diff + Kontext-Bundle         | ReviewReport                       | Blocking-Findings (Precision/Recall), False-Positives |
| `diff_to_risk_report`      | Diff + Kontext-Graph          | RiskReport                         | Score-Kalibrierung vs. Goldstandard       |
| `diff_to_test_selection`   | Diff + Callgraph              | Test-Set (Dateiliste)              | Coverage-Recall, False-Negatives          |
| `failed_ci_to_root_cause`  | CI-Log + Kontext-Bundle       | Root-Cause-Beschreibung + Fix-Vorschlag | Korrektheit, Zeit, Kosten           |

---

## Workflow-Schema

```yaml
benchmark_workflow:
  workflow_id: "diff_to_risk_report"
  version: "1.0"
  description: "Bewertet RiskReport-Qualität für einen gegebenen Diff"

  input_fixture: "benchmarks/fixtures/diff_to_risk_report/input_001.json"
  gold_file: "benchmarks/gold/diff_to_risk_report/gold_001.json"

  mode: codecompass_only    # codecompass_only | augment_only | hybrid

  metrics:
    - score_calibration_mae      # Mean Absolute Error vs. Gold-Score
    - dimension_precision        # Korrekt erkannte Dimensionen / Gesamt erkannt
    - dimension_recall           # Korrekt erkannte Dimensionen / Gesamt im Gold
    - evidence_present           # bool: Alle Funde haben Evidence-Zitat
    - latency_seconds
    - token_cost_usd

  eval_method: rubric           # rubric | exact_match | llm_judge
```

---

## Gold-Set Format

```json
{
  "workflow_id": "diff_to_risk_report",
  "fixture_id": "input_001",
  "gold_type": "rubric",
  "rubric": {
    "security_elevated": true,
    "test_gap_present": true,
    "overall_score_range": [50, 80],
    "required_evidence_patterns": [
      "src/auth/token.py",
      "JWT"
    ],
    "blocking_not_expected": false
  },
  "notes": "Fixture enthält JWT ohne Expiry — Security muss elevated sein"
}
```

Gold-Sets sind versionierte JSON-Dateien in `benchmarks/gold/<workflow_id>/`.
Manuelle Rubrik (menschlich bewertet) ist zulässig für subjektive Qualitätsdimensionen.

---

## Modi

| Modus               | Beschreibung                                              | Wann nutzen                          |
|---------------------|-----------------------------------------------------------|--------------------------------------|
| `codecompass_only`  | Nur lokaler CodeCompass-Graph, keine externen APIs        | Standard, immer verfügbar            |
| `augment_only`      | Nur Augment Context Engine (API-Key erforderlich)         | Direktvergleich mit externem System  |
| `hybrid`            | CodeCompass + Augment parallel, Ergebnis-Merge            | Evaluierung Hybrid-Ansatz            |

Default und CI-Standard: `codecompass_only`. Andere Modi brauchen explizite Config.

---

## Metriken

| Metrik                    | Einheit          | Beschreibung                                          |
|---------------------------|------------------|-------------------------------------------------------|
| `latency_seconds`         | s                | Wall-Clock-Zeit von Input bis Output                  |
| `token_cost_usd`          | USD              | Hochgerechnete Modellkosten (aus Usage-Response)      |
| `context_hit_rate`        | %                | Anteil relevanter Kontext-Chunks im Bundle            |
| `error_rate`              | %                | Anteil Läufe mit Fehler/Timeout                       |
| `approval_required_rate`  | %                | Anteil Läufe, die HITL-Gate ausgelöst haben           |
| `test_coverage_recall`    | %                | Anteil Gold-Testdateien im selektierten Test-Set      |
| `score_calibration_mae`   | Score-Einheiten  | MAE zwischen RiskReport-Score und Gold-Score          |
| `finding_precision`       | %                | Korrekte Findings / Gesamt erzeugte Findings          |
| `finding_recall`          | %                | Korrekte Findings / Gesamt im Gold                    |

---

## Verzeichnisstruktur

```
benchmarks/
  fixtures/
    issue_to_plan/
      input_001.json
      input_002.json
    diff_to_risk_report/
      input_001.json
  gold/
    issue_to_plan/
      gold_001.json
    diff_to_risk_report/
      gold_001.json
  results/
    2026-07-01/
      run_summary.json
      run_summary.md          # optionaler Markdown-Report
      diff_to_risk_report_001.json
```

---

## Output-Format

```json
{
  "run_id": "bench-<uuid>",
  "timestamp": "2026-07-01T10:00:00Z",
  "mode": "codecompass_only",
  "workflows": [
    {
      "workflow_id": "diff_to_risk_report",
      "fixture_id": "input_001",
      "verdict": "pass",
      "metrics": {
        "latency_seconds": 4.2,
        "token_cost_usd": 0.003,
        "score_calibration_mae": 8,
        "dimension_recall": 0.85,
        "evidence_present": true
      },
      "notes": "Security-Dimension korrekt elevated; test_gap leicht unterschätzt"
    }
  ],
  "aggregate": {
    "total_workflows": 7,
    "passed": 6,
    "failed": 1,
    "avg_latency_seconds": 5.1,
    "total_cost_usd": 0.021
  }
}
```

Zusätzlich: Markdown-Report in `results/<datum>/run_summary.md` (optional, per Config).

---

## Fake-Modus

Alle Workflows laufen ohne echten Augment-Zugang. Externe Calls werden durch
`FakeAugmentProvider` ersetzt, der vordefinierte Fixture-Antworten zurückgibt.

```yaml
benchmark:
  fake_mode: true           # Default: true
  fake_provider_fixtures: "benchmarks/fakes/augment_responses/"
```

---

## Tests

| Testfall                                         | Erwartung                                           |
|--------------------------------------------------|-----------------------------------------------------|
| `issue_to_plan` mit klarem Issue                 | DispatchPlan mit ≥2 Schritten, verdict=pass         |
| `diff_to_risk_report` mit Security-Diff          | score≥50, security-Dimension elevated               |
| `diff_to_test_selection` ohne Callgraph          | Test-Set leer oder minimal, error protokolliert     |
| `failed_ci_to_root_cause` mit bekanntem Fehler   | Root-Cause korrekt, Korrektheit-Metrik berechnet    |
| Benchmark-Lauf im Fake-Modus ohne API-Key        | Alle Workflows durchlaufen, kein externer Call      |
| Ergebnis-JSON validiert gegen Schema             | Alle Pflichtfelder vorhanden                        |
