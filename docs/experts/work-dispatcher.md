# Expert: Work Dispatcher
<!-- COSMOS-019 -->

## Zweck

Der Work Dispatcher zerlegt ein Ziel in sichere, geordnete Schritte, weist jedem Schritt
einen geeigneten Expert oder Worker zu und erzeugt einen prüfbaren DispatchPlan. Der
Dispatcher führt **nichts direkt aus** — er plant ausschließlich.

Unklare Ziele führen zu Analyse- oder Klärungsschritten, nie zu riskanter Direktausführung.
Der erzeugte Plan muss vor Ausführung durch einen Menschen (oder eine konfigurierte Policy)
freigegeben werden.

---

## Input

| Feld             | Typ              | Beschreibung                                              |
|------------------|------------------|-----------------------------------------------------------|
| `goal_text`      | string           | Natürlichsprachiges Ziel                                  |
| `context_bundle` | ContextBundle    | Vom Hub bereitgestellter Kontext (CodeCompass, Artefakte) |
| `policy_scope`   | PolicyScopeRef   | Geltende Policy: erlaubte Experts, Tools, Pfade           |
| `run_id`         | uuid             | Lauf-ID aus dem Hub                                       |

---

## Output: DispatchPlan

```yaml
dispatch_plan:
  plan_id: "plan-<uuid>"
  goal_ref: "<goal_id>"
  run_id: "<run_id>"
  status: draft              # draft | approved | rejected | executing
  created_at: "<iso8601>"

  steps:
    - step_id: "s1"
      title: "Kontext analysieren"
      expert_id: code_context_analyst
      inputs:
        - context_bundle
      outputs:
        - context_report
      approval_gate: null    # null = kein explizites Gate nötig
      risk_level: low

    - step_id: "s2"
      title: "Änderung entwerfen"
      expert_id: pr_author
      depends_on: ["s1"]
      inputs:
        - context_report
        - goal_text
      outputs:
        - diff_proposal
      approval_gate: apply_diff
      risk_level: medium

  risks:
    - "Unklare Datei-Scope — Analyse-Schritt notwendig"
  open_questions:
    - "Welche Tests müssen nach dem Change laufen?"
```

---

## Expert-Definition (Auszug)

```yaml
expert_id: work_dispatcher
version: "1.0"
purpose: "Zerlegt Ziele in Schritte, weist Experts zu, erzeugt DispatchPlan"
allowed_tools:
  - read_context_bundle
  - read_policy_scope
  - list_experts
denied_tools:
  - shell_exec
  - apply_diff
  - network_call
  - create_pull_request
output_contract: dispatch_plan
approval_gates:
  - dispatch_plan_approve
```

---

## Grenzen

- Kann keine Policy-Rechte erweitern oder neue Tools freischalten.
- Darf keinen Schritt als "keinen Approval nötig" markieren, wenn die Policy ein Gate verlangt.
- Unklare Ziele → `open_questions` + Klärungsschritt, kein Raten.
- Kann keine anderen Experts direkt instanziieren; das übernimmt der Hub nach Plan-Freigabe.

---

## Approval-Ablauf

```
[Goal eingetroffen]
      │
      ▼
[Dispatcher erzeugt DispatchPlan (status: draft)]
      │
      ▼
[HITL-Gate: Plan-Review durch Operator]
      │
  approved ──► [Hub führt Schritte sequenziell/parallel aus]
  rejected ──► [Plan verworfen, Grund gespeichert]
```

---

## Tests

| Testfall                                        | Erwartung                                          |
|-------------------------------------------------|----------------------------------------------------|
| Klares Ziel mit bekannten Experts               | DispatchPlan mit ≥1 Schritt, status=draft          |
| Unklares Ziel ohne konkreten Scope              | open_questions befüllt, Klärungsschritt eingefügt  |
| Dispatcher versucht Policy-denied Expert        | Schritt abgelehnt, Fehler im Plan protokolliert    |
| Plan enthält Approval-Gate bei risk_level=high  | approval_gate nicht null                           |
| Plan-Erzeugung ohne executions (pure planning)  | Kein Toolcall außer read_context_bundle/policy     |
