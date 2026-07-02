# Expert Registry
<!-- COSMOS-001 -->

## Zweck

Die Expert Registry definiert wiederverwendbare Worker-Templates ("Experts"). Jeder Expert
beschreibt eine Rolle — Dispatcher, PR Author, Reviewer usw. — inklusive erlaubter Tools,
Pfade, Modellrouting, Kontextstrategie, Output-Vertrag und Approval-Regeln.

Experts können keine eigenen Rechte erfinden. Die tatsächlich geltenden Rechte sind immer
die Schnittmenge aus Expert-Definition und aktiver Hub-Policy-Scope. Ein Expert mit
`allowed_tools: ["shell_exec"]` bekommt shell_exec nicht, wenn die Policy es nicht erlaubt.

---

## ExpertDefinition Schema (YAML)

```yaml
expert_id: pr_author          # eindeutig, snake_case
version: "1.0"                # semver-String, nicht float
title: "PR Author"
purpose: "Erstellt PR-Entwurf aus genehmigtem ChangeProposal"

allowed_tools:
  - read_file
  - diff_apply_proposal
  - git_status
denied_tools:
  - shell_exec
  - network_call

allowed_path_patterns:
  - "src/**"
  - "tests/**"
  - "docs/**"
denied_path_patterns:
  - ".env"
  - "secrets/**"
  - ".git/**"

model_routing:
  prefer_role: "coder"            # aus ModelProfile.model_role
  cost_class: ["free", "low"]     # aus ModelProfile.cost_class

context_strategy: "focused_diff_only"   # wie viel Kontext der Expert anfordert
output_contract: "diff_proposal"        # maschinenlesbarer Typ des Outputs

approval_gates:
  - apply_diff
  - create_pull_request

min_policy_scope: "project"   # "global" | "project" | "workspace"

extends: null                 # optional: base expert_id
```

Pflichtfelder: `expert_id`, `version`, `title`, `purpose`, `output_contract`.
Alle anderen Felder haben sichere Defaults (leere Allowlists, keine Modellpräferenz).

---

## Bekannte Experts

| expert_id              | Rolle / Zweck                                          | output_contract       |
|------------------------|--------------------------------------------------------|-----------------------|
| work_dispatcher        | Zerlegt Ziele in Schritte, weist Experts zu            | dispatch_plan         |
| code_context_analyst   | Analysiert Codebereich, liefert Kontextbundle          | context_bundle        |
| pr_author              | Erstellt PR-Entwurf aus genehmigtem ChangeProposal     | diff_proposal         |
| pair_reviewer          | Leichtgewichtiger erster Review-Pass                   | review_report         |
| deep_code_reviewer     | Tiefer Review: Architektur, Security, Tests            | review_report         |
| risk_analyst           | Bewertet PR-Risiko nach Dimensionen                    | risk_report           |
| test_runner            | Wählt Tests, führt sie in Sandbox aus                  | test_report           |
| security_reviewer      | Fokus auf Security-Funde und CVE-Relevanz              | risk_report           |
| documentation_writer   | Erstellt/aktualisiert Docs aus Code und Context        | documentation_patch   |
| release_assistant      | Vorbereitung Release-Notes, Changelog, Gate-Check      | release_summary       |

---

## Registry-Abfrage

Der Hub lädt Experts beim Start aus `config/experts/` (YAML oder JSON).

```
config/
  experts/
    pr_author.yaml
    deep_code_reviewer.yaml
    risk_analyst.yaml
    ...
```

Ladereihenfolge:
1. Built-in defaults aus `agent/experts/defaults/`
2. Projekt-Overrides aus `config/experts/` (überschreiben gleichnamige Defaults)

Versionskonflikte: Mehrere Dateien mit gleicher `expert_id` werden abgelehnt. Die neuere
Version muss explizit als Override registriert werden.

Validierung beim Laden:
- Pflichtfelder vorhanden
- `version` ist parsbares semver
- `allowed_tools` sind bekannte Tool-IDs (unbekannte Tools → Warnung + Ablehnung)
- `output_contract` ist registrierter Contract-Typ
- `extends`-Kette darf nicht zyklisch sein

---

## Vererbung

```yaml
expert_id: security_focused_reviewer
version: "1.0"
extends: deep_code_reviewer
purpose: "Deep Code Review mit Fokus auf Security-Funde"
denied_tools:
  - shell_exec
  - network_call
  - apply_diff          # Security Reviewer darf nie direkt anwenden
```

Semantik: Alle Felder aus `base_expert_id` werden übernommen. Explizit angegebene Felder
im erbenden Expert überschreiben das Base-Feld vollständig (kein Listen-Merge).
`denied_tools` ist additiv zur Base-Definition (Union, nie Subtraktion).

---

## Sicherheitsregeln

- Experts können keine Tools aus der Hub-Denylist freischalten.
- `denied_tools` eines Experts wird mit der Policy-Denylist per Union vereinigt.
- `allowed_path_patterns` wird mit Policy-`allowed_paths` per Schnittmenge berechnet.
- `min_policy_scope` kann ein Expert nicht selbst unterschreiten.
- Default-Experts laufen mit minimalen Rechten: leere Tool-Allowlist, kein Netzwerk.

---

## Tests

| Testfall                                | Erwartung                                       |
|-----------------------------------------|-------------------------------------------------|
| Gültige YAML-Expert-Datei laden         | ExpertDefinition-Objekt, alle Felder gesetzt    |
| Fehlende Pflichtfelder                  | ValidationError beim Laden                      |
| Unbekanntes Tool in allowed_tools       | Warnung, Expert wird abgelehnt                  |
| Zwei Dateien mit gleicher expert_id     | Konfliktfehler, kein Start                      |
| Expert mit extends-Kette (3 Ebenen)     | Felder korrekt übernommen/überschrieben         |
| Zyklische extends-Kette                 | CyclicInheritanceError                          |
| Expert versucht Policy-denied Tool      | Tool wird blockiert, run_id-gebundener Audit    |
| Expert mit min_policy_scope="project"   | Ablehnung in global-scope ohne Projekt-Kontext  |
