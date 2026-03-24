# Goal-Plan-Task-Execution-Verification-Artifact Model

Zweck
----
Dieses Dokument beschreibt das Domain-Modell und die Verantwortungsgrenzen für Goals, Plans, Tasks, Execution Workspaces, Verification Records und Artifacts. Der Hub bleibt Steuerungsebene (Control Plane); Workers führen Aufgaben aus (Execution Agents).

Kernkonzepte
-----------
- Goal: Nutzeranfrage mit minimalen Pflichtfeldern (z. B. Zieltext). Kann erweitert werden (Constraints, Akzeptanzkriterien).
- Plan: Vom Hub erzeugte strukturierte Folge von Tasks zur Erreichung eines Goals. Plans können persistiert oder temporär sein (Feature-Flag gesteuert).
- Task: Einzelne Operationseinheit, hat Input/Output, kann Delegation an Worker erfordern.
- Execution Workspace: Isolierter Ausführungsrahmen pro Task, sorgt für Cleanup und Ressourcentrennung.
- Verification Record: Beweisdaten, Audit-Informationen und Signaturen, die entscheiden, ob ein Artifact als vertrauenswürdig gilt.
- Artifact: Ergebnis einer Task/Verifikation, entweder kurzgefasste Nutzeransicht oder inspectierbarer Speicher mit Trace-IDs.

Verantwortungsgrenzen
---------------------
- Hub (Control Plane): Planung, Delegation, Governance, Auditing, Routing-Entscheidungen.
- Worker (Execution Plane): Ausführung, lokale Artefakt-Produktion, Umfeld-Isolation.
- Kein direkter Worker-zu-Worker-Orchestrierungsweg; alle Delegationen laufen über den Hub.

Observability & Audit
---------------------
- Jede Plan- und Task-Transition erzeugt Trace-IDs, Audit-Events und optional tamper-evidence Metadaten.
- Standardansicht liefert Artefakt-Zusammenfassungen; vollständige Spuren sind Zugriffs-gesteuert.

Migration & Feature Flags
-------------------------
- Persistente Plans sind optional und durch Feature-Flag konfigurierbar (siehe config.json -> feature_flags.goal_workflow_enabled bzw. persisted_plans_enabled).

Weiteres
-------
Dieses Dokument ist eine Ausgangsbasis; Sequenzdiagramme und konkrete API-Spezifikationen werden in separaten Abschnitten ergänzt.