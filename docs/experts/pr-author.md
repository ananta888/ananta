# Expert: PR Author
<!-- COSMOS-015 -->

## Zweck

Der PR Author erstellt aus einem genehmigten ChangeProposal einen vollständigen PR-Entwurf:
strukturierten PR-Body (Ziel, geänderte Dateien, Testergebnisse, Risiken, offene Punkte)
und optional einen GitHub Pull Request Draft (wenn die Integration aktiv ist).

Kein automatischer Merge. Die PR-Erstellung selbst ist ein separates Approval-Gate.

---

## Input

| Feld              | Typ              | Beschreibung                                         |
|-------------------|------------------|------------------------------------------------------|
| `change_proposal` | ChangeProposal   | Muss status=approved haben — wird abgelehnt sonst    |
| `diff`            | DiffPatch        | Vollständiger Patch, der im PR landet                |
| `test_results`    | TestReport       | Testergebnisse aus dem Tester Expert                 |
| `risk_report`     | RiskReport       | Risikobewertung aus dem Risk Analyst                 |
| `policy_scope`    | PolicyScopeRef   | Geltende Policy für diesen Lauf                      |

---

## Output

### PR-Body (immer erzeugt)

```markdown
## Ziel
<Kurzfassung aus change_proposal.summary>

## Geänderte Dateien
- src/foo/bar.py  (+42 / -3)
- tests/test_bar.py  (+18 / -0)

## Testergebnisse
- Status: PASSED (12/12)
- Laufzeit: 4.3s
- Artefakt: runs/<run_id>/test_report.json

## Risikobewertung
- Score: 28/100
- Dimensionen: test_gap=low, security=low, api_breakage=medium
- Details: runs/<run_id>/risk_report.json

## Offene Punkte
- [ ] Review durch Maintainer
- [ ] Label setzen: needs-review

## Kontext-Refs
- ChangeProposal: <proposal_id>
- Run: <run_id>
```

### Optionaler GitHub Draft PR

Nur wenn `github_integration.enabled=true` in der Projektkonfiguration.
Fällt bei fehlender Integration auf den Markdown-PR-Body zurück — kein Fehler.

---

## Expert-Definition (Auszug)

```yaml
expert_id: pr_author
version: "1.0"
purpose: "Erstellt PR-Entwurf aus genehmigtem ChangeProposal"
allowed_tools:
  - read_file
  - read_artifact
  - github_create_draft_pr   # nur wenn Integration aktiv
denied_tools:
  - shell_exec
  - apply_diff
  - merge_pull_request
output_contract: diff_proposal
approval_gates:
  - create_pull_request
```

---

## Grenzen

- Darf nur ChangeProposals mit `status=approved` verarbeiten. Nicht-genehmigte Proposals
  werden abgelehnt; Grund wird im Lauf-Artefakt protokolliert.
- PR-Erstellung auf GitHub ist ein eigenes Approval-Gate (`create_pull_request`).
- Kein automatischer Merge — nicht in Default-Policy, nicht per Expert-Override erlaubt.
- GitHub-Integration ist additiv: Fehlt sie, wird Markdown-Artefakt erzeugt.

---

## Fallback-Verhalten

```
GitHub-Integration aktiv?
  Ja ──► GitHub Draft PR erzeugen (approval_gate: create_pull_request)
  Nein ──► Markdown-PR-Entwurf als Artefakt speichern
           Pfad: runs/<run_id>/pr_draft.md
```

---

## Tests

| Testfall                                        | Erwartung                                         |
|-------------------------------------------------|---------------------------------------------------|
| Gültiger Input, GitHub nicht aktiv              | pr_draft.md erzeugt, kein GitHub-Call             |
| ChangeProposal mit status=pending               | Ablehnung, Fehlerartefakt, kein PR-Body           |
| Fehlender TestReport                            | PR-Body mit Warnung "Keine Testergebnisse"        |
| GitHub-Integration aktiv, Approval erteilt      | GitHub Draft PR erstellt, PR-URL im Artefakt      |
| PR-Body enthält alle Pflichtabschnitte          | Ziel, Dateien, Tests, Risiken, Offene Punkte      |
