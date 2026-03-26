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

## Sequenzdiagramme (ARCH-GOAL-812)

Die Sequenzen sind separat versioniert:

- Goal Ingestion und Planning: `architektur/uml/goal-lifecycle-sequence.mmd`
- Delegation, Verification und Artifact Publishing: `architektur/uml/goal-delegation-verification-sequence.mmd`

Beide Diagramme zeigen explizit den Hub als alleinige Orchestrierungsebene und vermeiden direkte Worker-zu-Worker-Flüsse.

## Execution Isolation & Container Boundaries (ARCH-GOAL-813)

- Jede Task-Ausführung läuft in einem eigenen, nachvollziehbaren Execution Scope (`execution_scope_id`).
- Workspace-Leases werden durch den Hub verwaltet (Anlage, Verlängerung, Cleanup-Zustand).
- Hub- und Worker-Runtimes bleiben container-separiert; es gibt keinen impliziten Shared-State.
- Retry-Läufe erzeugen neue Scope-Instanzen statt Re-Use bereits kontaminierter Workspaces.
- Cleanup-Events werden als Audit-/Trace-Signale persistiert, damit Isolation überprüfbar bleibt.

## Context Separation (ARCH-GOAL-814)

Es gelten drei klar getrennte Kontext-Typen:

1. **Task Working Context**: kurzlebig, task-spezifisch, nicht dauerhaft als Wissensquelle gedacht.
2. **Goal Context**: nutzerbezogene Ziele, Constraints und Akzeptanzkriterien über den gesamten Goal-Lebenszyklus.
3. **Project Knowledge Sources**: langlebige Wissensquellen (z. B. RAG/Repos), versioniert und wiederverwendbar.

Der Hub referenziert diese Ebenen explizit, damit Operatoren erkennen können, welche Entscheidungen auf flüchtigem Kontext und welche auf durablem Projektwissen basieren.

## Observability Model (ARCH-GOAL-815)

- Einheitliche `trace_id`-Verkettung über Goal → Plan → Task → Verification → Artifact.
- Audit-Events für Routing, Policy-Entscheidungen, Delegation, Fallback und Verifikation.
- Explainability-Metadaten an Policy-Entscheidungen (erlaubt/abgelehnt + Begründungscode).
- Artifact-Ansichten bleiben „summary-first“, mit kontrolliertem Drill-down auf technische Evidenz.
- Governance-Review kann jeden Artifact-Eintrag zugehörigen Plan-Knoten und Verifikationsnachweisen zuordnen.

## Default-First UX mit Advanced Step Disclosure (ARCH-GOAL-816)

- **Default-First**: Für den Start reicht ein einzelnes Goal-Feld; sichere Systemdefaults greifen automatisch.
- **Advanced Disclosure**: Zusätzliche Felder (Constraints, Policies, Routing-Präferenzen) sind optional und explizit.
- **Reversibilität**: Erweiterte Eingriffe bleiben zurücknehmbar, ohne den Basisflow zu brechen.
- **Governance-Sichtbarkeit**: Auch im einfachen Modus sind wesentliche Policy-/Verification-Indikatoren sichtbar.
- **Traceability by default**: Jede Nutzeransicht kann auf Wunsch zu Plan, Task, Trace und Artifact heruntergebrochen werden.

Observability & Audit
---------------------
- Jede Plan- und Task-Transition erzeugt Trace-IDs, Audit-Events und optional tamper-evidence Metadaten.
- Standardansicht liefert Artefakt-Zusammenfassungen; vollständige Spuren sind Zugriffs-gesteuert.

Migration & Feature Flags
-------------------------
- Persistente Plans sind optional und durch Feature-Flag konfigurierbar (siehe `config.json` -> `feature_flags.goal_workflow_enabled` bzw. `persisted_plans_enabled`).

Weiteres
-------
Dieses Dokument dient als Architektur-Referenz für Goal-basierte Flows. Detaillierte API-Beispiele liegen in den operativen Backend- und Operator-Guides.
