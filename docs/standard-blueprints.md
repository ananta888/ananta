# Standard Blueprints

Die folgende Liste ist der offizielle Standard-Blueprint-Katalog fuer den produktnahen Einstieg.

| Blueprint | Intended use | Safety/review stance | Default outputs (initial) |
| --- | --- | --- | --- |
| Scrum | Iterative, cross-funktionale Feature-Lieferung mit klarer Rollenverantwortung. | balanced security, standard verification | Initial Backlog, Sprint-Plan, Definition-of-Done-Check |
| Kanban | Kontinuierliche Flow-Steuerung mit WIP-orientierter Priorisierung. | balanced security, standard verification | Intake-Board, WIP-Policy-Check, Flow-Review-Plan |
| Research | Evidenzbasierte Analyse mit Quellenvalidierung und Synthese. | balanced security, verification required | Research-Brief, Source-Matrix, Findings-Summary |
| Code-Repair | Zielgerichtete Incident-Triage, Fix-Umsetzung und Regression-Absicherung. | balanced security, verification required | Incident-Triage, Patch-Plan, Regression-Checklist |
| TDD | Test-Driven Development mit explizitem Red -> Green -> Refactor Nachweisfluss fuer kleine Features und Bugfixes. | balanced security, verification required, human review gate | TestPlanArtifact, RedTestResultArtifact, PatchPlanArtifact, GreenTestResultArtifact, RefactorChecklist |
| Security-Review | Sicherheits- und Compliance-Review mit klarer Risikobewertung. | strict security, verification required | Scope-Threat-Review, Control-Validation, Remediation-Plan |
| Release-Prep | Release-Readiness inklusive Preflight, Go/No-Go und Rollback-Planung. | strict security, verification required | Release-Checklist, Verification-Sweep, Rollback-Readiness-Plan |
| Scrum-OpenCode | Scrum mit expliziter OpenCode/SGPT/Terminal-Ausfuehrungskaskade. | balanced security, verification required | Execution-Backlog, Cascade-Agreement, Increment-Validation |
| Research-Evolution | DeerFlow-Research plus Evolver-Proposal mit verpflichtendem Review-Gate. | strict security, verification required, human review gate | Research-Stage-Brief, Evolver-Proposal, Review-Gate-Checklist |

Hinweis: Die katalogisierte Produktsicht ist im Read-Model verfuegbar ueber `GET /teams/blueprints/catalog`.
Der Katalog liefert zusaetzlich pro Blueprint eine kompakte `work_profile_summary` mit:

- `recommended_goal_modes`
- `playbook_hints`
- `capability_hints`
- `governance_profile` (`label`, `hint`)

## Beispiel-Inputs und erwartete Ergebnis-Skizzen

Die folgenden Beispiele sind kurz genug fuer Demo und Erststart. Sie zeigen, was Nutzer vor dem Start eingeben und welche Resultatform danach erwartet wird.

| Blueprint | Beispiel-Input | Erwartete Ergebnis-Skizze |
| --- | --- | --- |
| Scrum | "Plane die naechsten zwei Wochen fuer Feature X mit klaren Rollen und Reviewpunkten." | Priorisierte Story-Liste, Sprint-Ziel, Review- und Abnahmeplan. |
| Kanban | "Organisiere ungeplante Anfragen fuer Team Y mit WIP-Limit 3." | Intake-Spaltenstruktur, WIP-Regeln, taeglicher Flow-Review-Ablauf. |
| Research | "Analysiere Optionen fuer Architekturentscheidung Z mit belastbaren Quellen." | Frageliste, Quellenmatrix mit Bewertung, zusammenfassende Empfehlung mit Risiken. |
| Code-Repair | "Behebe den Login-Fehler nach dem letzten Release sicher und testbar." | Triage-Protokoll, geplanter Fix, konkrete Regressionschecks mit Ergebnisstatus. |
| TDD | "Ergaenze Passwort-Validierung testgetrieben fuer den Login." | Verhalten beschrieben, Red-Test-Nachweis, minimaler Patch, Green-Test-Nachweis, optionaler Refactor-Check. |
| TDD | "Behebe Null-Check-Bug in der Profil-API mit TDD." | Bug als erwartetes Fehlverhalten fixiert, Red/Green-Evidenz vorhanden, Patch-Apply bleibt approval-gated. |
| Security-Review | "Pruefe den neuen API-Endpunkt auf Auth-, Input- und Logging-Risiken." | Risiko-Liste nach Schweregrad, Kontrollabgleich, priorisierte Remediation-Schritte. |
| Release-Prep | "Bereite Release 1.9.0 fuer produktionsnahen Rollout vor." | Vollstaendige Go/No-Go-Checkliste, Verifikationsnachweise, klarer Rollback-Plan. |
| Scrum-OpenCode | "Fuehre Sprint-Aufgaben fuer Bugfix + Refactor mit OpenCode-Ausfuehrung aus." | Backlog auf Ausfuehrungskaskade gemappt, abgestimmte Rollenabfolge, validierter Inkrement-Output. |
| Research-Evolution | "Untersuche Wachstumspfad fuer Modul A und schlage Evolver-Update vor." | DeerFlow-Erkenntnisse, Evolver-Proposal mit Begruendung, dokumentiertes Review-Gate-Urteil. |

## Sichtbarkeit nach der Instanziierung

Nach `POST /teams/blueprints/<id>/instantiate` sollen die oben definierten Default Outputs fuer Nutzer sichtbar sein:

1. in der Blueprint-Zusammenfassung vor dem Start,
2. in der Team-Startansicht direkt nach Instanziierung,
3. in den ersten Team-Tasks als erwartete Ergebnisartefakte.
