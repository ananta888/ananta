# Standard Blueprints

Die folgende Liste ist der offizielle Standard-Blueprint-Katalog fuer den produktnahen Einstieg.

## Enterprise organization Team Blueprints

Der Multi-Team-Katalog fuegt sieben wiederverwendbare Team Blueprints hinzu.
Sie werden von Organization Blueprints per stabilem Key und Version
referenziert und ersetzen die bestehenden Einzelteam-Blueprints nicht.

| Team Blueprint | Primaere Verantwortung |
| --- | --- |
| Enterprise Product Delivery Scrum | Product Owner, Scrum Master und spezialisierte Developers liefern ein verifiziertes Inkrement. |
| Portfolio Product Coordination | Gemeinsames Product Goal, Portfolio Backlog, Value-Stream-Zuordnung und Cross-Team-Eskalation. |
| Research and Discovery | Grounded Research, Quellenmatrix, Synthese und Category-Todo; keine Delivery-Entscheidung. |
| Proof of Concept | Zeit-/Kosten-beschraenkter Prototyp und Messdaten mit explizitem Exit-Gate. |
| Platform DevOps SRE | Plattform, CI/CD, Observability, Reliability und Hub-kontrolliertes Deployment. |
| Architecture Governance | Architekturentscheidungen, Constraints und unabhaengige Review-Gates. |
| Quality Security Release | Unabhaengige Quality-, Security-, Accessibility-, Compliance- und Release-Verifikation. |

Standardorganisationen umfassen fuenf bis zehn Teaminstanzen; acht ist der
Default und die einzige vollstaendige Referenzabnahme. Bei acht Teams werden
zwei Delivery-Instanzen und je eine Instanz der sechs anderen Typen erzeugt.
Die Teamzahl ist Cardinality einer wiederholbaren Gruppe, kein separates
Preset. Zwei-/Drei-Team-Varianten gehoeren ausschliesslich zum injizierten
Testkatalog und werden nicht produktiv geseedet.

Die Enterprise-Acht-Team-Referenz erzeugt 82 Rollenplaetze und 73 geplante
Standardbesetzungen. Ein Rollenplatz ist eine Verantwortung und moegliche
Zuweisung, nicht automatisch eine eigene Person oder ein eigener Agent.

## Lean Company Organization Blueprint

`lean_company_organization@1` ist eine additive Produktionsfamilie fuer kleine
Firmen. Sie laeuft durch denselben Hub-Compiler und denselben Grant-, Policy-
und Instanziierungspfad wie Enterprise, besitzt aber einen bewusst kompakten
Rollenkatalog.

| Standardgroesse | Teamkomposition |
| --- | --- |
| 5 Rollenplaetze / 2 Teams | Direction + eine Delivery Cell |
| 8 / 3 | zusaetzlich Discovery |
| 12 / 4 | zusaetzlich Enablement |
| 16 / 5 | zweite Delivery Cell |
| 20 / 6 | dritte Delivery Cell |

Direction enthaelt den Founder/Portfolio Product Lead. Eine Delivery Cell
enthaelt vier Rollenplaetze fuer Team-Produktverantwortung, technische
Fuehrung/Engineering, Product Engineering und unabhaengige Quality-/Security-
/Operations-Verifikation. Discovery ergaenzt Research Lead, Source-/
Requirements-Analyse und unabhaengiges Research Review. Enablement ergaenzt
Platform, Reliability, Security und Release Verification.

Alle Lean-Slots haben `default_count=1`. Darum entsprechen die obigen Zahlen
sowohl der Slotzahl als auch der Standardbesetzung. Weniger eindeutige Agents
sind nur durch explizit kompatibles Dual-Hatting innerhalb von Kapazitaet und
Separation of Duties moeglich. Worker routen oder starten niemals andere
Worker; alle Workflow-Uebergaenge laufen ueber den Hub.

Der aktuelle persistente Assignment-Pfad bindet registrierte Agents. Ein
Rollenplatz kann fachlich eine menschliche Verantwortung beschreiben, wird
aber erst dann als Human-Assignment dargestellt, wenn ein eigenes
Accountability-/Identity-Modell dafuer vorhanden ist; die Lean-Seeds behaupten
dies heute nicht vorzeitig.

Bei 16 und 20 Rollen existieren zwei beziehungsweise drei gleichartige
Delivery Cells. Der Lean-Delivery-Workflow verlangt dann beim Hub-Preview und
bei der Ableitung eine konkrete `target_unit_id`. Dadurch entsteht pro Cell
eine eindeutig gebundene Workflow-Instanz; eine mehrdeutige Anfrage waehlt
nicht stillschweigend das erste Team.

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

## Planner-Integration (aktuell)

AutoPlanner nutzt folgende Reihenfolge:

1. `PlanningTemplateCatalog` (deterministische Template-Aufloesung)
2. Blueprint-basierte Template-Aufloesung (Team Blueprint Artifacts/Rollenhinweise)
3. HubCopilot Planning
4. LLM Fallback

Wichtig: Team Blueprints und Planning Templates sind getrennte, aber gekoppelte Quellen.

- **Team Blueprints** liefern Team-Setup, Rollen, Policies und Start-Artefakte.
- **PlanningTemplateCatalog** liefert AutoPlanner-Subtask-Templates.
- **planning_utils.py** bleibt technische Utility-Schicht (Sanitizing, Validation, JSON/Subtask Parsing) und ist nicht mehr die primaere fachliche Template-Quelle.

Fuer neue Domain-Blueprints gilt: Planungsvorlagen in den Katalog/Blueprint-Daten aufnehmen, nicht als harte Python-Dictionaries in Route-Modulen.

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
