# Standard Blueprints

Die folgende Liste ist der offizielle Standard-Blueprint-Katalog fuer den produktnahen Einstieg.

| Blueprint | Intended use | Safety/review stance | Expected outputs (examples) |
| --- | --- | --- | --- |
| Scrum | Iterative, cross-funktionale Feature-Lieferung mit klarer Rollenverantwortung. | balanced security, standard verification | Scrum Backlog, Sprint Planning, Sprint Review |
| Kanban | Kontinuierliche Flow-Steuerung mit WIP-orientierter Priorisierung. | balanced security, standard verification | Intake Board, WIP Policy Check, Flow Review |
| Research | Evidenzbasierte Analyse mit Quellenvalidierung und Synthese. | balanced security, verification required | Research Intake, Source Matrix, Findings Summary |
| Code-Repair | Zielgerichtete Incident-Triage, Fix-Umsetzung und Regression-Absicherung. | balanced security, verification required | Incident Triage, Patch Plan, Regression Check |
| Security-Review | Sicherheits- und Compliance-Review mit klarer Risikobewertung. | strict security, verification required | Scope & Threat Review, Control Validation, Remediation Advice |
| Release-Prep | Release-Readiness inklusive Preflight, Go/No-Go und Rollback-Planung. | strict security, verification required | Release Checklist, Verification Sweep, Rollback Readiness |
| Scrum-OpenCode | Scrum mit expliziter OpenCode/SGPT/Terminal-Ausfuehrungskaskade. | balanced security, verification required | OpenCode Backlog Alignment, Execution Cascade Agreement, Increment Validation |
| Research-Evolution | DeerFlow-Research plus Evolver-Proposal mit verpflichtendem Review-Gate. | strict security, verification required, human review gate | DeerFlow Research Stage, Evolver Proposal Stage, Review Gate |

Hinweis: Die katalogisierte Produktsicht ist im Read-Model verfuegbar ueber `GET /teams/blueprints/catalog`.
Der Katalog liefert zusaetzlich pro Blueprint eine kompakte `work_profile_summary` mit:

- `recommended_goal_modes`
- `playbook_hints`
- `capability_hints`
- `governance_profile` (`label`, `hint`)
